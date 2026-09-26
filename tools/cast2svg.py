#!/usr/bin/env python3
"""Turns an asciicast v2 recording (from record.py) into an animated SVG of a macOS-style terminal window.

Replays the recording in the small terminal emulator of ansi2svg.py and takes a frame whenever the screen
changed (at most `--fps` per second). Long pauses are shortened to `--max-gap` seconds. Lines that look the
same in several frames are stored once and reused, which keeps the file small. The frames play in a loop
through a CSS animation, which GitHub shows in a README image.

Usage: cast2svg.py in.cast out.svg [--fps 10] [--max-gap 1.2] [--hold 5] [--title flashcat]
"""

import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ansi2svg import BG, FG, Screen  # noqa: E402

CW, LH, PAD, TOP = 8.43, 20, 22, 44  # character width, line height, padding, title bar height


def line_svg(line):
    """SVG text elements for one screen line (y = 0 is its baseline)."""
    parts, col = [], 0
    while col < len(line):
        ch, st = line[col]
        if ch == " ":
            col += 1
            continue
        end = col + 1
        if ord(ch) < 128:  # plain ASCII of the same style in one element; everything else on its own column
            while end < len(line) and line[end][1] == st and ord(line[end][0]) < 128:
                end += 1
        run = "".join(c for c, _ in line[col:end]).rstrip()
        if run:
            attrs = [f'x="{col * CW:.1f}"', f'fill="{st.get("fg", FG)}"']
            if st.get("bold"):
                attrs.append('font-weight="bold"')
            if st.get("italic"):
                attrs.append('font-style="italic"')
            if st.get("dim"):
                attrs.append('opacity="0.55"')
            parts.append(f'<text {" ".join(attrs)}>{html.escape(run)}</text>')
        col = end
    return "".join(parts)


def snapshot(screen, rows):
    """The visible screen: the last `rows` lines up to the cursor, plus the cursor position."""
    bottom = max(screen.r, max((i for i, r in enumerate(screen.rows) if r), default=0))
    top = max(0, bottom - rows + 1)
    lines = tuple(tuple((ch, tuple(sorted(st.items()))) for ch, st in screen.rows[i]) if i < len(screen.rows) else ()
                  for i in range(top, top + rows))
    return lines, (screen.r - top, min(screen.c, screen.cols - 1))


def main():
    args = sys.argv[1:]
    src, out = args[0], args[1]
    opt = {k: args[args.index(k) + 1] for k in ("--fps", "--max-gap", "--hold", "--title") if k in args}
    fps, max_gap, hold = float(opt.get("--fps", 10)), float(opt.get("--max-gap", 1.2)), float(opt.get("--hold", 5))
    title = opt.get("--title", "flashcat")

    with open(src, encoding="utf-8") as f:
        header = json.loads(f.readline())
        events = [json.loads(l) for l in f if l.strip()]
    cols, rows = header["width"], header["height"]
    screen = Screen(cols)

    frames = []  # (time, lines, cursor)
    clock, last_t, last_frame_t = 0.0, 0.0, -1.0
    for t, kind, data in events:
        if kind != "o":
            continue
        clock += min(t - last_t, max_gap)
        last_t = t
        screen.feed(data)
        lines, cursor = snapshot(screen, rows)
        if frames and (lines, cursor) == frames[-1][1:]:
            continue
        if frames and clock - last_frame_t < 1 / fps:
            frames[-1] = (frames[-1][0], lines, cursor)  # too soon: this state replaces the last frame
            continue
        frames.append((clock, lines, cursor))
        last_frame_t = clock
    total = clock + hold

    width = int(cols * CW + 2 * PAD)
    height = int(TOP + rows * LH + PAD)
    line_ids, defs = {}, []
    film = []
    for n, (_, lines, (cr, cc)) in enumerate(frames):
        uses = []
        for r, line in enumerate(lines):
            key = tuple((ch, st) for ch, st in line)
            if not "".join(ch for ch, _ in key).strip():
                continue
            if key not in line_ids:
                line_ids[key] = f"l{len(line_ids)}"
                defs.append(f'<g id="{line_ids[key]}">{line_svg([(ch, dict(st)) for ch, st in key])}</g>')
            uses.append(f'<use href="#{line_ids[key]}" y="{r * LH}"/>')
        cursor = f'<rect x="{cc * CW:.1f}" y="{cr * LH - 14}" width="{CW:.1f}" height="18" fill="{FG}" opacity="0.5"/>'
        film.append(f'<g transform="translate({n * width} 0)">{"".join(uses)}{cursor}</g>')

    steps = [f"{100 * t / total:.3f}%{{transform:translateX({-n * width}px)}}" for n, (t, _, _) in enumerate(frames)]
    steps[0] = f"0%{{transform:translateX(0px)}}"
    style = (f"#film{{animation:play {total:.2f}s steps(1,end) infinite}}"
             f"@keyframes play{{{''.join(steps)}100%{{transform:translateX({-(len(frames) - 1) * width}px)}}}}")
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           f"<style>{style}</style>",
           f'<rect width="{width}" height="{height}" rx="12" fill="{BG}"/>',
           '<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/>'
           '<circle cx="62" cy="20" r="6" fill="#28c840"/>',
           f'<text x="{width / 2}" y="24" fill="#7f848e" font-family="-apple-system,Helvetica,Arial,sans-serif" '
           f'font-size="13" text-anchor="middle">{html.escape(title)}</text>',
           f'<svg x="{PAD}" y="{TOP}" width="{width - 2 * PAD}" height="{rows * LH}" overflow="hidden">',
           f'<defs>{"".join(defs)}</defs>',
           '<g font-family="SF Mono,Menlo,Monaco,Consolas,monospace" font-size="14" xml:space="preserve">',
           f'<g transform="translate(0 14)"><g id="film">{"".join(film)}</g></g>',
           "</g></svg></svg>"]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))
    print(f"{out}: {len(frames)} frames, {total:.1f} s, {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()
