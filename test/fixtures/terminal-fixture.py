#!/usr/bin/env python3
"""Benign local PTY fixture. No model, network, branded consent, or input echo."""

import os
import re
import select
import time
import tty


def write(data):
    while data:
        count = os.write(1, data)
        data = data[count:]


def main():
    tty.setraw(0)
    size = os.get_terminal_size(0)
    controlling = os.tcgetpgrp(0) == os.getpgrp() == os.getpid()
    write(b"\x1b[?1049h\x1b[2J\x1b[HFIXTURE / NOT COPILOT\r\n")
    write(("FIXTURE SIZE cols=%d rows=%d TERM=%s CTL=%s\r\n" %
           (size.columns, size.lines, os.environ.get("TERM", ""), controlling)).encode("ascii"))
    korean = "한글 분할 출력\r\n".encode("utf-8")
    for chunk in (korean[:1], korean[1:2], korean[2:7], korean[7:]):
        write(chunk)
        time.sleep(0.02)
    prompt = b"FIXTURE input: [a] accept [q] quit"
    write(prompt)
    changed = False
    while True:
        data = os.read(0, 1024)
        if not data:
            return 0
        for key in data:
            if key == ord("q"):
                write(b"\r\n\x1b[?1049lFIXTURE DONE\r\n")
                return 0
            if key == ord("x"):
                write(b"\r\n\x1b[?1049lFIXTURE FAULT\r\n")
                return 3
            if key == ord("p"):
                write(b"\r\nFIXTURE QUERY\r\n\x1b[")
                time.sleep(0.02)
                write(b"6n")
                reply = bytearray()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not reply.endswith(b"R"):
                    if select.select([0], [], [], 0.05)[0]:
                        reply.extend(os.read(0, 128))
                if not re.fullmatch(rb"\x1b\[[1-9][0-9]*;[1-9][0-9]*R", reply):
                    write(b"\r\nFIXTURE PROTOCOL FAILED\r\n")
                    return 4
                write(b"\r\nFIXTURE PROTOCOL OK\r\n" + prompt)
            if key == ord("t"):
                # Same VT startup packets, no real application content. xterm 6
                # ignores kitty's query and answers focus-enable before OSC 10.
                packets = (
                    b"\x1b[>4;2m\x1b[?u",
                    b"\x1b[?1049h\x1b[?2004h\x1b[?1004h\x1b[?1003h\x1b[?1006h\x1b[?25l\x1b]10;?\x1b\\",
                    b"\x1b]11;?\x1b\\" + b"".join(b"\x1b]4;%d;?\x1b\\" % i for i in range(16)),
                )
                for packet in packets:
                    write(packet)
                    time.sleep(0.02)
                color = rb";rgb:[0-9a-f]{4}/[0-9a-f]{4}/[0-9a-f]{4}\x1b\\"
                identities = [b"10", b"11"] + [b"4;%d" % i for i in range(16)]
                expected = rb"\x1b\[O" + b"".join(rb"\x1b\]" + ident + color for ident in identities)
                reply = bytearray()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and len(reply) < 2048 and not re.fullmatch(expected, reply):
                    if select.select([0], [], [], 0.05)[0]:
                        reply.extend(os.read(0, 1024))
                if not re.fullmatch(expected, reply):
                    write(b"\r\nFIXTURE STARTUP PROTOCOL FAILED\r\n")
                    return 5
                write(b"\r\nFIXTURE STARTUP PROTOCOL OK\r\n" + prompt)
            if key == ord("s"):
                write(b"\x1b[?2026h\x1b[2J\x1b[HFIXTURE SYNCHRONIZED NEW SCREEN\r\n")
                time.sleep(0.25)
                write(b"\x1b[?2026l" + prompt)
            if key == ord("b"):
                changed = True
                prompt = b"FIXTURE CHANGED input: [q] quit (accept unavailable)"
                write(b"\r\n\x1b[2K" + prompt)
            elif key == ord("a") and not changed:
                write(b"\r\nACCEPTED ONCE\r\n" + prompt)
            # Unknown bytes are ignored, never echoed (including arbitrary secrets).


if __name__ == "__main__":
    raise SystemExit(main())
