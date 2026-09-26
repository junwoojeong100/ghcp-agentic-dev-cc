"""Fixture-only tests: never launch Copilot or contact a model/network."""

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "scripts/copilot-pty-bridge.py"
PROMPT = b"FIXTURE input: [a] accept [q] quit"
spec = importlib.util.spec_from_file_location("copilot_pty_bridge", BRIDGE_PATH)
bridge_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_module)


def b64(data):
    return base64.b64encode(data).decode("ascii")


class Controller:
    def __init__(self, take, workspace=ROOT, env=None):
        child_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if env:
            child_env.update(env)
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-u", str(BRIDGE_PATH), "--workspace", str(workspace),
             "--take-dir", str(take), "--fixture"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env)
        self.pending = b""
        self.events = []
        self.output = bytearray()
        self.seq = 0
        self.pid = None

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode("ascii") + b"\n")
        self.proc.stdin.flush()

    def receive(self, timeout=4):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise AssertionError("bridge event timed out")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError("bridge stdout closed before expected event")
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        event = json.loads(line)
        self.events.append(event)
        if event["type"] == "output":
            if event["seq"] != self.seq + 1:
                raise AssertionError("nonmonotonic output seq")
            self.seq = event["seq"]
            self.output.extend(base64.b64decode(event["data"], validate=True))
        if event["type"] == "started":
            self.pid = event["pid"]
        return event

    def until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.receive(max(0.001, deadline - time.monotonic()))
            if predicate(event):
                return event
        raise AssertionError("expected bridge event was not received")

    def text(self, value, timeout=4):
        if value not in self.output:
            self.until(lambda _: value in self.output, timeout)

    def start(self, cols=110, rows=30):
        self.send({"op": "start", "cols": cols, "rows": rows})
        started = self.until(lambda e: e["type"] == "started")
        self.text(PROMPT)
        return started

    def input(self, input_id, data, seq=None, **extra):
        self.send({"op": "input", "id": input_id,
                   "expectedSeq": self.seq if seq is None else seq, "data": b64(data), **extra})
        return self.until(lambda e: e["type"] == "input" and e["id"] == input_id)

    def exited(self):
        event = self.until(lambda e: e["type"] == "exited")
        if event["seq"] != self.seq:
            raise AssertionError("exited emitted before final output")
        if self.proc.wait(timeout=4) != 0:
            raise AssertionError("bridge failed: " + self.proc.stderr.read().decode())
        if self.pending or self.proc.stdout.read():
            raise AssertionError("output emitted after exited")
        return event

    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        try:
            self.proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # Last-resort test cleanup, confined to the fixture group we spawned.
            if self.pid:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.proc.kill()
            self.proc.wait(timeout=2)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pty-bridge-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.number = 0

    def take(self):
        self.number += 1
        path = self.directory / ("take-%d" % self.number)
        path.mkdir()
        return path

    def controller(self, **kwargs):
        take = kwargs.pop("take", None) or self.take()
        controller = Controller(take, **kwargs)
        self.addCleanup(controller.close)
        return controller, take

    def ready(self):
        controller, take = self.controller()
        self.assertEqual(controller.receive(), {"type": "ready"})
        return controller, take

    def test_no_child_before_explicit_start(self):
        with mock.patch.object(bridge_module.pty, "fork") as fork:
            bridge = bridge_module.Bridge(ROOT, self.take(), fixture=True)
            try:
                with redirect_stdout(io.StringIO()):
                    bridge.handle({"op": "input", "id": "early", "expectedSeq": 0, "data": b64(b"a")})
                self.assertIsNone(bridge.pid)
                fork.assert_not_called()
            finally:
                bridge.close()
        controller, take = self.ready()
        self.assertEqual((take / "terminal.ansi").read_bytes(), b"")
        reply = controller.input("before-start", b"a")
        self.assertEqual(reply["reason"], "not-running")
        self.assertIsNone(controller.pid)
        controller.send({"op": "stop"})
        self.assertTrue(controller.exited()["stopped"])

    def test_dimensions_controlling_pty_and_raw_split_utf8(self):
        controller, _ = self.ready()
        started = controller.start(cols=97, rows=23)
        self.assertEqual((started["cols"], started["rows"]), (97, 23))
        self.assertIn(b"FIXTURE / NOT COPILOT", controller.output)
        self.assertIn(b"cols=97 rows=23 TERM=xterm-256color CTL=True", controller.output)
        self.assertIn("한글 분할 출력".encode(), controller.output)
        self.assertIn(b"\x1b[?1049h\x1b[2J\x1b[H", controller.output)
        self.assertGreater(controller.seq, 1)
        controller.input("quit", b"q")
        self.assertEqual(controller.exited()["exitCode"], 0)

    def test_stale_and_duplicate_inputs_never_write(self):
        controller, _ = self.ready()
        controller.start()
        reply = controller.input("once", b"a")
        self.assertEqual(reply["status"], "sent")
        controller.text(b"ACCEPTED ONCE\r\n" + PROMPT)
        reply = controller.input("once", b"a")
        self.assertEqual(reply["reason"], "duplicate-id")
        old_seq = controller.seq
        controller.input("change", b"b")
        controller.text(b"FIXTURE CHANGED input: [q] quit (accept unavailable)")
        reply = controller.input("stale", b"a", seq=old_seq)
        self.assertEqual(reply["reason"], "stale-seq")
        self.assertEqual(controller.input("stale", b"a")["reason"], "duplicate-id")
        controller.input("quit", b"q")
        controller.exited()
        self.assertEqual(controller.output.count(b"ACCEPTED ONCE"), 1)

    def test_pending_output_is_drained_before_sequence_check(self):
        bridge = bridge_module.Bridge(ROOT, self.take(), fixture=True)
        self.addCleanup(bridge.close)
        bridge.pid = 999999  # Mocked operations only; never send signals to this value.
        self.addCleanup(setattr, bridge, "pid", None)
        def pending_output():
            bridge.seq += 1
            return True
        with redirect_stdout(io.StringIO()) as stdout, \
                mock.patch.object(bridge, "drain", side_effect=pending_output), \
                mock.patch.object(bridge, "poll_child"), \
                mock.patch.object(bridge, "write_input") as write:
            bridge.handle({"op": "input", "id": "pending", "expectedSeq": 0, "data": b64(b"a")})
            self.assertEqual(json.loads(stdout.getvalue())["reason"], "stale-seq")
            write.assert_not_called()

    def test_invalid_fields_base64_sequence_size_and_unknown_ops(self):
        controller, _ = self.ready()
        controller.start()
        invalid = [
            {"op": "start", "cols": 110, "rows": 30},
            {"op": "launch", "command": "never-run"},
            {"op": "stop", "extra": True},
            {"op": "input", "id": "extra", "expectedSeq": controller.seq, "data": b64(b"a"), "extra": 1},
            {"op": "input", "id": "bad64", "expectedSeq": controller.seq, "data": "!@#"},
            {"op": "input", "id": "bad-type", "expectedSeq": controller.seq, "data": 123},
            {"op": "input", "id": "badseq", "expectedSeq": True, "data": b64(b"a")},
            {"op": "input", "id": "huge", "expectedSeq": controller.seq, "data": b64(b"a" * (16384 + 1))},
            {"op": "input", "id": "", "expectedSeq": controller.seq, "data": b64(b"a")},
            ["input", "a"],
        ]
        for message in invalid:
            with self.subTest(message=str(message)[:100]):
                controller.send(message)
                event = controller.receive()
                self.assertIn("rejected", (event.get("type"), event.get("status")))
        self.assertNotIn(b"ACCEPTED ONCE", controller.output)
        controller.input("quit", b"q")
        controller.exited()

    def test_terminal_protocol_is_not_a_user_input_channel(self):
        controller, _ = self.ready()
        controller.start()
        for data in (b"a", b"a\r", b"\r", b"\x1b[1;1R", b"\x1b[?1;2c",
                     b"\x1b[I", b"\x1b[O", b"\x1b[?0u", b"\x1b]11;rgb:ffff/ffff/ffff\x07",
                     b"\x1b]4;0;rgb:ffff/ffff/ffff\x1b\\"):
            controller.send({"op": "protocol", "data": b64(data)})
            event = controller.receive()
            self.assertEqual(event, {"type": "protocol", "status": "rejected", "reason": "unmatched-protocol-reply"})
        controller.input("quit", b"q")
        controller.exited()
        self.assertNotIn(b"ACCEPTED ONCE", controller.output)

    def test_only_requested_protocol_response_is_forwarded_once(self):
        controller, take = self.ready()
        controller.start(cols=97, rows=23)
        controller.input("query", b"p")
        controller.text(b"\x1b[6n")
        controller.send({"op": "protocol", "data": b64(b"a\r")})
        self.assertEqual(controller.receive()["reason"], "unmatched-protocol-reply")
        controller.send({"op": "protocol", "data": b64(b"\x1b[99;1R")})
        self.assertEqual(controller.receive()["reason"], "unmatched-protocol-reply")
        controller.send({"op": "protocol", "data": b64(b"\x1b[6;1R")})
        event = controller.until(lambda e: e["type"] == "protocol")
        self.assertEqual(event, {"type": "protocol", "status": "sent", "query": "cursor"})
        controller.text(b"FIXTURE PROTOCOL OK")
        controller.send({"op": "protocol", "data": b64(b"\x1b[6;1R")})
        self.assertEqual(controller.receive()["reason"], "unmatched-protocol-reply")
        controller.input("quit", b"q")
        controller.exited()
        events = [json.loads(line) for line in (take / "pty-events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(e["type"] == "protocol-attempt" for e in events), 1)
        self.assertNotIn(b"ACCEPTED ONCE", controller.output)

    def test_final_output_and_timestamped_private_logs(self):
        controller, take = self.ready()
        controller.start()
        controller.input("quit", b"q")
        event = controller.exited()
        self.assertFalse(event["stopped"])
        self.assertIsNone(event["signal"])
        self.assertTrue(controller.output.endswith(b"FIXTURE DONE\r\n"))
        self.assertEqual((take / "terminal.ansi").read_bytes(), controller.output)
        logs = [json.loads(line) for line in (take / "pty-events.jsonl").read_text().splitlines()]
        self.assertTrue(all("timestamp" in entry for entry in logs))
        self.assertEqual(logs[-1]["type"], "exited")
        self.assertEqual(b"".join(base64.b64decode(e["data"]) for e in logs if e["type"] == "output"), controller.output)
        self.assertTrue(any(e["type"] == "input-attempt" and e["id"] == "quit" for e in logs))
        self.assertTrue(any(e["type"] == "input" and e["status"] == "sent" for e in logs))
        for name in ("terminal.ansi", "pty-events.jsonl"):
            self.assertEqual(stat.S_IMODE((take / name).stat().st_mode), 0o600)
        self.assertEqual(controller.proc.stderr.read(), b"")

    def test_nonzero_child_exit_is_reported_and_drained(self):
        controller, _ = self.ready()
        controller.start()
        controller.input("fault", b"x")
        event = controller.exited()
        self.assertEqual(event["exitCode"], 3)
        self.assertFalse(event["stopped"])
        self.assertTrue(controller.output.endswith(b"FIXTURE FAULT\r\n"))

    def test_stop_and_controller_eof_reap_owned_child(self):
        for mode in ("stop", "eof", "signal"):
            with self.subTest(mode=mode):
                controller, _ = self.ready()
                controller.start()
                if mode == "stop":
                    controller.send({"op": "stop"})
                elif mode == "eof":
                    controller.proc.stdin.close()
                else:
                    controller.proc.send_signal(signal.SIGTERM)
                event = controller.exited()
                self.assertTrue(event["stopped"])
                self.assertEqual(event["signal"], signal.SIGTERM)
                with self.assertRaises(ProcessLookupError):
                    os.kill(controller.pid, 0)

    def test_closed_controller_stdout_still_reaps_child(self):
        controller, take = self.ready()
        controller.start()
        controller.proc.stdout.close()
        controller.send({"op": "input", "id": "disconnected", "expectedSeq": controller.seq,
                         "data": b64(b"a")})
        self.assertNotEqual(controller.proc.wait(timeout=5), 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(controller.pid, 0)
        logs = [json.loads(line) for line in (take / "pty-events.jsonl").read_text().splitlines()]
        self.assertTrue(any(event["type"] == "error" for event in logs))

    def test_partial_input_failure_consumes_id_without_retry(self):
        bridge = bridge_module.Bridge(ROOT, self.take(), fixture=True)
        self.addCleanup(bridge.close)
        bridge.pid = 999999  # All child operations are mocked.
        self.addCleanup(setattr, bridge, "pid", None)
        message = {"op": "input", "id": "partial", "expectedSeq": 0, "data": b64(b"abc")}
        with redirect_stdout(io.StringIO()) as stdout, \
                mock.patch.object(bridge, "drain", return_value=True), \
                mock.patch.object(bridge, "poll_child"), \
                mock.patch.object(bridge, "write_input", side_effect=RuntimeError("partial write")) as write:
            with self.assertRaisesRegex(RuntimeError, "partial write"):
                bridge.handle(message)
            bridge.handle(message)
        write.assert_called_once_with(b"abc")
        self.assertEqual(json.loads(stdout.getvalue())["reason"], "duplicate-id")
        attempt = json.loads((bridge.take_dir / "pty-events.jsonl").read_text().splitlines()[0])
        self.assertEqual((attempt["type"], attempt["id"]), ("input-attempt", "partial"))

    def test_shutdown_escalates_with_a_bounded_kill_wait(self):
        bridge = bridge_module.Bridge(ROOT, self.take(), fixture=True)
        self.addCleanup(bridge.close)
        bridge.pid = 999999  # Never signal an actual process in this unit test.
        self.addCleanup(setattr, bridge, "pid", None)
        def killed(sig):
            if sig == signal.SIGKILL:
                bridge.status = signal.SIGKILL
                bridge.master_eof = True
        with mock.patch.object(bridge, "signal_group", side_effect=killed) as signals, \
                mock.patch.object(bridge, "drain"), mock.patch.object(bridge, "poll_child"), \
                mock.patch.object(bridge_module, "TERM_WAIT", 0.02), \
                mock.patch.object(bridge_module, "KILL_WAIT", 0.02):
            started = time.monotonic()
            bridge.terminate()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual([call.args[0] for call in signals.call_args_list[:2]],
                         [signal.SIGTERM, signal.SIGKILL])

    def test_malformed_and_oversized_lines_recover(self):
        controller, _ = self.ready()
        for data in (b"not-json\n", b'{"op":"start","op":"stop"}\n',
                     b'{"op":"start","cols":NaN,"rows":30}\n', b"\xff\n",
                     b"z" * (bridge_module.MAX_MESSAGE + 1) + b"\n"):
            controller.proc.stdin.write(data)
            controller.proc.stdin.flush()
            self.assertEqual(controller.receive()["type"], "rejected")
        controller.start()
        controller.input("quit", b"q")
        controller.exited()

    def test_input_at_size_limit_and_partial_writes(self):
        controller, _ = self.ready()
        controller.start()
        self.assertEqual(controller.input("limit", b"z" * bridge_module.MAX_INPUT)["status"], "sent")
        controller.input("quit", b"q")
        controller.exited()
        bridge = bridge_module.Bridge(ROOT, self.take(), fixture=True)
        self.addCleanup(bridge.close)
        bridge.master = 123456  # Mocked writes; clear before closing.
        self.addCleanup(setattr, bridge, "master", None)
        with mock.patch.object(bridge_module.os, "write", side_effect=[2, BlockingIOError(), 3]) as write, \
                mock.patch.object(bridge, "drain"), \
                mock.patch.object(bridge_module.select, "select", return_value=([], [], [])):
            bridge.write_input(b"abcde")
        self.assertEqual([c.args[1] for c in write.call_args_list], [b"abcde", b"cde", b"cde"])

    def test_invalid_directories_and_approval_environment_fail_closed(self):
        for kwargs in ({"workspace": "relative"}, {"workspace": self.directory / "missing"},
                       {"env": {"COPILOT_ALLOW_ALL": "1"}},
                       {"env": {"COPILOT_ASSISTED_APPROVAL": "1"}},
                       {"env": {"COPILOT_ALLOW_ALL": "yes"}}):
            with self.subTest(kwargs=kwargs):
                controller, _ = self.controller(**kwargs)
                self.assertEqual(controller.receive()["type"], "error")
                self.assertNotEqual(controller.proc.wait(timeout=3), 0)
                self.assertIsNone(controller.pid)
        link = self.directory / "take-link"
        link.symlink_to(self.take(), target_is_directory=True)
        controller, _ = self.controller(take=link)
        self.assertEqual(controller.receive()["type"], "error")
        self.assertNotEqual(controller.proc.wait(timeout=3), 0)

    def test_output_symlinks_cannot_overwrite_existing_files(self):
        take = self.take()
        target = self.directory / "untouched"
        target.write_bytes(b"keep")
        (take / "terminal.ansi").symlink_to(target)
        controller, _ = self.controller(take=take)
        self.assertEqual(controller.receive()["type"], "error")
        self.assertNotEqual(controller.proc.wait(timeout=3), 0)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_fixed_commands_without_executing_them(self):
        take = self.take()
        bridge = bridge_module.Bridge(ROOT, take, fixture=False)
        self.addCleanup(bridge.close)
        self.assertEqual(bridge.command(), [
            "/opt/homebrew/bin/copilot", "--mode", "plan", "--no-auto-update", "--no-remote",
            "--no-remote-export", "--disable-builtin-mcps", "--log-dir", str(take / "logs"),
            "--log-level", "info", "--usage-output-file", str(take / "usage.json")])
        fixture = bridge_module.Bridge(ROOT, self.take(), fixture=True)
        self.addCleanup(fixture.close)
        self.assertEqual(fixture.command(), [sys.executable, "-u", str(ROOT / "test/fixtures/terminal-fixture.py")])


class TerminalQueryTests(unittest.TestCase):
    def test_supported_queries_survive_every_byte_split(self):
        cases = [
            (b"\x1b[6n", b"\x1b[3;2R", "cursor"),
            (b"\x1b[?6n", b"\x1b[?3;2R", "private-cursor"),
            (b"\x1b[5n", b"\x1b[0n", "status"),
            (b"\x1b[c", b"\x1b[?1;2c", "device"),
            (b"\x1b[>0c", b"\x1b[>0;276;0c", "secondary-device"),
            (b"\x1b[?2026$p", b"\x1b[?2026;2$y", "sync-mode"),
            (b"\x1b[?2004$p", b"\x1b[?2004;1$y", "paste-mode"),
            (b"\x1b]10;?\x07", b"\x1b]10;rgb:eeee/eeee/eeee\x07", "foreground"),
            (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:1111/1616/1d1d\x1b\\", "background"),
            (b"\x1b[?1004h", b"\x1b[O", "focus"),
            (b"\x1b[?1004h", b"\x1b[I", "focus"),
            (b"\x1b]4;0;?\x07", b"\x1b]4;0;rgb:2e2e/3434/3636\x1b\\", "palette-0"),
            (b"\x1b]4;15;?\x1b\\", b"\x1b]4;15;rgb:eeee/eeee/ecec\x1b\\", "palette-15"),
            (b"\x1b]4;255;?\x1b\\", b"\x1b]4;255;rgb:eeee/eeee/eeee\x07", "palette-255"),
        ]
        for query, reply, kind in cases:
            for split in range(len(query) + 1):
                with self.subTest(query=query, split=split):
                    parser = bridge_module.TerminalQueries()
                    parser.feed(query[:split])
                    parser.feed(query[split:])
                    self.assertIsNone(parser.consume(reply + b"a\r", 110, 28))
                    self.assertEqual(parser.consume(reply, 110, 28), kind)
                    self.assertIsNone(parser.consume(reply, 110, 28))

    def test_focus_and_palette_only_accept_one_complete_corresponding_reply(self):
        queries = b"\x1b[?1004h\x1b]4;0;?\x1b\\\x1b]4;15;?\x1b\\"
        replies = [(b"\x1b[O", "focus"),
                   (b"\x1b]4;0;rgb:2e2e/3434/3636\x1b\\", "palette-0"),
                   (b"\x1b]4;15;rgb:eeee/eeee/ecec\x1b\\", "palette-15")]
        parser = bridge_module.TerminalQueries()
        for reply, _ in replies:
            self.assertIsNone(parser.consume(reply, 110, 28))
        # Feed across every byte boundary, including the OSC terminators.
        for byte in queries:
            parser.feed(bytes([byte]))
        for reply, _ in replies:
            for split in range(1, len(reply)):
                self.assertIsNone(parser.consume(reply[:split], 110, 28))
                self.assertIsNone(parser.consume(reply[split:], 110, 28))
            self.assertIsNone(parser.consume(reply * 2, 110, 28))
            self.assertIsNone(parser.consume(reply + b"a\r", 110, 28))
            self.assertIsNone(parser.consume(b"a" + reply, 110, 28))
            self.assertIsNone(parser.consume(reply + b"\x1b[200~a\x1b[201~", 110, 28))
        self.assertIsNone(parser.consume(b"".join(reply for reply, _ in replies), 110, 28))
        self.assertIsNone(parser.consume(b"\x1b]4;1;rgb:ffff/ffff/ffff\x1b\\", 110, 28))
        # A different requested index can arrive first without consuming index 0.
        for reply, kind in reversed(replies):
            self.assertEqual(parser.consume(reply, 110, 28), kind)
            self.assertIsNone(parser.consume(reply, 110, 28))
        self.assertIsNone(parser.consume(b"\x1b[I", 110, 28))
        self.assertEqual(parser.pending, [])

    def test_palette_bounds_and_unsupported_queries(self):
        parser = bridge_module.TerminalQueries()
        for index in (b"-1", b"256", b"999", b"0000", b"x"):
            parser.feed(b"\x1b]4;" + index + b";?\x1b\\")
        # Installed xterm 6.0.0 does not answer kitty keyboard queries.
        parser.feed(b"\x1b[>4;2m\x1b[?u")
        self.assertEqual(parser.pending, [])
        self.assertIsNone(parser.consume(b"\x1b[?0u", 110, 28))
        parser.feed(b"\x1b]4;255;?\x1b\\")
        for value in (b"4;256;rgb:ffff/ffff/ffff", b"4;999;rgb:ffff/ffff/ffff",
                      b"4;-1;rgb:ffff/ffff/ffff", b"4;254;rgb:ffff/ffff/ffff",
                      b"4;255;rgb:fffff/ffff/ffff", b"4;255;rgb:gggg/ffff/ffff",
                      b"4;255;rgb:ffff//ffff"):
            self.assertIsNone(parser.consume(b"\x1b]" + value + b"\x1b\\", 110, 28))
        self.assertEqual(parser.consume(b"\x1b]4;255;rgb:ffff/ffff/ffff\x1b\\", 110, 28), "palette-255")

    def test_new_query_types_preserve_string_prefix_ttl_and_queue_bounds(self):
        for query in (b"\x1b[?1004h", b"\x1b]4;0;?\x1b\\"):
            for prefix in (b"\x1b]0;title ", b"\x1bP", b"\x1b_", b"\x1b^"):
                parser = bridge_module.TerminalQueries()
                for byte in prefix + query + b"\x1b\\":
                    parser.feed(bytes([byte]))
                self.assertEqual(parser.pending, [])
        for query, reply in ((b"\x1b[?1004h", b"\x1b[O"),
                             (b"\x1b]4;0;?\x1b\\", b"\x1b]4;0;rgb:ffff/ffff/ffff\x1b\\")):
            parser = bridge_module.TerminalQueries()
            with mock.patch.object(bridge_module.time, "monotonic", return_value=10):
                parser.feed(query)
            with mock.patch.object(bridge_module.time, "monotonic", return_value=15):
                self.assertIsNone(parser.consume(reply, 110, 28))
        parser = bridge_module.TerminalQueries()
        parser.feed(b"\x1b[?1004h" + b"".join(b"\x1b]4;%d;?\x1b\\" % i for i in range(31)))
        self.assertEqual(len(parser.pending), 32)
        with self.assertRaisesRegex(ValueError, "too many"):
            parser.feed(b"\x1b]4;31;?\x1b\\")

    def test_unrequested_expired_oversized_and_string_embedded_queries(self):
        for value in (b"[6n", b"\x1b]0;title \x1b[6n\x07", b"\x1bPpayload \x1b[6n\x1b\\",
                      b"\x1b[6\x18n", b"\x1b]11;?" + b"x" * 400 + b"\x07"):
            parser = bridge_module.TerminalQueries()
            parser.feed(value)
            self.assertEqual(parser.pending, [], value)
        parser = bridge_module.TerminalQueries()
        with mock.patch.object(bridge_module.time, "monotonic", return_value=10):
            parser.feed(b"\x1b[6n")
        with mock.patch.object(bridge_module.time, "monotonic", return_value=16):
            self.assertIsNone(parser.consume(b"\x1b[1;1R", 110, 28))
        parser.feed(b"\x1b[6n")
        self.assertIsNone(parser.consume(b"\x1b[29;1R", 110, 28))
        self.assertIsNone(parser.consume(b"\x1b[1;111R", 110, 28))
        self.assertEqual(parser.consume(b"\x1b[1;1R", 110, 28), "cursor")
        with self.assertRaisesRegex(ValueError, "too many"):
            parser.feed(b"\x1b[6n" * 33)


if __name__ == "__main__":
    unittest.main()
