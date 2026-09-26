#!/usr/bin/env python3
"""Single-use NDJSON controller for an owned PTY; never starts on import/ready.

Only explicit input messages carry user keystrokes. Protocol messages accept
one narrowly validated reply to a corresponding outstanding terminal query.
"""

import argparse
import base64
import binascii
import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

MAX_INPUT = 16 * 1024
MAX_MESSAGE = 64 * 1024
TERM_WAIT = 1.0
KILL_WAIT = 1.0
OUTPUT_WAIT = 2.0


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("non-finite JSON number")


class TerminalQueries:
    """Recognize actual VT queries, not query-looking text inside OSC/DCS strings."""
    QUERIES = {
        b"[6n": "cursor", b"[?6n": "private-cursor",
        b"[5n": "status", b"[?5n": "private-status",
        b"[c": "device", b"[0c": "device",
        b"[>c": "secondary-device", b"[>0c": "secondary-device",
        b"[?2026$p": "sync-mode", b"[?2004$p": "paste-mode",
        # xterm sends the current focus once when reporting is enabled.
        b"[?1004h": "focus",
        b"]10;?": "foreground", b"]11;?": "background",
    }

    def __init__(self):
        self.state = "ground"
        self.sequence = bytearray()
        self.pending = []

    def complete(self):
        kind = self.QUERIES.get(bytes(self.sequence))
        palette = re.fullmatch(rb"\]4;([0-9]{1,3});\?", self.sequence)
        if palette and int(palette[1]) <= 255:
            kind = "palette-%d" % int(palette[1])
        if kind:
            now = time.monotonic()
            self.pending = [(k, expires) for k, expires in self.pending if expires > now]
            if len(self.pending) >= 32:
                raise ValueError("too many unanswered terminal queries")
            self.pending.append((kind, now + 5))
        self.state = "ground"
        self.sequence.clear()

    def feed(self, data):
        for byte in data:
            if self.state.startswith("string"):
                if self.state == "string-escape" and byte == 92:
                    self.complete()
                elif byte == 7 and self.sequence[:1] == b"]":
                    self.complete()
                elif byte == 27:
                    if self.state == "string-escape" and len(self.sequence) < 128:
                        self.sequence.append(27)
                    self.state = "string-escape"
                else:
                    if self.state == "string-escape" and len(self.sequence) < 128:
                        # Preserve the string prefix: embedded CSI-looking bytes
                        # must not become a standalone query at the closing ST.
                        self.sequence.append(27)
                    self.state = "string"
                    if len(self.sequence) < 128:
                        self.sequence.append(byte)
                continue
            if byte in (24, 26):  # CAN/SUB cancel a control sequence.
                self.state = "ground"
                self.sequence.clear()
            elif byte == 27:
                self.state = "escape"
                self.sequence.clear()
            elif self.state == "escape":
                self.sequence.append(byte)
                self.state = "csi" if byte == 91 else "string" if byte in b"]PX^_" else "ground"
            elif self.state == "csi":
                self.sequence.append(byte)
                if 64 <= byte <= 126:
                    self.complete()
                elif len(self.sequence) > 128 or not 32 <= byte <= 63:
                    self.state = "ground"
                    self.sequence.clear()

    def consume(self, data, cols, rows):
        now = time.monotonic()
        self.pending = [(kind, expires) for kind, expires in self.pending if expires > now]
        kind = None
        cursor = re.fullmatch(rb"\x1b\[(\??)([1-9][0-9]{0,4});([1-9][0-9]{0,4})R", data)
        if cursor and int(cursor[2]) <= rows and int(cursor[3]) <= cols:
            kind = "private-cursor" if cursor[1] else "cursor"
        elif data in (b"\x1b[0n", b"\x1b[?0n"):
            kind = "private-status" if b"?" in data else "status"
        elif re.fullmatch(rb"\x1b\[\?[0-9]{1,5}(?:;[0-9]{1,5}){0,8}c", data):
            kind = "device"
        elif re.fullmatch(rb"\x1b\[>[0-9]{1,5}(?:;[0-9]{1,5}){2}c", data):
            kind = "secondary-device"
        elif re.fullmatch(rb"\x1b\[\?2026;[0-4]\$y", data):
            kind = "sync-mode"
        elif re.fullmatch(rb"\x1b\[\?2004;[0-4]\$y", data):
            kind = "paste-mode"
        elif data in (b"\x1b[I", b"\x1b[O"):
            kind = "focus"
        else:
            color = re.fullmatch(rb"\x1b\](10|11|4;(0|[1-9][0-9]{0,2}));rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)", data)
            if color:
                if color[1] in (b"10", b"11"):
                    kind = "foreground" if color[1] == b"10" else "background"
                elif int(color[2]) <= 255:
                    kind = "palette-%d" % int(color[2])
        for index, (expected, _) in enumerate(self.pending):
            if kind == expected:
                del self.pending[index]  # Consume before write, including partial failure.
                return kind
        return None


class Bridge:
    def __init__(self, workspace, take_dir, fixture=False):
        self.workspace, self.take_dir = Path(workspace), Path(take_dir)
        if not self.workspace.is_absolute() or not self.workspace.is_dir():
            raise ValueError("workspace must be an existing absolute directory")
        if (not self.take_dir.is_absolute() or self.take_dir.is_symlink()
                or not self.take_dir.is_dir()):
            raise ValueError("take-dir must be an existing nonsymlink absolute directory")
        for name in ("COPILOT_ALLOW_ALL", "COPILOT_ASSISTED_APPROVAL"):
            if os.environ.get(name, "").lower() not in ("", "0", "false"):
                raise ValueError("refusing active " + name)
        self.fixture = fixture
        self.pid = self.master = self.status = None
        self.master_eof = False
        self.started = self.ended = self.stop_requested = False
        self.seq = 0
        self.stdout_fd = None
        self.stdout_open = True
        self.cleanup_only = False
        self.ids = set()
        self.queries = TerminalQueries()
        self.cols = self.rows = 0
        self.raw = self.events = None
        directory = os.open(self.take_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # A take is single-use. Never truncate prior evidence or follow log links.
            for name in ("terminal.ansi", "pty-events.jsonl"):
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                stream = os.fdopen(fd, "wb")
                if name == "terminal.ansi":
                    self.raw = stream
                else:
                    self.events = stream
            if not fixture:
                if os.path.lexists(self.take_dir / "usage.json"):
                    raise ValueError("usage.json already exists; use a new take")
                os.mkdir("logs", mode=0o700, dir_fd=directory)
        except BaseException:
            self.close_files()
            raise
        finally:
            os.close(directory)

    def command(self):
        if self.fixture:
            fixture = Path(__file__).resolve().parent.parent / "test/fixtures/terminal-fixture.py"
            return [sys.executable, "-u", str(fixture)]
        return ["/opt/homebrew/bin/copilot", "--mode", "plan", "--no-auto-update",
                "--no-remote", "--no-remote-export", "--disable-builtin-mcps",
                "--log-dir", str(self.take_dir / "logs"), "--log-level", "info",
                "--usage-output-file", str(self.take_dir / "usage.json")]

    def record(self, event):
        self.events.write((json.dumps({"timestamp": timestamp(), **event},
                                      ensure_ascii=True) + "\n").encode("ascii"))
        self.events.flush()

    def emit(self, event):
        self.record(event)
        if not self.stdout_open:
            return
        encoded = json.dumps(event, ensure_ascii=True) + "\n"
        if self.stdout_fd is None:  # Also supports in-process tests with redirected stdout.
            print(encoded, end="", flush=True)
            return
        data = encoded.encode("ascii")
        deadline = time.monotonic() + OUTPUT_WAIT
        try:
            while data:
                if time.monotonic() >= deadline:
                    raise TimeoutError("controller stdout backpressure timed out")
                try:
                    count = os.write(self.stdout_fd, data)
                    if not count:
                        raise BrokenPipeError("controller stdout closed")
                    data = data[count:]
                except BlockingIOError:
                    select.select([], [self.stdout_fd], [], 0.02)
        except OSError:
            self.stdout_open = False
            raise

    def reject(self, message, reason):
        op = message.get("op") if isinstance(message, dict) else None
        if op == "input":
            self.emit({"type": "input", "id": message.get("id"),
                       "status": "rejected", "reason": reason})
        elif op == "protocol":
            self.emit({"type": "protocol", "status": "rejected", "reason": reason})
        else:
            self.emit({"type": "rejected", "op": op, "reason": reason})

    def start(self, cols, rows):
        self.started = True  # No retry, including after a failed fork/exec.
        self.cols, self.rows = cols, rows
        argv = self.command()
        error_read, error_write = os.pipe()  # CLOEXEC: EOF acknowledges a successful exec.
        try:
            self.pid, self.master = pty.fork()
        except BaseException:
            os.close(error_read)
            os.close(error_write)
            raise
        if self.pid == 0:
            try:
                os.close(error_read)
                for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    signal.signal(sig, signal.SIG_DFL)
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                os.chdir(self.workspace)
                env = dict(os.environ, TERM="xterm-256color")
                os.execve(argv[0], argv, env)
            except BaseException as exc:
                # Never print child startup errors to the controller's JSON stdout.
                os.write(error_write, str(exc).encode("utf-8", "replace")[:2048])
            finally:
                os._exit(127)
        os.close(error_write)
        os.set_blocking(self.master, False)
        try:
            readable, _, _ = select.select([error_read], [], [], 5)
            if not readable:
                raise RuntimeError("child startup timed out")
            failure = os.read(error_read, 4096)
            if failure:
                raise RuntimeError("child startup failed: " + failure.decode("utf-8", "replace"))
        finally:
            os.close(error_read)
        self.emit({"type": "started", "pid": self.pid, "cols": cols, "rows": rows})

    def drain(self):
        """Return True only at EOF/EAGAIN; bound work so a noisy child cannot starve stop."""
        if self.master is None or self.master_eof:
            return True
        for _ in range(64):
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                return True
            except OSError as exc:
                if exc.errno != errno.EIO:  # Linux's PTY EOF; macOS generally returns b''.
                    raise
                data = b""
            if not data:
                self.master_eof = True
                return True
            self.seq += 1
            try:
                self.raw.write(data)
                self.raw.flush()
                self.queries.feed(data)
                self.emit({"type": "output", "seq": self.seq,
                           "data": base64.b64encode(data).decode("ascii")})
            except Exception:
                if not self.cleanup_only:
                    raise  # A logging/transport failure must still terminate and reap.
        return False

    def poll_child(self):
        if self.pid is not None and self.status is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = status

    def write_input(self, data):
        written = 0
        deadline = time.monotonic() + 2.0
        try:
            while written < len(data):
                if self.stop_requested or time.monotonic() >= deadline:
                    raise TimeoutError("PTY input interrupted or timed out")
                try:
                    count = os.write(self.master, data[written:])
                    if not count:
                        raise OSError("zero-length PTY write")
                    written += count
                except BlockingIOError:
                    self.drain()
                    select.select([], [self.master], [], 0.02)
        except Exception as exc:
            # Never replay an input, even when the child received only a prefix.
            raise RuntimeError("PTY input failed after %d bytes" % written) from exc

    def handle(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("op"), str):
            self.reject(message, "invalid-message")
            return
        op = message["op"]
        fields = {"start": {"op", "cols", "rows"},
                  "input": {"op", "id", "expectedSeq", "data"},
                  "protocol": {"op", "data"}, "stop": {"op"}}
        if op not in fields:
            self.reject(message, "unknown-op")
            return
        if set(message) != fields[op]:
            self.reject(message, "invalid-fields")
            return
        if op == "stop":
            self.finish(stopped=True)
        elif op == "start":
            if self.started:
                self.reject(message, "already-started")
            elif any(type(message[k]) is not int or not 1 <= message[k] <= 65535
                     for k in ("cols", "rows")):
                self.reject(message, "invalid-size")
            else:
                self.start(message["cols"], message["rows"])
        elif op == "protocol":
            encoded = message["data"]
            if not isinstance(encoded, str) or len(encoded) > 256:
                self.reject(message, "invalid-protocol-data")
                return
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                self.reject(message, "invalid-protocol-data")
                return
            self.poll_child()
            if self.pid is None or self.status is not None or self.master_eof:
                self.reject(message, "not-running")
                return
            kind = self.queries.consume(data, self.cols, self.rows)
            if kind is None:
                self.reject(message, "unmatched-protocol-reply")
                return
            self.record({"type": "protocol-attempt", "query": kind, "data": encoded})
            self.write_input(data)
            self.emit({"type": "protocol", "status": "sent", "query": kind})
        else:
            input_id = message["id"]
            if not isinstance(input_id, str) or not 1 <= len(input_id) <= 256:
                self.reject(message, "invalid-id")
                return
            if input_id in self.ids:
                self.reject(message, "duplicate-id")
                return
            self.ids.add(input_id)  # Consume IDs for rejected attempts too; never retry.
            if type(message["expectedSeq"]) is not int or message["expectedSeq"] < 0:
                self.reject(message, "invalid-seq")
                return
            encoded = message["data"]
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_INPUT + 2) // 3):
                self.reject(message, "invalid-data")
                return
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                self.reject(message, "invalid-base64")
                return
            if len(data) > MAX_INPUT:
                self.reject(message, "input-too-large")
                return
            if self.pid is None:
                self.reject(message, "not-running")
                return
            quiet = self.drain()  # Includes bytes already queued in the kernel PTY.
            self.poll_child()
            if self.status is not None or self.master_eof:
                self.reject(message, "not-running")
            elif not quiet:
                self.reject(message, "output-busy")
            elif message["expectedSeq"] != self.seq:
                self.reject(message, "stale-seq")
            else:
                self.record({"type": "input-attempt", "id": input_id,
                             "expectedSeq": message["expectedSeq"], "data": encoded})
                self.write_input(data)
                self.emit({"type": "input", "id": input_id, "status": "sent"})

    def signal_group(self, sig):
        if self.pid is not None:
            try:
                os.killpg(self.pid, sig)
            except ProcessLookupError:
                pass

    def terminate(self):
        if self.pid is None:
            return
        # pty.fork creates a session/process-group leader. Never signal a name,
        # the parent's process group, or any process not in this owned group.
        for sig, duration in ((signal.SIGTERM, TERM_WAIT), (signal.SIGKILL, KILL_WAIT)):
            self.signal_group(sig)
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                self.drain()
                self.poll_child()
                if self.status is not None and self.master_eof:
                    # A descendant could have closed its PTY but kept running.
                    self.signal_group(signal.SIGKILL)
                    return
                time.sleep(0.01)
        self.drain()
        self.poll_child()
        if self.status is None or not self.master_eof:
            raise RuntimeError("owned PTY did not close/reap within the shutdown deadline")

    def finish(self, stopped):
        self.terminate()
        exit_code = sig = None
        if self.status is not None:
            if os.WIFEXITED(self.status):
                exit_code = os.WEXITSTATUS(self.status)
            elif os.WIFSIGNALED(self.status):
                sig = os.WTERMSIG(self.status)
        self.emit({"type": "exited", "exitCode": exit_code, "signal": sig,
                   "seq": self.seq, "stopped": stopped})
        self.ended = True

    def run(self):
        self.stdout_fd = 1
        os.set_blocking(self.stdout_fd, False)
        self.emit({"type": "ready"})
        buffer = bytearray()
        dropping = False
        while not self.ended:
            if self.stop_requested:
                self.finish(stopped=True)
                break
            self.drain()
            self.poll_child()
            if self.status is not None:
                self.finish(stopped=False)
                break
            fds = [0] + ([self.master] if self.master is not None and not self.master_eof else [])
            readable, _, _ = select.select(fds, [], [], 0.05)
            if 0 not in readable:
                continue
            data = os.read(0, 8192)
            if not data:
                self.finish(stopped=True)
                break
            # A bounded line parser recovers after oversized/malformed messages.
            chunks = data.split(b"\n")
            for index, chunk in enumerate(chunks):
                complete = index < len(chunks) - 1
                if not dropping:
                    buffer.extend(chunk)
                    if len(buffer) > MAX_MESSAGE:
                        self.reject(None, "message-too-large")
                        buffer.clear()
                        dropping = True
                if complete:
                    if not dropping:
                        try:
                            message = json.loads(buffer, object_pairs_hook=strict_object,
                                                 parse_constant=invalid_constant)
                        except (ValueError, UnicodeError, RecursionError):
                            self.reject(None, "invalid-json")
                        else:
                            self.handle(message)
                    buffer.clear()
                    dropping = False
                if self.ended:
                    break
        return 0

    def close_files(self):
        for stream in (self.raw, self.events):
            if stream is not None:
                stream.close()

    def close(self):
        self.cleanup_only = True
        try:
            if not self.ended:
                self.terminate()
        finally:
            if self.master is not None:
                os.close(self.master)
                self.master = None
            self.close_files()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--take-dir", required=True)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    bridge = None
    result = 1
    try:
        bridge = Bridge(args.workspace, args.take_dir, args.fixture)
        def request_stop(signum, frame):
            bridge.stop_requested = True
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, request_stop)
        result = bridge.run()
    except Exception as exc:
        event = {"type": "error", "message": str(exc)}
        try:
            if bridge is None:
                print(json.dumps(event), flush=True)
            else:
                bridge.emit(event)
        except (OSError, ValueError):
            pass  # Controller may already be gone; cleanup must still run.
    finally:
        if bridge is not None:
            try:
                bridge.close()
            except Exception as exc:
                print("PTY cleanup failed: " + str(exc), file=sys.stderr, flush=True)
                result = 1
    return result


if __name__ == "__main__":
    sys.exit(main())
