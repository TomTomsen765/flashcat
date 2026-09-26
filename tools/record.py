#!/usr/bin/env python3
"""Records a scripted terminal session as an asciicast v2 file (for cast2svg.py or asciinema).

Runs a command in a pseudo terminal of a fixed size and plays a script against it: wait until some text
appears, then type like a person. Everything the program prints is saved with timestamps.

Usage: record.py script.json out.cast

script.json: {"cols": 90, "rows": 30, "cwd": "…", "home": "…", "command": "flashcat", "run": "~/.local/bin/flashcat",
              "prompt": "~/invoices % ",
              "steps": [["wait", "text"], ["sleep", 1.0], ["type", "hello\\n"], ["end"]]}
"prompt" is shown (and "command" typed after it) before the command starts, like in a real shell;
"run" is what actually runs (default: "command").
"end" stops the recording; the program then gets /exit and is left to finish on its own.
"""

import codecs
import fcntl
import json
import os
import pty
import random
import select
import struct
import sys
import termios
import time

ANSI = __import__("re").compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[78]")


def main():
    script = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    cols, rows = script.get("cols", 90), script.get("rows", 30)
    events, start = [], time.time()

    def emit(data):
        events.append([round(time.time() - start, 3), "o", data])

    def fake_type(text):
        for ch in text:
            emit(ch.replace("\n", "\r\n"))
            time.sleep(random.uniform(0.04, 0.09))

    if script.get("prompt"):
        emit(script["prompt"])
        time.sleep(0.8)
        fake_type(script["command"] + "\n")

    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.chdir(os.path.expanduser(script["cwd"]))
        os.environ["TERM"] = "xterm-256color"
        if script.get("home"):  # e.g. a demo home folder, so paths are shown as ~/…
            os.environ["HOME"] = os.path.expanduser(script["home"])
        os.execvp("/bin/zsh", ["/bin/zsh", "-c", script.get("run", script["command"])])

    seen = ""  # everything printed so far, without escape codes (to wait for text)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")  # characters may be split between two reads

    def pump(timeout):
        nonlocal seen
        if select.select([fd], [], [], timeout)[0]:
            try:
                raw = os.read(fd, 65536)
            except OSError:
                return False
            if not raw:
                return False
            data = decoder.decode(raw)
            emit(data)
            seen += ANSI.sub("", data)
        return True

    def wait_for(text, limit=600):
        mark = len(seen)
        deadline = time.time() + limit
        while time.time() < deadline:
            if text in seen[mark:]:
                return
            if not pump(0.05):
                sys.exit(f"program ended while waiting for {text!r}")
        sys.exit(f"timed out waiting for {text!r}")

    for step in script["steps"]:
        kind = step[0]
        if kind == "wait":
            wait_for(step[1])
        elif kind == "sleep":
            end = time.time() + step[1]
            while time.time() < end:
                pump(0.05)
        elif kind == "type":
            for ch in step[1]:
                os.write(fd, ch.replace("\n", "\r").encode())
                pause = time.time() + random.uniform(0.04, 0.10)
                while time.time() < pause:
                    pump(0.01)
        elif kind == "end":
            break

    header = {"version": 2, "width": cols, "height": rows, "timestamp": int(start),
              "env": {"TERM": "xterm-256color", "SHELL": "/bin/zsh"}}
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{out}: {len(events)} events, {events[-1][0]:.1f} s")

    os.write(fd, b"/exit\r")  # let the program clean up (e.g. unload the model)
    deadline = time.time() + 60
    while time.time() < deadline and pump(0.2):
        pass
    os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
