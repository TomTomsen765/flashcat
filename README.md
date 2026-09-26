# Flashcat

```
      /\_/\
     ( o.o )
      > ^ <
  ╭──────────────────────────────────────────────────────╮
  │ ✻ Flashcat                           local · private │
  │ Gemma 4 · 26B · 64k context                          │
  │ ~/Documents/my-project                               │
  │ /help for commands                                   │
  ╰──────────────────────────────────────────────────────╯
```

**A local AI assistant for the macOS terminal.** Flashcat chats with you, reads and writes files in the
folder you start it in, looks at images, reads PDFs, Word and Excel files (even scans), and can search
the web — all with a model that runs **on your own Mac** through [LM Studio](https://lmstudio.ai).
Nothing you ask leaves your computer unless you allow a web request.

Named after Flash, my cat. 🐈

## Install

1. Install **[LM Studio](https://lmstudio.ai)** and open it once.
2. Run this in the terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/TomTomsen765/flashcat/main/install.sh | bash
```

The installer checks your Mac, installs the `flashcat` command into `~/.local/bin` and downloads the
default model, **Gemma 4 26B** (about 15.6 GB), through LM Studio.

**Requirements:** macOS on Apple Silicon, 24 GB memory recommended (16 GB works, but tight),
Apple's command line tools (`xcode-select --install`) for Python.

## Use

```sh
cd ~/Documents/my-project
flashcat
```

Then just talk to it:

```
❯ Summarize @contract.pdf and put the deadlines into deadlines.md

  ◇ reads contract.pdf                                         ✓ 0.4 s
  ◇ writes deadlines.md
  ? Write? [Y/N] y
  ✓ created: deadlines.md

⏺ The contract runs until …
```

| Command | |
|---|---|
| `/help` | all commands |
| `/undo` | undo the last file change |
| `/copy`, `/save` | copy or save the last answer |
| `/resume` | earlier chats in this folder |
| `/compact` | summarize the chat to free context |
| `/think` | think more thoroughly (slower) |
| `Esc` | cancel the current answer |
| `@file` | attach a file to your message (Tab completes) |

Start options: `flashcat --continue` (last chat), `flashcat --models`, `flashcat --model <name>`,
`FLASHCAT_CONTEXT=32768 flashcat` (smaller context, less memory).

Put standing instructions into a `FLASHCAT.md` in your project folder (or `~/.flashcat/FLASHCAT.md`
for all folders).

## What it can do

- **Files:** list, read, search, create and change text files; create Word (`.docx`) and PDF documents;
  rename and move files
- **Documents:** reads PDF, Word, Excel — scanned PDFs and images via macOS text recognition
- **Images:** describes and analyzes pictures in the folder
- **Web:** web search (DuckDuckGo) and reading web pages
- **Terminal:** answers stream live with Markdown, tables and syntax-highlighted code; clickable file names

## Safety

- Flashcat can only access the folder it was started in (and its subfolders). Paths outside, `../`
  and symbolic links pointing outside are blocked.
- **Every** change asks first — with a preview of the change — and the old version is backed up in
  `.flashcat-backup/`. `/undo` reverts the last change.
- **Every** web request asks first and shows the exact address or search term. Addresses on your own
  computer or local network are always blocked.
- It cannot delete files or run commands.
- Starting it in your home folder shows a warning first.

The model runs locally; LM Studio is loaded when Flashcat starts and unloaded again when the last
Flashcat window closes, so the memory is freed.

## Uninstall

```sh
curl -fsSL https://raw.githubusercontent.com/TomTomsen765/flashcat/main/uninstall.sh | bash
```

## License

MIT
