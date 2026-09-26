#!/usr/bin/env python3
"""Turns a recorded terminal session (script(1) log with ANSI codes) into an SVG screenshot.

Replays the output in a tiny terminal emulator (cursor moves, erase, colors) so live re-drawn lines
end up as they looked on screen, then draws the final screen as a macOS-style terminal window.

Usage: ansi2svg.py session.log out.svg [--from TEXT] [--until TEXT] [--cols N]
"""

import html
import re
import sys

PALETTE = {  # dark theme, similar to Hyper / One Dark
    30: "#3f4451", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b", 34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#dcdfe4",
    90: "#7f848e", 91: "#ff7a85", 92: "#b5e890", 93: "#f0d197", 94: "#8cc8ff", 95: "#dd9cf0", 96: "#7fd3dc", 97: "#ffffff",
}
FG, BG = "#dcdfe4", "#1c1d21"


class Screen:
    def __init__(self, cols):
        self.cols, self.rows = cols, [[]]
        self.r = self.c = 0
        self.saved = (0, 0)
        self.style = {}

    def row(self, r):
        while len(self.rows) <= r:
            self.rows.append([])
        return self.rows[r]

    def put(self, ch):
        if self.c >= self.cols:  # soft wrap
            self.r, self.c = self.r + 1, 0
        line = self.row(self.r)
        while len(line) <= self.c:
            line.append((" ", {}))
        line[self.c] = (ch, dict(self.style))
        self.c += 1

    def sgr(self, params):
        nums = [int(p) if p else 0 for p in params.split(";")] if params else [0]
        i = 0
        while i < len(nums):
            n = nums[i]
            if n == 0:
                self.style = {}
            elif n == 1:
                self.style["bold"] = True
            elif n == 2:
                self.style["dim"] = True
            elif n == 3:
                self.style["italic"] = True
            elif n == 22:
                self.style.pop("bold", None), self.style.pop("dim", None)
            elif n == 23:
                self.style.pop("italic", None)
            elif n in PALETTE:
                self.style["fg"] = PALETTE[n]
            elif n == 39:
                self.style.pop("fg", None)
            elif n == 38 and i + 4 < len(nums) and nums[i + 1] == 2:
                self.style["fg"] = "#%02x%02x%02x" % tuple(nums[i + 2:i + 5])
                i += 4
            elif n == 38 and i + 2 < len(nums) and nums[i + 1] == 5:
                self.style["fg"] = PALETTE.get(30 + nums[i + 2] % 8, FG)
                i += 2
            i += 1

    def feed(self, data):
        data = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", data)  # OSC (hyperlinks, titles)
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == "\x1b":
                m = re.match(r"\x1b\[([?0-9;]*)([A-Za-z])", data[i:])
                if m:
                    params, cmd = m.group(1), m.group(2)
                    n = int(params) if params.isdigit() else 1
                    if cmd == "m":
                        self.sgr(params)
                    elif cmd == "A":
                        self.r = max(0, self.r - n)
                    elif cmd == "B":
                        self.r += n
                    elif cmd == "C":
                        self.c += n
                    elif cmd == "D":
                        self.c = max(0, self.c - n)
                    elif cmd == "G":
                        self.c = n - 1
                    elif cmd == "K":
                        del self.row(self.r)[self.c:]
                    elif cmd == "J":
                        del self.row(self.r)[self.c:]
                        del self.rows[self.r + 1:]
                    i += len(m.group(0))
                    continue
                if data[i + 1:i + 2] == "7":
                    self.saved = (self.r, self.c)
                elif data[i + 1:i + 2] == "8":
                    self.r, self.c = self.saved
                i += 2
                continue
            if ch == "\r":
                self.c = 0
            elif ch == "\n":
                self.r += 1
                self.row(self.r)
            elif ch == "\b":
                self.c = max(0, self.c - 1)
            elif ch == "\t":
                self.c = (self.c // 8 + 1) * 8
            elif ch >= " ":
                self.put(ch)
            i += 1

    def text(self, r):
        return "".join(ch for ch, _ in self.rows[r]).rstrip()


def render(screen, start, end, out, cols):
    cw, lh, pad, top = 8.43, 20, 22, 44
    lines = screen.rows[start:end]
    width = int(cols * cw + 2 * pad)
    height = int(top + len(lines) * lh + pad)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<rect width="{width}" height="{height}" rx="12" fill="{BG}"/>',
             '<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/>'
             '<circle cx="62" cy="20" r="6" fill="#28c840"/>',
             f'<text x="{width / 2}" y="24" fill="#7f848e" font-family="-apple-system,Helvetica,Arial,sans-serif" '
             'font-size="13" text-anchor="middle">flashcat</text>',
             '<g font-family="SF Mono,Menlo,Monaco,Consolas,monospace" font-size="14" xml:space="preserve">']
    for n, line in enumerate(lines):
        y = top + n * lh + 14
        col = 0
        while col < len(line):
            ch, st = line[col]
            if ch == " ":
                col += 1
                continue
            # group plain ASCII of the same style; place every other character on its own column
            end_col = col + 1
            if ord(ch) < 128:
                while end_col < len(line) and line[end_col][1] == st and ord(line[end_col][0]) < 128:
                    end_col += 1
            run = "".join(c for c, _ in line[col:end_col]).rstrip()
            attrs = [f'x="{pad + col * cw:.1f}"', f'y="{y}"', f'fill="{st.get("fg", FG)}"']
            if st.get("bold"):
                attrs.append('font-weight="bold"')
            if st.get("italic"):
                attrs.append('font-style="italic"')
            if st.get("dim"):
                attrs.append('opacity="0.55"')
            if run:
                parts.append(f'<text {" ".join(attrs)}>{html.escape(run)}</text>')
            col = end_col
    parts.append("</g></svg>")
    open(out, "w", encoding="utf-8").write("\n".join(parts))


def main():
    args = sys.argv[1:]
    src, out = args[0], args[1]
    opt = {k: args[args.index(k) + 1] for k in ("--from", "--until", "--cols") if k in args}
    cols = int(opt.get("--cols", 86))
    screen = Screen(cols)
    screen.feed(open(src, encoding="utf-8", errors="replace", newline="").read())  # keep \r as it is
    texts = [screen.text(r) for r in range(len(screen.rows))]
    start = next((i for i, t in enumerate(texts) if opt.get("--from", "") in t and t), 0)
    end = next((i for i, t in enumerate(texts) if "--until" in opt and opt["--until"] in t), len(texts))
    while start < end and not texts[start]:
        start += 1
    while end > start and not texts[end - 1]:
        end -= 1
    render(screen, start, end, out, cols)
    print(f"{out}: rows {start}-{end} of {len(texts)}")


if __name__ == "__main__":
    main()
