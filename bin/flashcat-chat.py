#!/usr/bin/env python3
"""Flashcat - terminal chat with a local LM Studio model that can work in the current folder.

Named after Flash the cat.

Tools: list, read (text/PDF/Word/Excel, scans via macOS text recognition), search, write + edit (text
files), write Word and PDF documents, move/rename, view images, run commands (in a sandbox), fetch web pages,
web search. Every change is confirmed by the user, backed up and can be undone with /undo. File access is limited
to the folder the chat was started in. Talks to the OpenAI-compatible server of LM Studio or Ollama on localhost
(port from FLASHCAT_PORT). Stdlib only.

Usage: flashcat-chat.py MODEL [--continue] [QUESTION …]
       (with a question: answers once and exits; piped input is attached to the question)
       flashcat-chat.py --version | --confirm-folder
"""

import base64
import datetime
import difflib
import fcntl
import hashlib
import html
import html.parser
import http.client
import ipaddress
import json
import os
import re
import select
import signal
import socket
import shutil
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

try:
    import readline  # noqa: F401  (line editing / history for input())
except ImportError:
    pass

VERSION = "1.3.11"
BACKEND = os.environ.get("FLASHCAT_BACKEND") or "lmstudio"  # "lmstudio" or "ollama", chosen by the launcher
SERVER = f"http://localhost:{os.environ.get('FLASHCAT_PORT') or (11434 if BACKEND == 'ollama' else 1234)}"
API_KEY = os.environ.get("FLASHCAT_API_KEY", "")  # only needed if LM Studio requires authentication
URL = SERVER + "/v1/chat/completions"
MODEL = sys.argv[1] if len(sys.argv) > 1 else ""
MODEL_NAME = os.environ.get("FLASHCAT_MODEL_NAME") or MODEL  # shown name (Ollama runs a copy with Flashcat's context size)
NAME = "Flashcat"
RESUME = any(a in ("--continue", "-c") for a in sys.argv[2:])
QUESTION = " ".join(a for a in sys.argv[2:] if a not in ("--continue", "-c")).strip()  # one-shot mode
ROOT = os.path.realpath(os.getcwd())
HOME_DIR = os.path.expanduser("~/.flashcat")
FOLDER_ID = hashlib.sha1(ROOT.encode()).hexdigest()[:16]
SESSION_DIR = os.path.join(HOME_DIR, "sessions", FOLDER_ID)
MAX_READ = 100_000
MAX_HITS = 100
MAX_DOCS_SEARCHED = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".Trash", "Library"}
WRITE_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".html", ".htm", ".css",
             ".js", ".ts", ".py", ".sh", ".ini", ".log", ".tex"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff"}
TEXTUTIL_EXT = {".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".webarchive"}
BACKUP_DIR = ".flashcat-backup"

DIM, BOLD, YELLOW, RED, GREEN, RESET = "\033[2m", "\033[1m", "\033[1;33m", "\033[31m", "\033[32m", "\033[0m"
ORANGE = "\033[1;38;2;217;119;87m"

SYSTEM = (
    "You are Flashcat, a helpful AI assistant in the terminal that runs locally on the user's Mac "
    "(named after Flash, the cat of its creator). Answer concisely and clearly, in the language the user writes in. "
    f"You work in the folder {ROOT} (and its subfolders); paths are relative to this folder. "
    "Use the tools when the user asks about files, folder contents, images, web pages or current "
    "information from the internet, instead of guessing. "
    "You can read and search text, PDF, Word and Excel files (read_file reads scanned PDFs and images with text "
    "via text recognition), look at images (view_image), search the internet (web_search) and read web pages "
    "(fetch_url). For information from the internet, name the source with its address (URL). "
    "You create text files (e.g. .txt, .md, .csv) with write_file or change them precisely with edit_file; "
    "you create Word documents (.docx) with write_docx and PDF documents with write_pdf; rename or move files with move_file "
    "(several at once with move_files). "
    "For changes to existing text files use edit_file: read the file first and give old_text exactly "
    "as it appears in the file. "
    "You can run terminal commands with run_command (zsh, in this folder) - e.g. to run tests, scripts or "
    "builds and then fix what fails. They run in a sandbox: no internet, writing only inside this folder. "
    "Servers and other programs that keep running (npm start, a development server, anything that listens on a "
    "port) cannot run there - do not try them; tell the user to start them in another terminal window, and "
    "run a build command that finishes instead. "
    "Never use commands to delete or overwrite files unless the user asked for exactly that; change files with "
    "edit_file / write_file, which keep a backup. "
    "IMPORTANT: The writing, command and internet tools ask the user for confirmation themselves. "
    "So NEVER ask in the chat 'Shall I …?', but call the right tool right away "
    "as soon as the user wants something written, changed, moved or run. "
    "Private data (hidden settings and keys in the home folder, ~/Library, key files) is blocked; only access it "
    "when the user explicitly asks for it - the user is then asked to allow it."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "Lists the files and subfolders of a folder.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path, '.' for the start folder"},
            "show_private": {"type": "boolean", "description": "Also list private items (hidden settings, keys, "
                             "~/Library). Only when the user explicitly asks; the user must allow it."}}}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Reads a file: text, PDF, Word (docx/doc/rtf/odt) or Excel (xlsx). Scanned PDFs and images are read via text recognition.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path to the file"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search",
        "description": "Searches recursively for text (regular expression, case-insensitive) in text, PDF, Word and Excel files (not in scans).",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Relative start folder, default '.'"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Creates a new text file or replaces its entire content. "
                       "For small changes to existing files use edit_file. The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Complete new file content"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replaces a passage in a text file with new text. old_text must occur exactly and exactly "
                       "once in the file. The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "old_text": {"type": "string", "description": "Exact current text (with enough context to be unique)"},
            "new_text": {"type": "string", "description": "New text that replaces old_text"}},
            "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "write_docx",
        "description": "Creates a Word document (.docx). Content as simple Markdown: '# ' / '## ' / '### ' "
                       "for headings, '- ' for bullet points, **bold**, *italic*, blank line for a new paragraph. "
                       "The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path, ending in .docx"},
            "content": {"type": "string", "description": "Document content as simple Markdown"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "write_pdf",
        "description": "Creates a PDF document (A4). Content as simple Markdown like with write_docx. "
                       "The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path, ending in .pdf"},
            "content": {"type": "string", "description": "Document content as simple Markdown"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "move_file",
        "description": "Renames or moves a file or folder (also into new subfolders). "
                       "Never overwrites anything. The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "source": {"type": "string", "description": "Relative current path"},
            "destination": {"type": "string", "description": "Relative new path (or an existing target folder)"}},
            "required": ["source", "destination"]}}},
    {"type": "function", "function": {
        "name": "move_files",
        "description": "Renames or moves several files or folders in one step (e.g. renaming all photos). Never "
                       "overwrites anything. The user confirms the whole list once; /undo reverts all of it.",
        "parameters": {"type": "object", "properties": {
            "moves": {"type": "array", "items": {"type": "object", "properties": {
                "source": {"type": "string", "description": "Relative current path"},
                "destination": {"type": "string", "description": "Relative new path (or an existing target folder)"}},
                "required": ["source", "destination"]}}},
            "required": ["moves"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Runs a terminal command (zsh) in the start folder and returns its output and exit code - "
                       "e.g. tests, scripts, builds, git status. Runs in a sandbox: no internet, it can only write "
                       "inside the folder, no private data. Not interactive (no input), not for servers or other programs "
                       "that keep running. The user confirms first.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The command line, e.g. 'python3 -m unittest'"},
            "timeout": {"type": "integer", "description": "Seconds until the command is stopped, default 120, max 600"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "view_image",
        "description": "Shows you an image from the folder (jpg, png, gif, webp, heic, …) so you can describe or analyze it.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path to the image"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Searches the internet (DuckDuckGo) and returns title, address and snippet of the results. "
                       "For details, use fetch_url on a result afterwards.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search terms"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Loads a web page (http/https) and returns its text.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}},
            "required": ["url"]}}},
]


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------- reading ----------

HOME = os.path.realpath(os.path.expanduser("~"))
# private data: everything hidden directly in the home folder (~/.ssh, ~/.zshrc, ~/.config, … - settings, keys,
# tokens, history) and ~/Library (keychains, browser data, mail, messages). Blocked even when Flashcat is started in
# the home folder itself, unless it was started inside one of them on purpose.
PRIVATE_NAME = re.compile(r"(id_(rsa|dsa|ecdsa|ed25519)(_sk)?|.*\.(pem|p12|pfx|keychain|keychain-db)|\.netrc|"
                          r"\.git-credentials)", re.IGNORECASE)  # private keys and credential files, wherever they are


def inside(path, folder):
    return path == folder or path.startswith(folder + os.sep)


private_ok = set()  # private items the user unlocked in this chat
TTY_ANSWERS = False  # set when stdin carries piped data (one-shot mode): answers then come from the terminal


def ask(prompt):
    """The user's answer to a question. With piped input the answer is read from the terminal; without a terminal
    there is no answer, which every question treats as No."""
    if sys.stdin.isatty() or not TTY_ANSWERS:
        return input(prompt)
    try:
        with open("/dev/tty", "r+") as t:
            t.write(prompt)
            t.flush()
            return t.readline().rstrip("\n")
    except OSError:
        return ""


def private_item(full):
    """The private item `full` belongs to - a key file, or the hidden file/folder in ~ or ~/Library - or None."""
    if PRIVATE_NAME.fullmatch(os.path.basename(full)):
        return full
    # compared in lower case: macOS ignores case in file names, so ~/LIBRARY is ~/Library
    low, home = full.lower(), HOME.lower()
    if low == home or not inside(low, home):
        return None
    name = full[len(HOME) + 1:].split(os.sep)[0]
    top = os.path.join(HOME, name)
    private = name.startswith(".") or name.lower() == "library"
    return top if private and not inside(ROOT.lower(), top.lower()) else None


def locked(full):
    item = private_item(full)
    return item is not None and item not in private_ok


def confirm_private(item, what="Allow access for this chat?"):
    """Red card: asks the user to unlock a private item for the rest of this chat."""
    ui_break()
    print()
    print(card(f"{RED}! Private data{RESET}", [
        f"{RED}{clean(item.replace(HOME, '~', 1))}{RESET}",
        f"{RED}Can contain keys, passwords, tokens or private messages.{RESET}",
        f"{RED}Only allow it if you asked for it yourself –{RESET}",
        f"{RED}a document or web page could have tricked the model.{RESET}"], color=RED))
    try:
        answer = ask(f"  {RED}? {what} [Y/N]{RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return answer in ("y", "yes")


PRIVATE_REFUSED = ("Refused: this is private data (keys, passwords, history, ~/Library). It can only be accessed "
                   "when the user asks for it and allows it.")


def resolve(path, ask=False):
    """Absolute path inside the start folder. Private data is refused unless unlocked; with ask=True (a tool or the
    user accesses exactly this path) the user is asked to unlock it."""
    full = os.path.realpath(os.path.join(ROOT, path or "."))
    if not inside(full, ROOT):
        raise ValueError("Access outside the start folder is not allowed.")
    if locked(full):
        if not ask:
            raise ValueError(PRIVATE_REFUSED)
        if not confirm_private(private_item(full)):
            raise ValueError("Refused: Flashcat asked the user whether you may access this private data and "
                             "the user answered No. Say briefly that you did not open it, and do not ask again.")
        private_ok.add(private_item(full))
        print(f"  {RED}✓ {clean(private_item(full).replace(HOME, '~', 1))} unlocked for this chat{RESET}")
    return full


def allowed(full):
    """True if `full` (a path found while walking a folder) may be accessed without asking."""
    try:
        resolve(full)
        return True
    except ValueError:
        return False


def list_dir(path=".", show_private=False):
    full = resolve(path, ask=True)
    entries = sorted(os.listdir(full), key=lambda n: (not os.path.isdir(os.path.join(full, n)), n.lower()))
    hidden = [n for n in entries if not allowed(os.path.join(full, n))]
    private = [n for n in hidden if inside(os.path.realpath(os.path.join(full, n)), ROOT)]  # not links outside
    if show_private and private:
        rel = os.path.relpath(full, ROOT)
        if confirm_private(f"{plural(len(private), 'private item')} in {'the start folder' if rel == '.' else rel}",
                           "Show their names (not their contents)?"):
            hidden = [n for n in hidden if n not in private]
        else:
            return "The user declined showing the private items."
    lines = []
    for name in entries:
        if name in hidden:
            continue
        if name in private:
            lines.append(f"{name}{'/' if os.path.isdir(os.path.join(full, name)) else ''}  (private – opening it asks the user)")
            continue
        p = os.path.join(full, name)
        if os.path.isdir(p):
            lines.append(name + "/")
        else:
            try:
                lines.append(f"{name}  ({plural(os.path.getsize(p), 'byte')})")
            except OSError:
                lines.append(name)
    if hidden:
        lines.append(f"({plural(len(hidden), 'item')} not shown: private data or links outside the folder. "
                     "Only if the user asks for them: list_dir with show_private=true.)")
    return "\n".join(lines) or "(empty)"


PDF_JXA = (
    'function run(a){ObjC.import("PDFKit");'
    'var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(a[0]));'
    'return d.isNil()?"":d.string.js}'
)


# macOS text recognition (Vision framework); PDFs are rendered page by page first
OCR_JXA = """
function recognize(cgimage, url) {
  var handler = url ? $.VNImageRequestHandler.alloc.initWithURLOptions(url, $.NSDictionary.dictionary)
                    : $.VNImageRequestHandler.alloc.initWithCGImageOptions(cgimage, $.NSDictionary.dictionary);
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;
  req.usesLanguageCorrection = true;
  // the user's languages (from macOS settings) that text recognition supports, English as fallback
  var supported = ObjC.deepUnwrap(req.supportedRecognitionLanguagesAndReturnError($())) || [];
  var wanted = ObjC.deepUnwrap($.NSLocale.preferredLanguages).concat(["en-US"]), langs = [];
  wanted.forEach(function (w) {
    supported.forEach(function (s) {
      if (s.split("-")[0] == w.split("-")[0] && langs.indexOf(s) < 0) langs.push(s);
    });
  });
  req.recognitionLanguages = $(langs.length ? langs : ["en-US"]);
  handler.performRequestsError($([req]), $());
  var out = [], res = req.results;
  for (var i = 0; i < res.count; i++) out.push(res.objectAtIndex(i).topCandidates(1).objectAtIndex(0).string.js);
  return out.join("\\n");
}
function run(a) {
  ObjC.import("Vision"); ObjC.import("PDFKit"); ObjC.import("AppKit");
  var path = a[0], maxPages = parseInt(a[1]);
  if (!path.toLowerCase().endsWith(".pdf")) return recognize(null, $.NSURL.fileURLWithPath(path));
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(path));
  if (doc.isNil()) return "";
  var pages = [];
  for (var p = 0; p < Math.min(doc.pageCount, maxPages); p++) {
    var page = doc.pageAtIndex(p), box = page.boundsForBox(0);
    var scale = 2000 / Math.max(box.size.width, box.size.height);
    var img = page.thumbnailOfSizeForBox($.NSMakeSize(box.size.width * scale, box.size.height * scale), 0);
    pages.push("--- Page " + (p + 1) + " ---\\n" + recognize(img.CGImageForProposedRectContextHints($(), $(), $())));
  }
  if (doc.pageCount > maxPages) pages.push("… (only the first " + maxPages + " of " + doc.pageCount + " pages recognized)");
  return pages.join("\\n");
}
"""
OCR_MAX_PAGES = 20


def ocr(full):
    """Recognizes text in a scanned PDF or an image using macOS' built-in text recognition."""
    ui_break()
    print(f"    {DIM}↳ text recognition (scan) – the first time after a restart may take up to 30 s …{RESET}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        target = full
        if not full.lower().endswith(".pdf"):
            # JPEG flattens transparency, which otherwise hides dark text on a transparent background
            target = os.path.join(tmp, "image.jpg")
            subprocess.run(["sips", "-s", "format", "jpeg", full, "--out", target],
                           capture_output=True, timeout=60, check=True)
        return subprocess.run(["osascript", "-l", "JavaScript", "-e", OCR_JXA, target, str(OCR_MAX_PAGES)],
                              capture_output=True, text=True, timeout=600).stdout.strip()


XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def excel_date(serial):
    """Excel serial number (days since 1899-12-30) as an ISO date, with time if present."""
    try:
        d = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(serial))
    except (ValueError, OverflowError):
        return serial
    return d.strftime("%Y-%m-%d %H:%M" if d.hour or d.minute else "%Y-%m-%d")


def read_xlsx(full, max_rows=3000):
    """Excel workbook as plain text: one block per sheet, one line per row, cells separated by ' | '."""
    def col_index(ref):
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group():
            n = n * 26 + ord(ch) - 64
        return n - 1

    with zipfile.ZipFile(full) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(XLSX_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(XLSX_NS + "t")))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        date_styles = set()  # cell style indexes whose number format is a date
        if "xl/styles.xml" in z.namelist():
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom = {int(n.get("numFmtId")): n.get("formatCode", "") for n in styles.iter(XLSX_NS + "numFmt")}
            xfs = styles.find(XLSX_NS + "cellXfs")
            for idx, xf in enumerate(xfs if xfs is not None else []):
                fid = int(xf.get("numFmtId", 0))
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fid, ""))
                if 14 <= fid <= 22 or 45 <= fid <= 47 or (fid in custom and re.search(r"[dy]", code, re.I)):
                    date_styles.add(idx)
        out = []
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter(XLSX_NS + "sheet"):
            target = rels.get(sheet.get(XLSX_REL), "")
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            out.append(f"=== Sheet: {sheet.get('name')} ===")
            rows = 0
            for row in ET.fromstring(z.read(path)).iter(XLSX_NS + "row"):
                cells = {}
                for c in row.findall(XLSX_NS + "c"):
                    v, kind = c.find(XLSX_NS + "v"), c.get("t")
                    if kind == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif kind == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(XLSX_NS + "t"))
                    elif kind == "b":
                        val = "TRUE" if v is not None and v.text == "1" else "FALSE"
                    else:
                        val = v.text if v is not None and v.text else ""
                        if val and int(c.get("s", -1)) in date_styles:
                            val = excel_date(val)
                    if val != "":
                        cells[col_index(c.get("r", "A"))] = val.replace("\n", " ")
                if cells:
                    out.append(f"Row {row.get('r')}: " + " | ".join(cells.get(i, "") for i in range(max(cells) + 1)))
                    rows += 1
                    if rows >= max_rows:
                        out.append(f"… (only the first {max_rows} rows)")
                        break
    return "\n".join(out)


def extract_text(full, allow_ocr=True):
    """Text of PDF/Word/Excel files (and images via text recognition), or None for other files."""
    ext = os.path.splitext(full)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(full)
    if ext in IMAGE_EXT:
        return ocr(full) if allow_ocr else None
    if ext == ".pdf":
        cmd = ["osascript", "-l", "JavaScript", "-e", PDF_JXA, full]
    elif ext in TEXTUTIL_EXT:
        cmd = ["textutil", "-convert", "txt", "-stdout", full]
    else:
        return None
    text = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()
    if ext == ".pdf" and not text and allow_ocr:
        text = ocr(full)  # no text layer: probably a scan
    return text


def read_file(path):
    full = resolve(path, ask=True)
    text = extract_text(full)
    if text is not None:
        if not text:
            return "No text was found in this file (not even with text recognition)."
        if len(text) > MAX_READ:
            text = text[:MAX_READ] + f"\n… (shortened, only the first {MAX_READ} characters)"
        return text
    with open(full, "rb") as f:
        data = f.read(MAX_READ + 1)
    if b"\0" in data[:4096]:
        return "This is not a text file."
    text = data[:MAX_READ].decode("utf-8", errors="replace")
    if len(data) > MAX_READ:
        text += f"\n… (shortened, only the first {MAX_READ} bytes)"
    return text


def search(pattern, path="."):
    rx = re.compile(pattern, re.IGNORECASE)
    hits, docs = [], 0
    for dirpath, dirnames, filenames in os.walk(resolve(path, ask=True)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                       and allowed(os.path.join(dirpath, d))]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if not allowed(p):  # private data, or a link to a file outside the folder
                continue
            ext = os.path.splitext(name)[1].lower()
            label = ""
            try:
                if ext in {".pdf", ".xlsx", ".xlsm"} | (TEXTUTIL_EXT - {".html", ".htm"}):
                    if docs >= MAX_DOCS_SEARCHED:
                        continue
                    docs += 1
                    text = extract_text(p, allow_ocr=False) or ""
                    label = {".pdf": " (PDF)", ".xlsx": " (Excel)", ".xlsm": " (Excel)"}.get(ext, " (Word)")
                elif ext in IMAGE_EXT:
                    continue
                else:
                    with open(p, "rb") as f:
                        data = f.read(2_000_000)
                    if b"\0" in data[:4096]:
                        continue
                    text = data.decode("utf-8", errors="replace")
            except (OSError, subprocess.SubprocessError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{os.path.relpath(p, ROOT)}:{i}{label}: {line.strip()[:200]}")
                    if len(hits) >= MAX_HITS:
                        return "\n".join(hits) + "\n… (more matches cut off)"
    return "\n".join(hits) or "No matches."


# ---------- changing files (confirmed, backed up, undoable) ----------

journal = []  # changes made in this chat, newest last; used by /undo


def in_backup(rel):
    return rel.split(os.sep)[0].lower() == BACKUP_DIR  # lower case: macOS ignores case in file names


def check_target(path, allowed_ext):
    """Returns (full, rel) for a file that may be written, or raises ValueError with a refusal reason."""
    full = resolve(path, ask=True)
    rel = os.path.relpath(full, ROOT)
    if os.path.splitext(full)[1].lower() not in allowed_ext:
        raise ValueError(f"Refused: only {', '.join(sorted(allowed_ext))} are allowed.")
    if in_backup(rel):
        raise ValueError("Refused: the backup folder is off limits.")
    if os.path.isdir(full):
        raise ValueError("Refused: this is a folder.")
    if os.path.exists(full) and os.stat(full).st_nlink > 1:
        # a hard link shares its content with a file that may live outside the start folder
        raise ValueError("Refused: the file is a hard link and could change a file outside the folder.")
    return full, rel


def confirm(question, show_all=None):
    """Y/N question. With `show_all` (a function), A shows the complete preview first and asks again."""
    ui_break()
    choices = "[Y/N/A = show all]" if show_all else "[Y/N]"
    try:
        answer = ask(f"  {ORANGE}?{RESET} {question} {DIM}{choices}{RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if show_all and answer in ("a", "all"):
        show_all()
        return confirm(question)
    return answer in ("y", "yes")


def backup_copy(full, rel, suffix="bak", move=False):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(ROOT, BACKUP_DIR, f"{rel}.{stamp}.{suffix}")
    folder = os.path.dirname(backup)
    if os.path.realpath(folder) != os.path.normpath(folder):
        # e.g. a downloaded project that ships .flashcat-backup as a link to another folder
        raise ValueError(f"Refused: {BACKUP_DIR} contains a symbolic link.")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(ROOT, BACKUP_DIR, ".gitignore")
    if not os.path.lexists(ignore):  # backups may hold private old versions: never commit them by accident
        with open(ignore, "w") as f:
            f.write("*\n")
    (shutil.move if move else shutil.copy2)(full, backup)
    return backup


def save_with_backup(full, rel, content=None, source_file=None):
    """Writes text `content` (or copies `source_file`) to `full`, backing up any previous version."""
    backup = backup_copy(full, rel) if os.path.exists(full) else None
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if source_file:
        shutil.copyfile(source_file, full)
    else:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    journal.append({"type": "write", "full": full, "rel": rel, "backup": backup})
    (stats["changed"] if backup else stats["created"]).add(rel)
    note = f" Backup of the old version: {os.path.relpath(backup, ROOT)}" if backup else ""
    ui_break()
    print(f"  {GREEN}✓{RESET} {DIM}{'changed' if backup else 'created'}:{RESET} {file_link(rel)}"
          f"{DIM}{'  · backup in ' + BACKUP_DIR if backup else ''}{RESET}")
    return f"Written: {rel}.{note}"


def preview(title, lines, limit=20):
    """Card with line numbers and a green bar: what a new file / document will contain.

    Returns a function that shows the complete preview if lines were cut off (for confirm's A), else None."""
    ui_break()
    width = term_width() - 14
    body = [f"{GREEN}▌{RESET}{DIM}{n:>4}{RESET}  {clean(line)[:width]}" for n, line in enumerate(lines[:limit], 1)]
    cut = limit is not None and len(lines) > limit
    if cut:
        body.append(f"{DIM}      … {plural(len(lines) - limit, 'more line')} (A shows all){RESET}")
    print()
    print(card(f"{ORANGE}{clean(title)}{RESET}", body or [f"{DIM}(empty){RESET}"]))
    return (lambda: preview(title, lines, limit=None)) if cut else None


def diff_card(title, old, new, context=2, limit=40):
    """Card showing a change with old/new line numbers and red/green bars.

    Returns a function that shows the complete change if lines were cut off (for confirm's A), else None."""
    ui_break()
    a, b = clean(old).splitlines(), clean(new).splitlines()
    width = term_width() - 16
    body = []
    for group in difflib.SequenceMatcher(None, a, b).get_grouped_opcodes(context):
        if body:
            body.append(f"{DIM}      ⋯{RESET}")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                body += [f"{DIM} {i + 1:>4}  {a[i][:width]}{RESET}" for i in range(i1, i2)]
                continue
            body += [f"{RED}▌{RESET}{DIM}{i + 1:>4}{RESET}  {RED}{a[i][:width]}{RESET}" for i in range(i1, i2)]
            body += [f"{GREEN}▌{RESET}{DIM}{j + 1:>4}{RESET}  {GREEN}{b[j][:width]}{RESET}" for j in range(j1, j2)]
    cut = limit is not None and len(body) > limit
    if cut:
        body = body[:limit] + [f"{DIM}      … {plural(len(body) - limit, 'more line')} (A shows all){RESET}"]
    print()
    print(card(f"{ORANGE}{clean(title)}{RESET}", body or [f"{DIM}(no change){RESET}"]))
    return (lambda: diff_card(title, old, new, context, limit=None)) if cut else None


def write_file(path, content):
    full, rel = check_target(path, WRITE_EXT)
    if content and not content.endswith("\n"):
        content += "\n"  # text files end with a line break (the model often leaves it out)
    lines = content.splitlines()
    if os.path.exists(full):
        with open(full, encoding="utf-8", errors="replace") as f:
            show_all = diff_card(f"overwrites {rel}", f.read(), content)
    else:
        show_all = preview(f"new file {rel} · {plural(len(lines), 'line')}", lines)
    if not confirm("Write?", show_all):
        print()
        return "The user declined writing. The file was not changed."
    return save_with_backup(full, rel, content)


def edit_file(path, old_text, new_text):
    full, rel = check_target(path, WRITE_EXT)
    if not os.path.exists(full):
        return "Error: the file does not exist. Use write_file to create it."
    with open(full, encoding="utf-8", errors="replace") as f:
        old = f.read()
    count = old.count(old_text) if old_text else 0
    if count == 0:
        return "Error: old_text does not occur in the file. Read the file again and give the text exactly."
    if count > 1:
        return f"Error: old_text occurs {count} times. Give more surrounding text so the passage is unique."
    new = old.replace(old_text, new_text, 1)
    show_all = diff_card(f"changes {rel}", old, new)
    if not confirm("Change?", show_all):
        print()
        return "The user declined the change. The file was not changed."
    return save_with_backup(full, rel, new)


def markdown_to_html(md, plain_lists=False):
    def inline(s):
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        return re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", s)

    out, in_list, para = [], False, []

    def flush():
        nonlocal para
        if para:
            out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
            para = []

    for line in md.splitlines():
        s = line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)", s)
        item = re.match(r"^[-*•]\s+(.*)", s)
        if heading or item or not s:
            flush()
        if in_list and not item:
            out.append("</ul>")
            in_list = False
        if heading:
            n = len(heading.group(1))
            out.append(f"<h{n}>{inline(heading.group(2))}</h{n}>")
        elif item and plain_lists:
            out.append(f"<p style=\"margin:0 0 2pt 18pt\">•&nbsp;&nbsp;{inline(item.group(1))}</p>")
        elif item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(item.group(1))}</li>")
        elif s:
            para.append(s)
    flush()
    if in_list:
        out.append("</ul>")
    return ('<html><head><meta charset="utf-8"><style>body{font-family:Helvetica;font-size:11pt} p{margin:0 0 8pt 0}'
            "</style></head><body>" + "\n".join(out) + "</body></html>")


def write_docx(path, content):
    full, rel = check_target(path, {".docx"})
    replaces = " (replaces the existing one)" if os.path.exists(full) else ""
    show_all = preview(f"Word document {rel}{replaces}", content.splitlines())
    if not confirm("Create?", show_all):
        print()
        return "The user declined. No document was created."
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "doc.html"), os.path.join(tmp, "doc.docx")
        with open(src, "w", encoding="utf-8") as f:
            f.write(markdown_to_html(content))
        subprocess.run(["textutil", "-convert", "docx", src, "-output", out],
                       capture_output=True, timeout=60, check=True)
        return save_with_backup(full, rel, source_file=out)


HTML2PDF_JXA = """
function run(a) {
  ObjC.import("AppKit");
  var html = $.NSData.dataWithContentsOfFile(a[0]);
  var opts = $.NSDictionary.dictionaryWithObjectsForKeys(
    [$.NSHTMLTextDocumentType, $.NSNumber.numberWithInt(4)],
    [$.NSDocumentTypeDocumentAttribute, $.NSCharacterEncodingDocumentAttribute]);
  var str = $.NSAttributedString.alloc.initWithDataOptionsDocumentAttributesError(html, opts, $(), $());
  var info = $.NSPrintInfo.sharedPrintInfo.copy;
  info.paperSize = $.NSMakeSize(595, 842);
  info.topMargin = 56; info.bottomMargin = 56; info.leftMargin = 56; info.rightMargin = 56;
  info.horizontalPagination = $.NSFitPagination;
  info.verticalPagination = $.NSAutoPagination;
  info.jobDisposition = $.NSPrintSaveJob;
  info.dictionary.setObjectForKey($.NSURL.fileURLWithPath(a[1]), $.NSPrintJobSavingURL);
  var view = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 595 - 112, 842));
  view.textStorage.setAttributedString(str);
  view.sizeToFit;
  var op = $.NSPrintOperation.printOperationWithViewPrintInfo(view, info);
  op.showsPrintPanel = false; op.showsProgressPanel = false;
  return op.runOperation ? "ok" : "fail";
}
"""


def write_pdf(path, content):
    full, rel = check_target(path, {".pdf"})
    replaces = " (replaces the existing one)" if os.path.exists(full) else ""
    show_all = preview(f"PDF {rel}{replaces}", content.splitlines())
    if not confirm("Create?", show_all):
        print()
        return "The user declined. No PDF was created."
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "doc.html"), os.path.join(tmp, "doc.pdf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(markdown_to_html(content, plain_lists=True))
        subprocess.run(["osascript", "-l", "JavaScript", "-e", HTML2PDF_JXA, src, out],
                       capture_output=True, timeout=120, check=True)
        if not os.path.exists(out):
            return "Error: the PDF could not be created."
        return save_with_backup(full, rel, source_file=out)


def plan_move(source, destination):
    """Checks one move. Returns (src, dst, src_rel, dst_rel) or raises ValueError with the reason."""
    src = resolve(source, ask=True)
    if src == ROOT:
        raise ValueError("Refused: the start folder itself cannot be moved.")
    if not os.path.lexists(src):
        raise ValueError(f"Error: {clean(source)} does not exist.")
    dst = resolve(destination, ask=True)
    if os.path.isdir(dst) or destination.endswith("/"):  # "folder/" means into that folder, even a new one
        dst = os.path.join(dst, os.path.basename(src))
    src_rel, dst_rel = clean(os.path.relpath(src, ROOT)), clean(os.path.relpath(dst, ROOT))
    if in_backup(src_rel) or in_backup(dst_rel):
        raise ValueError("Refused: the backup folder is off limits.")
    if os.path.lexists(dst):
        raise ValueError(f"Refused: {dst_rel} already exists – nothing is overwritten.")
    if dst.startswith(src + os.sep):
        raise ValueError("Refused: a folder cannot be moved into itself.")
    return src, dst, src_rel, dst_rel


def do_move(src, dst, src_rel, dst_rel):
    if os.path.lexists(dst):  # appeared since the check: never overwrite or move into it
        raise ValueError(f"Refused: {dst_rel} already exists – nothing is overwritten.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    stats["moved"] += 1
    return {"src": src, "dst": dst, "src_rel": src_rel, "dst_rel": dst_rel}


def move_file(source, destination):
    try:
        src, dst, src_rel, dst_rel = plan_move(source, destination)
    except ValueError as e:
        return str(e)
    rename = os.path.dirname(src) == os.path.dirname(dst)
    ui_break()
    print()
    print(card(f"{ORANGE}{'renames' if rename else 'moves'}{RESET}", [f"{src_rel}  {DIM}→{RESET}  {dst_rel}"]))
    if not confirm("Rename?" if rename else "Move?"):
        print()
        return "The user declined. Nothing was moved."
    journal.append({"type": "move", **do_move(src, dst, src_rel, dst_rel)})
    print(f"  {GREEN}✓{RESET} {DIM}moved:{RESET} {src_rel} {DIM}→{RESET} {file_link(dst_rel)}")
    return f"Moved: {src_rel} → {dst_rel}"


MAX_MOVES = 500


def move_files(moves):
    """Several moves, checked completely first, confirmed once and undone together."""
    if not isinstance(moves, list) or not moves:
        return "Error: moves must be a non-empty list of {source, destination}."
    if len(moves) > MAX_MOVES:
        return f"Refused: at most {MAX_MOVES} moves at once."
    plan, sources, targets = [], set(), set()
    for m in moves:
        if not isinstance(m, dict):
            return "Error: every entry needs source and destination."
        try:
            src, dst, src_rel, dst_rel = plan_move(str(m.get("source") or ""), str(m.get("destination") or ""))
        except ValueError as e:
            return f"{e} (in the entry {clean(str(m.get('source')))}; nothing was moved)"
        # compared in lower case: macOS ignores case, so two targets differing only in case are the same file
        if src.lower() in sources or dst.lower() in targets or dst.lower() in sources or src.lower() in targets:
            return (f"Refused: {src_rel} → {dst_rel} collides with another entry of the list (same source or target, "
                    "or a chain of moves). Nothing was moved.")
        if any(inside(dst, p[0]) or inside(p[1], src) or inside(dst, p[1]) or inside(p[1], dst) for p in plan):
            return f"Refused: {src_rel} → {dst_rel} moves into or out of another moved folder. Nothing was moved."
        sources.add(src.lower())
        targets.add(dst.lower())
        plan.append((src, dst, src_rel, dst_rel))
    lines = [f"{src_rel}  {DIM}→{RESET}  {dst_rel}" for _, _, src_rel, dst_rel in plan]
    title = f"moves {plural(len(plan), 'item')}"

    def show(limit=20):
        ui_break()
        body = lines[:limit] if limit else lines
        if limit and len(lines) > limit:
            body = body + [f"{DIM}… {plural(len(lines) - limit, 'more line')} (A shows all){RESET}"]
        print()
        print(card(f"{ORANGE}{title}{RESET}", body))

    show()
    if not confirm(f"Move {plural(len(plan), 'item')}?", (lambda: show(None)) if len(lines) > 20 else None):
        print()
        return "The user declined. Nothing was moved."
    done = []
    try:
        for step in plan:
            done.append(do_move(*step))
    finally:
        if done:
            journal.append({"type": "batch", "moves": done})
    print(f"  {GREEN}✓{RESET} {DIM}moved:{RESET} {plural(len(done), 'item')}")
    return f"Moved {plural(len(done), 'item')}:\n" + "\n".join(f"{d['src_rel']} → {d['dst_rel']}" for d in done)


def describe(e):
    """What undoing journal entry `e` does, in words."""
    if e["type"] == "move":
        return f"move {e['dst_rel']} back to {e['src_rel']}"
    if e["type"] == "batch":
        return f"move {plural(len(e['moves']), 'item')} back ({e['moves'][0]['dst_rel']} → {e['moves'][0]['src_rel']}, …)"
    if e["backup"]:
        return f"restore {clean(e['rel'])} to the version before the change"
    return f"remove the newly created file {clean(e['rel'])} (a copy goes to {BACKUP_DIR})"


def undo():
    """Reverts the newest change of this chat after confirmation. Returns a description or None."""
    if not journal:
        print("Nothing has been changed in this chat that could be undone.\n")
        return None
    e = journal[-1]
    desc = describe(e)
    if not confirm(f"Undo: {desc}?"):
        print()
        return None
    if e["type"] in ("move", "batch"):
        moves = e["moves"] if e["type"] == "batch" else [e]
        blocked = [m["src_rel"] for m in moves if os.path.lexists(m["src"]) or not os.path.lexists(m["dst"])]
        if blocked:
            print(f"Not possible: {blocked[0]} exists again by now (or its moved version is gone).\n")
            return None
        for m in reversed(moves):
            os.makedirs(os.path.dirname(m["src"]), exist_ok=True)
            shutil.move(m["dst"], m["src"])
    elif e["backup"]:
        if os.path.exists(e["full"]):
            backup_copy(e["full"], e["rel"], "before-undo")
        shutil.copy2(e["backup"], e["full"])
    elif os.path.exists(e["full"]):
        backup_copy(e["full"], e["rel"], "removed", move=True)
    journal.pop()
    stats["undone"] += 1
    print(f"  {GREEN}✓{RESET} {DIM}undone:{RESET} {desc}\n")
    return desc


def show_journal():
    if not journal:
        print("Nothing has been changed in this chat that could be undone.\n")
        return
    for i, e in enumerate(reversed(journal), 1):
        print(f"  {i:>2}. {describe(e)}{DIM}{'  ◀ next /undo' if i == 1 else ''}{RESET}")
    print(f"{DIM}  /undo reverts them one by one, newest first{RESET}\n")


# ---------- commands (confirmed, sandboxed) ----------

RUN_TIMEOUT, RUN_MAX_TIMEOUT = 120, 600
# a command that tried to listen on a port, e.g. Node: "listen EPERM: operation not permitted 0.0.0.0:8080",
# Go: "listen tcp :8080: bind: operation not permitted", Python: "server_bind … Operation not permitted"
SERVER_BLOCKED = re.compile(r"\blisten\b[^\n]*(EPERM|EACCES|not permitted)|\bbind\b[^\n]*not permitted|"
                            r"server_bind[\s\S]{0,400}not permitted", re.IGNORECASE)
MAX_OUTPUT = 20_000
MAX_COMMAND = 2000
# developer tools installed in the home folder that commands may read (not private: programs, no secrets)
TOOLCHAINS = [".local/bin", ".local/lib", ".local/pipx", ".cargo", ".rustup", ".nvm", ".pyenv", ".rbenv", ".volta",
              ".bun", ".deno", ".sdkman", ".gitconfig", ".config/git", "go", "Library/Python"]
DESTRUCTIVE = re.compile(r"(^|[\s;&|(`])(rm|rmdir|mv|dd|truncate|shred|unlink|find\s.*-delete|"
                         r"git\s+(reset|clean|checkout|restore|stash|rebase|push\s+-f))\b|(^|[^-=>&0-9])>(?![&>]|\s*/dev/null)")


def sb_string(path):
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sb_regex(path):
    """`path` as a literal inside a sandbox regular expression."""
    return re.sub(r'([.^$*+?()\[\]{}|\\"])', r"\\\1", path)


def any_case(text):
    """Regular expression matching `text` in upper or lower case (macOS ignores case in file names)."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else sb_regex(c) for c in text)


def sandbox_profile(own_tmp):
    """macOS sandbox for run_command: no network, writing only in the start folder (not its backups) and the
    command's own temporary folder `own_tmp`, no reading of user files outside the start folder (home folders, other
    users, external drives, other apps' temporary files - system files and developer tools stay readable), private
    data and key files blocked like for the other tools, no opening apps or URLs, no clipboard, no keychain."""
    home, root = HOME, ROOT
    private = f'(regex #"^{sb_regex(home)}/\\.") (subpath {sb_string(os.path.join(home, "Library"))})'
    top = os.path.relpath(root, home).split(os.sep)[0] if inside(root.lower(), home.lower()) else ""
    root_private = top.startswith(".") and top != "." or top.lower() == "library"  # started inside e.g. ~/.config
    keys = ("(id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]*\\.(" + "|".join(any_case(e) for e in
            ("pem", "p12", "pfx", "keychain", "keychain-db")) + ")|" + any_case(".netrc") + "|" +
            any_case(".git-credentials") + ")")
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny lsopen)",
        "(deny appleevent-send)",
        '(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd") '
        '(global-name "com.apple.coreservices.quarantine-resolver") (global-name "com.apple.pasteboard.1") '
        '(global-name "com.apple.SecurityServer") (global-name "com.apple.securityd.xpc") '
        '(global-name "com.apple.secd") (global-name-regex #"^com\\.apple\\.nsurlsessiond"))',
        # reading: no user files except the start folder and developer tools; no temporary folders of other apps
        # (they can hold private data: images, documents, caches). Later rules win, so the order matters.
        f"(deny file-read-data (subpath {sb_string(home)}) (subpath \"/Users\") (subpath \"/Volumes\") "
        '(subpath "/private/var/folders") (subpath "/private/tmp") (subpath "/private/var/tmp"))',
        f"(allow file-read-data (subpath {sb_string(root)}))",
        f"(deny file-read-data {private})",
        "(allow file-read-data " + " ".join(f"(subpath {sb_string(os.path.join(home, t))})" for t in TOOLCHAINS) + ")",
        # writing: only the start folder and the command's own temporary folder, never private data or the backups
        "(deny file-write*)",
        f"(allow file-write* (subpath {sb_string(root)}) "
        + ' (literal "/dev/null") (literal "/dev/zero") (regex #"^/dev/tty") (regex #"^/dev/fd/"))',
        f"(allow file-read-data file-write* (subpath {sb_string(own_tmp)}) "
        # caches of Apple's developer tools (xcrun, compilers) - tool paths and compiled modules, nothing private
        '(regex #"^/private/var/folders/[^/]+/[^/]+/T/xcrun_db(-|$)") '
        '(regex #"^/private/var/folders/[^/]+/[^/]+/C/(com\\.apple\\.dt\\.|clang|org\\.llvm|org\\.swift|com\\.apple\\.DeveloperTools)"))',
        f"(deny file-write* {private})",
    ]
    if root_private:  # started there on purpose: the start folder itself stays usable
        rules.append(f"(allow file-read-data file-write* (subpath {sb_string(root)}))")
    rules += [
        f'(deny file-read-data file-write* (regex #"/{keys}$"))',
        f"(deny file-write* (subpath {sb_string(os.path.join(root, BACKUP_DIR))}))",
    ]
    return "\n".join(rules)


def command_env(own_tmp, run_id):
    env = dict(os.environ)
    env.pop("FLASHCAT_API_KEY", None)
    env.update(PAGER="cat", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", NO_COLOR="1", TERM="dumb",
               TMPDIR=own_tmp + "/", FLASHCAT_RUN=run_id, HOME=HOME)
    return env


def stop_leftovers(proc, run_id):
    """Stops what a command left running: its process group, and processes that detached from it (found by the
    FLASHCAT_RUN mark in their environment) - they could otherwise go on changing the folder unseen."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        ps = subprocess.run(["ps", "-Aww", "-E", "-o", "pid=,command="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return
    for line in ps.stdout.splitlines():
        pid, _, rest = line.strip().partition(" ")
        if f"FLASHCAT_RUN={run_id}" in rest and pid.isdigit() and int(pid) != os.getpid():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


# ---------- git settings that run programs (checked after every command) ----------

# git settings that cannot start a program; everything else (core.fsmonitor, core.pager, alias.x = !…, filter.*,
# include.path, core.hooksPath, …) could run code outside the sandbox the next time the user runs git
SAFE_GIT_KEYS = re.compile(r"(core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|"
                           r"precomposeunicode|symlinks|autocrlf|eol)|user\.(name|email)|init\.defaultbranch|"
                           r"remote\.[^.]+\.(url|fetch|pushurl)|branch\.[^.]+\.(remote|merge|rebase)|"
                           r"extensions\.[a-z]+|pull\.rebase|push\.default|lfs\.repositoryformatversion)", re.IGNORECASE)
MAX_GIT_DEPTH = 3


def git_dirs():
    """The .git folders of the repositories in the start folder (the start folder's own, and subfolders up to
    MAX_GIT_DEPTH levels deep)."""
    found = []
    for dirpath, dirnames, _ in os.walk(ROOT):
        depth = dirpath[len(ROOT):].count(os.sep)
        for d in dirnames:
            if d.lower() == ".git" and os.path.isdir(os.path.join(dirpath, d)):
                found.append(os.path.join(dirpath, d))
        dirnames[:] = [] if depth >= MAX_GIT_DEPTH else \
            [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d))]
    return found


def git_settings():
    """{path: content} of every git config file and hook (not the *.sample examples) in the start folder."""
    files = {}
    for g in git_dirs():
        for dirpath, dirnames, filenames in os.walk(g):
            rel = os.path.relpath(dirpath, g)
            if rel != "." and rel.split(os.sep)[0].lower() in ("objects", "refs", "logs", "lfs"):
                dirnames[:] = []
                continue
            in_hooks = "hooks" in rel.lower().split(os.sep)
            for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
                p = os.path.join(dirpath, name)
                if in_hooks and not name.endswith(".sample") or name.lower().startswith("config"):
                    try:
                        files[p] = ("link:" + os.readlink(p)) if os.path.islink(p) else \
                            open(p, "rb").read(200_000).decode("utf-8", errors="replace")
                    except OSError:
                        pass
        if os.path.islink(os.path.join(g, "hooks")):
            files[os.path.join(g, "hooks")] = "link:" + os.readlink(os.path.join(g, "hooks"))
    return files


def git_config_entries(text):
    """Set of (key, value) pairs of a git config file, e.g. ("core.fsmonitor", "evil.sh")."""
    entries, section = set(), ""
    for line in text.splitlines():
        line = line.strip()
        head = re.match(r'\[\s*([^\s\]"]+)(?:\s+"(.*)")?\s*\]', line)
        if head:
            section = head.group(1).lower() + (f".{head.group(2)}" if head.group(2) is not None else "")
            continue
        kv = re.match(r"([A-Za-z][\w-]*)\s*(?:=\s*(.*))?$", line)
        if kv and section:
            entries.add((f"{section}.{kv.group(1).lower()}", (kv.group(2) or "").strip()))
    return entries


def risky_git_changes(before, after):
    """[(path, what)] for new or changed hooks and new git settings that can run programs."""
    risky = []
    for p, content in after.items():
        old = before.get(p)
        if content == old:
            continue
        if os.path.basename(p).lower().startswith("config") and not content.startswith("link:"):
            new = git_config_entries(content) - git_config_entries(old or "")
            for key, value in sorted(new):
                if not SAFE_GIT_KEYS.fullmatch(key):
                    risky.append((p, f"{key} = {value}"))
        else:
            risky.append((p, "new hook" if old is None else "changed hook"))
    return risky


def restore_git_setting(p, old, risky_lines):
    """Puts a git config file or hook back as it was before the command."""
    if os.path.islink(p) or not os.path.basename(p).lower().startswith("config"):
        if os.path.islink(p) or os.path.isfile(p):
            os.remove(p)
        if old is not None and not old.startswith("link:"):
            with open(p, "w", encoding="utf-8") as f:
                f.write(old)
        return
    if old is not None:
        with open(p, "w", encoding="utf-8") as f:
            f.write(old)
        return
    # a new repository's config: keep it, only without the risky settings
    bad = {line.split(" = ", 1)[0] for line in risky_lines}
    out, section = [], ""
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines():
            head = re.match(r'\s*\[\s*([^\s\]"]+)(?:\s+"(.*)")?\s*\]', line)
            key = re.match(r"\s*([A-Za-z][\w-]*)\s*(=|$)", line)
            if head:
                section = head.group(1).lower() + (f".{head.group(2)}" if head.group(2) is not None else "")
            elif key and f"{section}.{key.group(1).lower()}" in bad:
                continue
            out.append(line)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def check_git_settings(before):
    """After a command: new hooks or git settings that run programs are shown in red and undone unless the user
    keeps them. Returns a note for the model."""
    after = git_settings()
    risky = risky_git_changes(before, after)
    if not risky:
        return ""
    lines = [f"{RED}{clean(os.path.relpath(p, ROOT))}: {clean(what)[:120]}{RESET}" for p, what in risky[:12]]
    if len(risky) > 12:
        lines.append(f"{RED}… and {len(risky) - 12} more{RESET}")
    lines += [f"{RED}git runs these programs itself, outside the sandbox, the next time you use git.{RESET}",
              f"{RED}Only keep them if you asked for exactly this.{RESET}"]
    ui_break()
    print(card(f"{RED}! The command changed git settings that run programs{RESET}", lines, color=RED))
    try:
        keep = ask(f"  {RED}? Keep them? [Y/N]{RESET} ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        keep = False
    if keep:
        return " The user kept the changed git settings."
    for p in {p for p, _ in risky}:
        restore_git_setting(p, before.get(p), [w for q, w in risky if q == p])
    print(f"  {GREEN}✓{RESET} {DIM}git settings restored{RESET}")
    return (" The command changed git settings that can run programs (hooks or config); the user did not keep "
            "them and they were undone. Do not try again.")


TERMINAL_CODES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-_]")


def shorten_output(text):
    """Keeps the start and (more of) the end of long output: errors and summaries are usually at the end."""
    if len(text) <= MAX_OUTPUT:
        return text
    head, tail = MAX_OUTPUT // 5, MAX_OUTPUT - MAX_OUTPUT // 5
    return text[:head] + f"\n… ({len(text) - head - tail} characters left out) …\n" + text[-tail:]


def run_command(command, timeout=RUN_TIMEOUT):
    command = str(command or "").strip()
    if not command:
        return "Error: the command is empty."
    if len(command) > MAX_COMMAND:
        return f"Refused: the command is longer than {MAX_COMMAND} characters."
    try:
        timeout = max(1, min(int(timeout or RUN_TIMEOUT), RUN_MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = RUN_TIMEOUT
    if not os.path.exists("/usr/bin/sandbox-exec"):
        return "Error: commands cannot run here (the macOS sandbox is missing)."
    ui_break()
    shown = clean(command)
    lines = [f"{BOLD}{piece}{RESET}" for piece in shown.splitlines() or [""]]
    if DESTRUCTIVE.search(command):
        lines.append(f"{RED}! can delete or overwrite files – /undo cannot bring them back{RESET}")
    lines.append(f"{DIM}sandbox: no internet · writes only in this folder · stops after {timeout} s{RESET}")
    print()
    print(card(f"{ORANGE}runs a command{RESET}", lines))
    if not confirm("Run?"):
        print()
        return "The user declined. The command was not run."
    # the backup folder exists before the command runs, so no command can create it (e.g. as a link) in other case
    os.makedirs(os.path.join(ROOT, BACKUP_DIR), exist_ok=True)
    own_tmp = os.path.realpath(tempfile.mkdtemp(prefix="flashcat-run-"))
    run_id = os.urandom(8).hex()
    git_before = git_settings()
    try:
        return _run_sandboxed(command, timeout, own_tmp, run_id, git_before)
    finally:
        shutil.rmtree(own_tmp, ignore_errors=True)


def _run_sandboxed(command, timeout, own_tmp, run_id, git_before):
    proc = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", sandbox_profile(own_tmp), "/bin/zsh", "-f", "-c", command],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, env=command_env(own_tmp, run_id))
    chunks, last = [], {"line": ""}

    def reader():
        for raw in iter(lambda: proc.stdout.readline(), b""):
            text = raw.decode("utf-8", errors="replace")
            chunks.append(text)
            if text.strip():
                last["line"] = text.strip()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    started, timed_out = time.time(), False
    try:
        while t.is_alive():
            t.join(0.2)
            elapsed = time.time() - started
            if elapsed > timeout:
                timed_out = True
                break
            if LIVE:
                tail = clean(TERMINAL_CODES.sub("", last["line"]))[: max(10, term_width() - 20)]
                print(f"\r  {DIM}│ {int(elapsed)} s  {tail}{RESET}\033[K", end="", flush=True)
    except KeyboardInterrupt:
        if LIVE:
            print("\r\033[K", end="")
        raise
    finally:
        stop_leftovers(proc, run_id)  # also after a normal end: nothing keeps running in the background
        t.join(2)
        code = proc.wait()
        proc.stdout.close()
    if LIVE:
        print("\r\033[K", end="", flush=True)
    output = clean(TERMINAL_CODES.sub("", "".join(chunks))).rstrip()
    stats["commands"] += 1
    tail = output.expandtabs(4).splitlines()[-8:]
    color = GREEN if code == 0 and not timed_out else RED
    status = "stopped after the time limit" if timed_out else f"exit code {code}"
    body = [f"{DIM}{line[: term_width() - 10]}{RESET}" for line in tail] or [f"{DIM}(no output){RESET}"]
    print(card(f"{color}{'✓' if color == GREEN else '✗'}{RESET} {DIM}{status} · {fmt_num(time.time() - started)} s{RESET}",
               body))
    note = ""
    if SERVER_BLOCKED.search(output):
        note = (" The command tried to start a server (listen on a port). Servers cannot run here: commands have no "
                "network and nothing keeps running after a command. Tell the user to start it themselves in another "
                "terminal window in this folder, or run a build command that finishes instead.")
    elif "operation not permitted" in output.lower():  # Node and Go write it in lower case
        note = (" The sandbox blocked something (Operation not permitted): internet, writing outside the folder or "
                "private data are not available to commands.")
    note += check_git_settings(git_before)
    return (f"Exit code: {code}" + (f" (stopped after {timeout} s)" if timed_out else "") + note + "\n"
            + (shorten_output(output) or "(no output)"))


# ---------- images and web ----------

pending_images = []  # (rel path, data URL) queued by view_image, sent to the model after the tool results


def image_data_url(full):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "image.jpg")
        # scale down to max 1024 px and convert to JPEG (handles HEIC etc.)
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1024", full, "--out", out],
                       capture_output=True, timeout=60, check=True)
        with open(out, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


def view_image(path):
    full = resolve(path, ask=True)
    rel = os.path.relpath(full, ROOT)
    if os.path.splitext(full)[1].lower() not in IMAGE_EXT:
        return "Error: this is not a supported image file."
    pending_images.append((rel, image_data_url(full)))
    return f"The image {rel} follows in the next message."


class _TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def check_public_url(url):
    """Refuses URLs that point to this Mac or the local network (localhost, 192.168.x.x, 10.x.x.x, …)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http and https addresses are allowed.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError(f"The address {parsed.hostname} was not found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise ValueError("Refused: addresses on this computer or in the local network are blocked.")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_web = urllib.request.build_opener(_PublicRedirects)


def http_get(url, timeout=30, data=None):
    check_public_url(url)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with _web.open(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read(3_000_000)
    charset = re.search(r"charset=([\w-]+)", ctype)
    return ctype, raw.decode(charset.group(1) if charset else "utf-8", errors="replace")


MAX_WEB_DETAIL = 1000
WEB_DENIED = "The user declined the internet access. Nothing was fetched."


def confirm_web(what, detail):
    """Every internet access needs the user's OK: the model might otherwise send folder contents out
    inside a URL or search query (e.g. when a manipulated document tells it to)."""
    ui_break()
    shown = clean(detail)
    if len(shown) > MAX_WEB_DETAIL:
        raise ValueError(f"Refused: the {what.lower()} is longer than {MAX_WEB_DETAIL} characters.")
    # shown in full (wrapped), so nothing can hide behind a cut-off end
    width = max(20, term_width() - 6)
    print(f"    {DIM}{what}:{RESET}")
    for i in range(0, len(shown), width):
        print(f"    {shown[i:i + width]}")
    return confirm("Allow internet access?")


def fetch_url(url):
    if not re.match(r"^https?://", url):
        return "Error: only http and https addresses are allowed."
    # the public-address check runs in http_get, after the OK: already its DNS lookup could carry data out
    if not confirm_web("Address", url):
        return WEB_DENIED
    ctype, text = http_get(url)
    if "html" in ctype or text.lstrip().lower().startswith(("<!doctype", "<html")):
        parser = _TextExtractor()
        parser.feed(text)
        text = "".join(parser.parts)
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    return text[:30_000] + ("\n… (shortened)" if len(text) > 30_000 else "")


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def web_search(query):
    if not confirm_web("DuckDuckGo search", query):
        return WEB_DENIED
    # DuckDuckGo answers GET requests from scripts with a bot check, POST works; kl=wt-wt: worldwide, no region
    _, page = http_get("https://html.duckduckgo.com/html/", timeout=20,
                       data=urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode())
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    results = []
    for i, (href, title) in enumerate(links[:8]):
        href = html.unescape(href)
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if target.startswith("//"):
            target = "https:" + target
        snippet = strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append(f"{i + 1}. {strip_tags(title)}\n   {target}\n   {snippet}")
    return "\n".join(results) or "No results (or the search is currently unreachable)."


FUNCS = {"list_dir": list_dir, "read_file": read_file, "search": search, "write_file": write_file,
         "edit_file": edit_file, "write_docx": write_docx, "write_pdf": write_pdf, "move_file": move_file,
         "move_files": move_files, "run_command": run_command, "view_image": view_image, "web_search": web_search,
         "fetch_url": fetch_url}


# ---------- model ----------

INTERNAL_REPLIES = ("Understood.", "All right, I have the conversation so far in mind.")
state = {"thinking": False, "context": 32768, "used": 0, "last_answer": "", "tok_s": 0.0, "turn_start": 0.0,
         "phase": "thinking", "tools_shown": False}
# for the receipt shown when the chat ends
stats = {"start": time.time(), "questions": 0, "created": set(), "changed": set(), "moved": 0, "undone": 0,
         "commands": 0}


def loaded_context_length():
    if BACKEND == "ollama":
        # the launcher asked for FLASHCAT_CONTEXT; Ollama caps it at the model's maximum, so ask what it loaded
        try:
            with urllib.request.urlopen(f"{SERVER}/api/ps", timeout=5) as r:
                for m in json.load(r).get("models") or []:
                    if m.get("name") in (MODEL, MODEL + ":latest") and m.get("context_length"):
                        return int(m["context_length"])
        except Exception:
            pass
        try:
            return int(os.environ.get("FLASHCAT_CONTEXT") or 32768)
        except ValueError:
            return 32768
    try:
        with urllib.request.urlopen(f"{SERVER}/api/v0/models/{MODEL}", timeout=5) as r:
            return int(json.load(r).get("loaded_context_length") or 32768)
    except Exception:
        return 32768


try:
    from pygments import highlight
    from pygments.formatters import TerminalFormatter
    from pygments.lexers import TextLexer, get_lexer_by_name
    from pygments.util import ClassNotFound
    # uses the terminal's own color palette, so code stays readable on light and dark backgrounds
    CODE_FORMATTER = TerminalFormatter(bg=os.environ.get("FLASHCAT_BG", "dark"))
except ImportError:  # optional: without pygments, code is shown in a single color
    highlight = None

CODE_ALIASES = {"js": "javascript", "ts": "typescript", "sh": "bash", "shell": "bash", "zsh": "bash",
                "py": "python", "yml": "yaml", "md": "markdown", "html": "html", "htm": "html"}
ITALIC, CYAN, BLUE = "\033[3m", "\033[36m", "\033[1;34m"


# control characters except tab and newline, and invisible Unicode direction marks (they can make an address
# look different from what it is)
CONTROL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def clean(text):
    """Removes terminal control characters (e.g. hidden escape sequences in a document or web page)."""
    return CONTROL_RE.sub("", text)


ANSI_RE = re.compile(r"\033\[[0-9;]*m|\033\]8;[^\033]*\033\\")  # colors and OSC 8 hyperlinks
INDENT = "  "  # answers are indented; the first line starts with "⏺ "
LIVE = sys.stdout.isatty()


def visible_len(text):
    return len(ANSI_RE.sub("", text))


def term_width():
    return shutil.get_terminal_size((80, 20)).columns or 80


def link(text, full):
    """Clickable file link (OSC 8, Cmd+click in the terminal opens the file)."""
    if not LIVE:
        return text
    return f"\033]8;;file://{urllib.parse.quote(full)}\033\\{text}\033]8;;\033\\"


def file_link(rel):
    full = os.path.join(ROOT, rel)
    return link(clean(rel), full) if os.path.exists(full) else clean(rel)


def right_aligned(text):
    pad = max(1, term_width() - visible_len(text) - 2)
    return " " * pad + text


def card(title, lines, color=None):
    """Rounded card: ╭─ title ───╮ … ╰───╯ (lines may contain color codes)."""
    color = color or DIM
    width = min(term_width() - 4, max([visible_len(title) + 6] + [visible_len(l) + 4 for l in lines] + [40]))
    inner = width - 4
    head = f" {title} " if title else ""
    out = [f"  {color}╭─{RESET}{head}{color}{'─' * max(0, width - 3 - visible_len(head))}╮{RESET}"]
    for line in lines:
        for piece in wrap_ansi(line, inner):
            out.append(f"  {color}│{RESET} {piece}{' ' * max(0, inner - visible_len(piece))} {color}│{RESET}")
    out.append(f"  {color}╰{'─' * (width - 2)}╯{RESET}")
    return "\n".join(out)


def fmt_num(x, digits=1):
    return f"{x:.{digits}f}"


def wrap_ansi(text, width, hang=0):
    """Word-wraps text containing color codes to `width` visible columns; continuation lines get `hang` spaces."""
    if width < 4 or visible_len(text) <= width:
        return [text]
    lines, current, used = [], "", 0
    for word in re.split(r"(\s+)", text):
        if not word:
            continue
        n = visible_len(word)
        if word.isspace():
            if used and used + n <= width:
                current, used = current + word, used + n
            continue
        limit = width - (hang if lines else 0)
        if used and used + n > limit:
            lines.append(current.rstrip())
            current, used = " " * hang, hang
            limit = width - hang
        current, used = current + word, used + n
    lines.append(current)
    return lines


class MarkdownStream:
    """Prints streamed Markdown with formatting: headings, lists, **bold**, `code` and highlighted code blocks.

    Text appears live; each line is re-drawn formatted (indented and word-wrapped) once it is complete.
    The caller prints the "⏺ " marker of the first line.
    """

    def __init__(self):
        self.buf = ""            # current incomplete line
        self.shown = 0           # characters of self.buf already printed raw
        self.started = True      # the current line's indent is already on screen (the caller printed "⏺ ")
        self.lexer = None        # set while inside a ``` code block
        self.lang = ""
        self.table = []          # rows of a Markdown table being collected
        self.live = LIVE

    def feed(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._emit(line)
        # table rows are collected silently and drawn as a whole once the table is complete
        in_table_row = self.lexer is None and self.buf.lstrip().startswith("|")
        if self.live and len(self.buf) > self.shown and not in_table_row:
            self._flush_table()
            self._start_line()
            print(self.buf[self.shown:], end="", flush=True)
            self.shown = len(self.buf)

    def finish(self):
        if self.buf or self.shown:
            self._emit(self.buf)
        self._flush_table()
        self.buf, self.shown = "", 0

    def _flush_table(self):
        if not self.table:
            return
        rows, self.table = self.table, []
        self._start_line()
        print(("\n" + INDENT).join(self._render_table(rows)))
        self.started = False

    def _render_table(self, rows):
        def cells(row):
            row = row.strip()
            row = row[1:] if row.startswith("|") else row
            row = row[:-1] if row.endswith("|") else row
            return [c.strip() for c in re.split(r"(?<!\\)\|", row)]

        parsed = [cells(r) for r in rows]
        is_sep = [all(re.fullmatch(r":?-{2,}:?", c) for c in r if c) and any(r) for r in parsed]
        header = 1 if len(parsed) > 1 and is_sep[1] else 0
        body = [r for r, sep in zip(parsed, is_sep) if not sep]
        ncol = max(len(r) for r in body)
        body = [[self._inline(c) for c in r] + [""] * (ncol - len(r)) for r in body]
        if header:
            body[0] = [f"{BOLD}{c}{RESET}" for c in body[0]]
        widths = [max(visible_len(r[i]) for r in body) for i in range(ncol)]
        avail = term_width() - len(INDENT) - 1 - (3 * ncol + 1)
        while sum(widths) > avail and max(widths) > 6:
            widths[widths.index(max(widths))] -= 1
        line = lambda l, m, r: DIM + l + m.join("─" * (w + 2) for w in widths) + r + RESET
        out = [line("┌", "┬", "┐")]
        for n, row in enumerate(body):
            wrapped = [wrap_ansi(c, widths[i]) for i, c in enumerate(row)]
            for k in range(max(len(w) for w in wrapped)):
                parts = []
                for i, w in enumerate(wrapped):
                    piece = w[k] if k < len(w) else ""
                    parts.append(" " + piece + RESET + " " * max(0, widths[i] - visible_len(piece)) + " ")
                out.append(f"{DIM}│{RESET}" + f"{DIM}│{RESET}".join(parts) + f"{DIM}│{RESET}")
            if n == 0 and header:
                out.append(line("├", "┼", "┤"))
        out.append(line("└", "┴", "┘"))
        return out

    def _start_line(self):
        if not self.started:
            print(INDENT, end="")
            self.started = True

    def _emit(self, line):
        self._erase_raw(line)
        if self.lexer is None and line.lstrip().startswith("|"):
            self.table.append(line)
            self.shown = 0
            self.started = self.started and self.live  # the erased raw row left the cursor after the indent
            return
        self._flush_table()
        self._start_line()
        text, hang = self._render(line)
        cols = shutil.get_terminal_size((80, 20)).columns or 80
        pieces = [text] if hang is None else wrap_ansi(text, cols - len(INDENT) - 1, hang)
        print(("\n" + INDENT).join(pieces))
        self.shown, self.started = 0, False

    def _erase_raw(self, line):
        """Removes the raw (unformatted) text of the current line from the screen."""
        if not self.live or self.shown == 0:
            return
        cols = shutil.get_terminal_size((80, 20)).columns or 80
        rows = max(1, -(-(len(INDENT) + min(self.shown, len(line))) // cols))
        up = f"\033[{rows - 1}A" if rows > 1 else ""
        print(f"{up}\r\033[{len(INDENT)}C\033[J", end="")

    def _render(self, line):
        """Returns (formatted line, hanging indent for word wrap or None for no wrapping)."""
        fence = re.match(r"^\s*```\s*([\w+#.-]*)", line)
        if fence:
            if self.lexer is None:
                self.lang = fence.group(1).lower()
                self.lexer = self._lexer(self.lang)
                return f"{DIM}┌─ {self.lang or 'code'} {'─' * 30}{RESET}", None
            self.lexer = None
            return f"{DIM}└{'─' * 34}{RESET}", None
        if self.lexer is not None:
            if highlight and self.lexer is not True:
                return highlight(line, self.lexer, CODE_FORMATTER).rstrip("\n"), None
            return f"{GREEN}{line}{RESET}", None
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            return f"{BLUE}{self._inline(heading.group(2))}{RESET}", 0
        item = re.match(r"^(\s*)(?:[-*+]|(\d+[.)]))\s+(.*)", line)
        if item:
            marker = item.group(2) or "•"
            prefix = f"{item.group(1)}{marker} "
            return prefix + self._inline(item.group(3)), len(prefix)
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            return f"{DIM}{'─' * 40}{RESET}", None
        quote = re.match(r"^\s*>\s?(.*)", line)
        if quote:
            return f"{DIM}│{RESET} {ITALIC}{self._inline(quote.group(1))}{RESET}", 2
        # the model answers in the user's language, so German warning words are recognized too
        warning = re.match(r"^\s*(?:⚠️?\s*|\**(?:Warning|Important|Caution|Note|Achtung|Warnung|Wichtig|Vorsicht)\b)", line)
        if warning:
            return f"{ORANGE}!{RESET} {self._inline(line.strip().lstrip('⚠️').strip())}", 2
        return self._inline(line), 0

    FILE_WORD = re.compile(r"(?<![\w/@.])((?:[\w-]+/)*[\w.-]*\w\.[A-Za-z0-9]{1,5})(?![\w/])")

    @staticmethod
    def _file_link(name):
        """Links `name` if it is an existing file in the start folder, else returns it unchanged."""
        try:
            full = resolve(name)
        except ValueError:
            return name
        return link(name, full) if os.path.isfile(full) else name

    # the model sometimes writes symbols in LaTeX math, which the terminal cannot show
    LATEX = {"rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒", "leftrightarrow": "↔", "times": "×",
             "cdot": "·", "approx": "≈", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "pm": "±",
             "div": "÷", "infty": "∞", "checkmark": "✓"}
    LATEX_RE = re.compile(r"\$\s*\\(" + "|".join(sorted(LATEX, key=len, reverse=True)) + r")\s*\$")

    def _inline(self, text):
        text = self.LATEX_RE.sub(lambda m: self.LATEX[m.group(1)], text)
        parts = re.split(r"(`[^`]+`)", text)
        out = []
        for part in parts:
            if len(part) > 1 and part.startswith("`") and part.endswith("`"):
                out.append(f"{CYAN}{self._file_link(part[1:-1])}{RESET}")
                continue
            part = self.FILE_WORD.sub(lambda m: self._file_link(m.group(1)), part)
            part = re.sub(r"\*\*(.+?)\*\*", lambda m: f"{BOLD}{m.group(1)}{RESET}", part)
            part = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", lambda m: f"{ITALIC}{m.group(1)}{RESET}", part)
            out.append(part)
        return "".join(out)

    @staticmethod
    def _lexer(lang):
        if not highlight:
            return True
        try:
            return get_lexer_by_name(CODE_ALIASES.get(lang, lang or "text"), stripnl=False, ensurenl=False)
        except ClassNotFound:
            return TextLexer()


class EscToCancel:
    """While active, pressing Esc interrupts the running request (like Ctrl+C).

    Puts the terminal into cbreak mode and watches for a lone Esc byte; escape sequences such as
    arrow keys (Esc followed by more bytes) are ignored.
    """

    def __init__(self):
        self.fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        self.stop = threading.Event()
        self.saved = None

    def __enter__(self):
        if self.fd is None:
            return self
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()
        return self

    def _watch(self):
        while not self.stop.is_set():
            if not select.select([self.fd], [], [], 0.1)[0]:
                continue
            key = os.read(self.fd, 1)
            if key != b"\x1b":
                continue  # typing during an answer is ignored
            if select.select([self.fd], [], [], 0.05)[0]:
                os.read(self.fd, 16)  # arrow key or similar escape sequence, not a lone Esc
                continue
            if not self.stop.is_set():
                self.stop.set()
                os.kill(os.getpid(), signal.SIGINT)
            return

    def __exit__(self, *exc):
        if self.fd is None:
            return False
        self.stop.set()
        self.thread.join(0.3)
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        return False


def call_model(messages, tools=True, show=True):
    """Streams the reply: text is printed live as it arrives; returns the full assistant message."""
    payload = {"model": MODEL, "messages": messages, "temperature": 0.7, "stream": True,
               "stream_options": {"include_usage": True}}
    if tools:
        payload["tools"] = TOOLS
    if not state["thinking"] and not state.get("no_reasoning_effort"):
        payload["reasoning_effort"] = "none"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    done = threading.Event()
    spinner = threading.Thread(target=spin, args=(done,), daemon=True)
    if sys.stdout.isatty():
        spinner.start()

    def stop_spinner():
        if not done.is_set():
            done.set()
            if spinner.is_alive():
                spinner.join()

    content, calls = "", {}
    t_start, t_first, generated = time.time(), None, 0
    renderer = MarkdownStream()
    try:
        for attempt in range(2):  # LM Studio occasionally drops a connection; retry once
            try:
                req = urllib.request.Request(URL, json.dumps(payload).encode(), headers)
                with EscToCancel(), urllib.request.urlopen(req, timeout=900) as r:
                    for raw in r:
                        line = raw.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        usage = chunk.get("usage")
                        if usage:
                            state["used"] = usage.get("total_tokens", 0)
                            generated = usage.get("completion_tokens", 0)
                        choices = chunk.get("choices") or [{}]
                        delta = choices[0].get("delta") or {}
                        if t_first is None and (delta.get("content") or delta.get("tool_calls")
                                                or delta.get("reasoning_content")):
                            t_first = time.time()
                        if delta.get("reasoning_content"):
                            state["phase"] = "reasoning"
                        elif delta.get("tool_calls"):
                            state["phase"] = "planning the next step"
                        text = delta.get("content")
                        if text:
                            if not content:
                                stop_spinner()
                                text = text.lstrip()
                                if show:
                                    if state["tools_shown"]:
                                        print()  # air between the tool timeline and the answer
                                        state["tools_shown"] = False
                                    print("⏺ ", end="")
                            text = clean(text)
                            content += text
                            if show:
                                renderer.feed(text)
                        for tc in delta.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0),
                                                 {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["function"]["name"] += fn.get("name") or ""
                            c["function"]["arguments"] += fn.get("arguments") or ""
                break
            except urllib.error.HTTPError as e:
                if e.code == 400 and "reasoning_effort" in payload and not attempt:
                    # some servers or versions do not know this setting: continue without it
                    state["no_reasoning_effort"] = True
                    del payload["reasoning_effort"]
                    continue
                if e.code in (401, 403):
                    raise RuntimeError("The model server requires an API key. In LM Studio, turn off \"Require authentication\" in "
                                       "LM Studio's server settings, or start with FLASHCAT_API_KEY=<key> flashcat") from None
                raise
            except (http.client.RemoteDisconnected, ConnectionResetError):
                if attempt or content or calls:
                    raise
    finally:
        stop_spinner()
        if content and show:
            renderer.finish()
            print()
    if t_first and generated:
        state["tok_s"] = generated / max(time.time() - t_first, 0.001)
    if BACKEND == "ollama":
        keep_loaded()
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg


def keep_loaded():
    """Ollama unloads a model 5 minutes after the last request; Flashcat keeps it loaded until the chat ends (the
    launcher's cleanup unloads it), so the next question does not wait for loading again."""
    try:
        req = urllib.request.Request(SERVER + "/api/generate", json.dumps({"model": MODEL, "keep_alive": -1}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


STAR_FRAMES = "·✢✳✶✻✽✻✶✳✢"


def spin(done):
    """Breathing orange star with what the model is doing and the elapsed time, until `done` is set."""
    start = time.time()
    i = 0
    while not done.wait(0.12):
        star = STAR_FRAMES[i % len(STAR_FRAMES)]
        print(f"\r  {ORANGE}{star}{RESET} {state['phase']} …  {DIM}{int(time.time() - start)} s · Esc to cancel{RESET}\033[K",
              end="", flush=True)
        i += 1
    print("\r\033[K", end="", flush=True)


# ---------- tool timeline ----------

TOOL_VERBS = {"list_dir": "looks into", "read_file": "reads", "search": "searches", "write_file": "writes",
              "edit_file": "changes", "write_docx": "creates Word document", "write_pdf": "creates PDF",
              "move_file": "moves", "move_files": "moves", "run_command": "runs", "view_image": "looks at",
              "web_search": "searches the web for", "fetch_url": "opens"}
INTERACTIVE_TOOLS = {"write_file", "edit_file", "write_docx", "write_pdf", "move_file", "move_files", "run_command"}
tool_line = {"open": False, "text": ""}


def ui_break():
    """Called before anything is printed inside a tool, so the tool's timeline line is not overwritten later."""
    if tool_line["open"]:
        tool_line["open"] = False
        print()


def run_tool(call):
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    raw = clean(str(args.get("path") or args.get("source") or args.get("pattern") or args.get("query")
                    or args.get("url") or args.get("command") or "."))
    if name == "move_files":
        raw = plural(len(args.get("moves") or []), "item")
    verb = TOOL_VERBS.get(name, name)
    room = max(10, term_width() - len(verb) - 22)
    short = raw if len(raw) <= room else raw[: room - 1] + "…"
    if name in ("search", "web_search"):
        target = f"“{short}”"
    elif name in ("run_command", "move_files"):
        target = short
    elif name == "fetch_url":
        target = urllib.parse.urlparse(raw).netloc or short
    elif raw == ".":
        target = "the folder"
    else:
        target = link(short, os.path.join(ROOT, raw)) if os.path.exists(os.path.join(ROOT, raw)) else short
    text = f"  {DIM}◇{RESET} {verb} {target}"
    if name not in ("search", "web_search", "fetch_url", "run_command", "move_files") and private_item(os.path.realpath(os.path.join(ROOT, raw))):
        text = f"  {RED}◇ {verb} {short} · private{RESET}"
    print(text, end="", flush=True)
    tool_line.update(open=True, text=text)
    state["tools_shown"] = True
    started = time.time()
    try:
        if name not in FUNCS:
            raise ValueError(f"there is no tool called {name}.")
        result = str(FUNCS[name](**args))
    except TypeError as e:
        result = f"Error: wrong arguments for {name}: {e}"
    except FileNotFoundError:
        result = f"Error: {raw} does not exist."
    except IsADirectoryError:
        result = f"Error: {raw} is a folder."
    except Exception as e:  # report tool errors (incl. refusals) back to the model
        result = f"Error: {e}"
    if result.startswith(("Error", "Refused")):
        status = f"{RED}✗{RESET} {DIM}{result.split(':', 1)[-1].strip()[:40]}{RESET}"
    elif "declined" in result:
        status = f"{YELLOW}✗ declined{RESET}"
    else:
        status = f"{GREEN}✓{RESET} {DIM}{fmt_num(time.time() - started)} s{RESET}"
    if tool_line["open"]:
        pad = max(2, term_width() - visible_len(text) - visible_len(status) - 2)
        print(" " * pad + status)
        tool_line["open"] = False
    elif name not in INTERACTIVE_TOOLS or "✗" in status:
        print(f"    {status}")
    return result


# ---------- sessions, instructions, context ----------

TRUSTED_FILE = os.path.join(HOME_DIR, "trusted-instructions.json")


def folder_instructions_trusted(path, content):
    """A FLASHCAT.md in the start folder may come from someone else (e.g. a downloaded project) and could steer
    the model. It is shown and only loaded after the user's OK - the first time, and again whenever it changed."""
    digest = hashlib.sha256(content.encode()).hexdigest()
    try:
        with open(TRUSTED_FILE, encoding="utf-8") as f:
            trusted = json.load(f)
    except (OSError, json.JSONDecodeError):
        trusted = {}
    if trusted.get(path) == digest:
        return True
    print(f"\n  {ORANGE}!{RESET} This folder has instructions for Flashcat {DIM}(new or changed since last time){RESET}")
    show_all = preview(f"{os.path.relpath(path, ROOT)} · {plural(len(content.splitlines()), 'line')}", content.splitlines())
    print(f"  {DIM}Only load instructions you wrote yourself or trust – they steer every answer.{RESET}")
    if not confirm("Load these instructions?", show_all):
        print(f"  {DIM}not loaded – you will be asked again next time{RESET}\n")
        return False
    trusted[path] = digest
    with open(TRUSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(trusted, f, indent=1)
    print()
    return True


def system_prompt():
    text, loaded = SYSTEM, []
    candidates = [("global", os.path.join(HOME_DIR, "FLASHCAT.md")), ("folder", os.path.join(ROOT, "FLASHCAT.md"))]
    for label, p in candidates:
        if label == "folder":
            try:
                p = resolve("FLASHCAT.md")  # a link to a file outside the folder is not followed
            except ValueError:
                continue
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                content = f.read()[:20_000]
            if label == "folder" and not folder_instructions_trusted(p, content):
                continue
            text += f"\n\nInstructions from the user ({label}, from {p}):\n" + content
            loaded.append(p)
    return text, loaded


def without_images(messages):
    """Copy of the history with image payloads replaced by a short note (keeps session files small)."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            texts = [p.get("text", "") for p in m["content"] if p.get("type") == "text"]
            m = {**m, "content": " ".join(texts) + " [an image was shown here]"}
        out.append(m)
    return out


def new_session_id():
    return time.strftime("%Y%m%d-%H%M%S")


def save_session(messages):
    if len(messages) < 2:
        return
    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(os.path.join(SESSION_DIR, state["session"] + ".json"), "w", encoding="utf-8") as f:
        json.dump({"root": ROOT, "saved": time.strftime("%Y-%m-%d %H:%M"),
                   "messages": without_images(messages[1:]), "journal": journal}, f, ensure_ascii=False)


def list_sessions():
    """[(session id, data)] for this folder, newest first."""
    old = os.path.join(HOME_DIR, "sessions", FOLDER_ID + ".json")  # single-session format of an earlier version
    if os.path.isfile(old):
        os.makedirs(SESSION_DIR, exist_ok=True)
        shutil.move(old, os.path.join(SESSION_DIR, "20000101-000000.json"))
    result = []
    for name in sorted(os.listdir(SESSION_DIR), reverse=True) if os.path.isdir(SESSION_DIR) else []:
        if name.endswith(".json"):
            try:
                with open(os.path.join(SESSION_DIR, name), encoding="utf-8") as f:
                    result.append((name[:-5], json.load(f)))
            except (OSError, json.JSONDecodeError):
                pass
    return result


JOURNAL_KEYS = {"write": ("full", "rel", "backup"), "move": ("src", "dst", "src_rel", "dst_rel"), "batch": ("moves",)}


def valid_journal_entry(e):
    """True for an undo entry from a saved chat that is complete and only touches paths inside this folder."""
    if not isinstance(e, dict) or e.get("type") not in JOURNAL_KEYS or not all(k in e for k in JOURNAL_KEYS[e["type"]]):
        return False
    entries = e["moves"] if e["type"] == "batch" else [e]
    if not isinstance(entries, list) or not entries:
        return False
    paths = []
    for m in entries:
        if not isinstance(m, dict):
            return False
        keys = ("full", "backup") if e["type"] == "write" else JOURNAL_KEYS["move"]
        if e["type"] != "write" and not all(k in m for k in keys):
            return False
        paths += [m.get(k) for k in ("full", "backup", "src", "dst") if m.get(k) is not None]
    return all(isinstance(x, str) and inside(os.path.realpath(x), ROOT) for x in paths)


def resume(messages, session_id, data):
    state.update(session=session_id, used=0, last_answer="")
    journal.clear()
    journal.extend(e for e in data.get("journal") or [] if valid_journal_entry(e))
    msgs = data.get("messages") or []
    print(f"  {GREEN}✓{RESET} {DIM}chat from {data.get('saved')} loaded · {len(msgs)} messages{RESET}")
    last = next((m["content"] for m in reversed(msgs)
                 if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"]), "")
    if last:
        short = " ".join(last.split())
        print(f"  {DIM}│ {short[:200]}{'…' if len(short) > 200 else ''}{RESET}")
    return messages[:1] + msgs


def first_question(data):
    for m in data.get("messages") or []:
        if m.get("role") == "user" and isinstance(m.get("content"), str) and not m["content"].startswith("("):
            return m["content"].split("\n\n--- File:")[0].replace("\n", " ")
    return ""


def show_context():
    used, total = state["used"], state["context"]
    if not used:
        return
    pct = min(used / total, 1.0)
    color = RED if pct > 0.85 else ORANGE if pct > 0.7 else DIM
    filled = max(1, round(pct * 10))
    bar = f"{color}{'▰' * filled}{RESET}{DIM}{'▱' * (10 - filled)}"
    info = f" {round(pct * 100)}%"
    if state["turn_start"]:
        info += f" · {fmt_num(time.time() - state['turn_start'])} s"
        if state["tok_s"]:
            info += f" · {state['tok_s']:.0f} Tok/s"
    print(right_aligned(f"{bar}{info}{RESET}"))
    if pct > 0.7:
        print(right_aligned(f"{color}context is getting full – /compact frees space{RESET}"))
    print()


def pretty_model():
    known = {"gemma-4-26b-a4b-it-qat": "Gemma 4 · 26B", "gemma4:26b": "Gemma 4 · 26B"}
    return known.get(MODEL_NAME, MODEL_NAME) + (" · Ollama" if BACKEND == "ollama" else "")


def start_card(loaded, sessions_count):
    folder = ROOT.replace(os.path.expanduser("~"), "~", 1)
    room = min(term_width() - 8, 60)
    if len(folder) > room:  # keep the start and the end of long paths
        folder = folder[: room // 3] + "…" + folder[-(room - room // 3 - 1):]
    hints = []
    if loaded:
        hints.append("instructions loaded")
    if sessions_count:
        hints.append(f"{sessions_count} earlier chat{'s' if sessions_count > 1 else ''} (/resume)")
    hints.append(f"{paste_key()} pastes images · /help for commands")
    rest = [f"{DIM}{pretty_model()} · {round(state['context'] / 1024)}k context{RESET}",
            f"{DIM}{folder}{RESET}", f"{DIM}{' · '.join(hints)}{RESET}"]
    title = f"{ORANGE}✻{RESET} {BOLD}Flashcat{RESET} {DIM}{VERSION}{RESET}"
    tag = f"{DIM}local · private{RESET}"
    # right-align the tag to the widest line of the card
    inner = min(term_width() - 8, max([visible_len(l) for l in rest] + [50]))
    head = title + " " * max(2, inner - visible_len(title) - visible_len(tag)) + tag
    box = card("", [head] + rest)
    print()
    print(cat())
    print(box)
    print()
    # the eyes line is this many rows above the first input line: paws line + card + empty line + 1
    state["cat_rows_up"] = 1 + box.count("\n") + 1 + 1 + 1


def set_title(on):
    """Window title "🐈 Flashcat · folder" while the chat runs; the terminal's own title comes back afterwards
    (saved and restored with the xterm title stack)."""
    if not LIVE:
        return
    if on:
        name = clean(os.path.basename(ROOT) or ROOT).replace("\007", "")
        print(f"\033[22;0t\033]0;🐈 Flashcat · {name}\007", end="", flush=True)
    else:
        print("\033[23;0t\033[?2004l", end="", flush=True)  # title back, bracketed paste off


def cat_asleep():
    hour = time.localtime().tm_hour
    return hour >= 23 or hour < 6


def cat_eyes(eyes):
    return f"{DIM}     ({RESET} {ORANGE}{eyes}{RESET} {DIM}){RESET}"


def cat():
    """Small cat sitting on the start card; sleepy late at night."""
    extra = f" {DIM}z z{RESET}" if cat_asleep() else ""
    return (f"{DIM}      /\\_/\\{RESET}{extra}\n"
            f"{cat_eyes('-.-' if cat_asleep() else 'o.o')}\n"
            f"{DIM}      > ^ <{RESET}")


def blink_cat(stop):
    """Lets the cat blink (or, when asleep, peek with one eye) until `stop` is set - i.e. until the first input.

    Redraws only the eyes line above the prompt, saving and restoring the cursor so typing is not disturbed.
    """
    import random
    up = state.get("cat_rows_up")
    if not LIVE or not up or up >= (shutil.get_terminal_size((80, 24)).lines or 24):
        return
    awake = "-.-" if cat_asleep() else "o.o"
    moves = [("o.-", 0.6), ("-.o", 0.6)] if cat_asleep() else [("-.-", 0.15), ("-.-", 0.15)]

    def draw(eyes):
        print(f"\0337\033[{up}A\r{cat_eyes(eyes)}\0338", end="", flush=True)

    while not stop.wait(random.uniform(2.5, 6.0)):
        eyes, hold = random.choice(moves)
        draw(eyes)
        if stop.wait(hold):
            draw(awake)
            break
        draw(awake)
        if not cat_asleep() and random.random() < 0.3 and not stop.wait(0.15):  # sometimes a double blink
            draw(eyes)
            stop.wait(0.12)
            draw(awake)


def receipt():
    """Short summary of the session, shown when the chat ends."""
    if not stats["questions"]:
        return
    minutes = max(1, round((time.time() - stats["start"]) / 60))
    q = stats["questions"]
    lines = [f"{minutes} min · {q} question{'s' if q != 1 else ''}"]
    parts = []
    if stats["created"]:
        parts.append(f"{len(stats['created'])} created")
    if stats["changed"]:
        parts.append(f"{len(stats['changed'])} changed")
    if stats["moved"]:
        parts.append(f"{stats['moved']} moved")
    if stats["undone"]:
        parts.append(f"{stats['undone']} undone")
    if stats["commands"]:
        lines.append(plural(stats["commands"], "command") + " run")
    if parts:
        lines.append("Files: " + " · ".join(parts))
        names = sorted(stats["created"] | stats["changed"])
        for rel in names[:6]:
            lines.append(f"{DIM}  •{RESET} {file_link(rel)}")
        if len(names) > 6:
            lines.append(f"{DIM}  … and {len(names) - 6} more{RESET}")
        if stats["changed"]:
            lines.append(f"{DIM}Earlier versions are in {BACKUP_DIR}/{RESET}")
    else:
        lines.append(f"{DIM}No files changed{RESET}")
    lines.append(f"{DIM}Continue with: flashcat --continue{RESET}")
    print()
    print(card(f"{ORANGE}✻{RESET} Session ended", lines))


def compact(messages):
    if len(messages) < 3:
        print("Nothing to summarize yet.\n")
        return messages
    request = without_images(messages) + [{"role": "user", "content": (
        "Summarize our conversation so far for yourself so we can continue with less context. "
        "Keep all important facts, file names, results, decisions and open tasks. "
        "Only the summary, as bullet points, in the language of the conversation.")}]
    print(f"{DIM}  summarizing the chat …{RESET}")
    summary = call_model(request, tools=False, show=False)["content"].strip()
    new = [messages[0],
           {"role": "user", "content": "Summary of our conversation so far:\n" + summary},
           {"role": "assistant", "content": INTERNAL_REPLIES[1]}]
    print(f"{DIM}{summary}{RESET}\n")
    print(f"Chat summarized ({len(messages) - 1} → 2 messages).\n")
    state["used"] = 0
    return new


# ---------- input ----------

def read_input():
    """One user message. Multi-line: paste it (detected automatically) or wrap it in lines with \"\"\"."""
    # orange ❯ like Claude (macOS' libedit moves marked-invisible color codes, so they are left unmarked)
    watcher = PasteWatcher()
    try:
        first = input(f"{ORANGE}❯{RESET} ")
    finally:
        watcher.stop()
    if first.strip() == '"""':
        lines = []
        while True:
            line = input(f"{DIM}...{RESET} ")
            if line.strip() == '"""':
                return "\n".join(lines).strip()
            lines.append(line)
    lines = [first]
    # pasted text arrives all at once: keep reading while more input is already waiting
    while sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.05)[0]:
        lines.append(sys.stdin.readline().rstrip("\n"))
    # the lines after the first one of a multi-line paste bypass the line editor: remove the paste marks there
    return "\n".join(lines).replace(PASTE_START, "").replace(PASTE_END, "").strip()


COMMANDS = ["/help", "/undo", "/copy", "/save", "/export", "/paste", "/remember", "/resume", "/clear", "/compact",
            "/context", "/think", "/exit"]
COMMAND_ALIASES = {"/?": "/help", "/quit": "/exit"}


def complete(text, i):
    """Tab completion: /commands and @file names (relative to the start folder)."""
    options = []
    if text.startswith("/"):
        options = [c for c in COMMANDS if c.startswith(text.lower())]
    elif text.startswith("@"):
        partial = text[1:].strip('"')
        folder, prefix = os.path.split(partial)
        try:
            names = sorted(os.listdir(resolve(folder or ".")))
        except (OSError, ValueError):
            names = []
        for n in names:
            if not allowed(os.path.join(ROOT, folder, n)):
                continue
            if n.startswith(prefix) and (prefix.startswith(".") or not n.startswith(".")):
                p = os.path.join(folder, n)
                if os.path.isdir(os.path.join(ROOT, p)):
                    p += "/"
                options.append(f'@"{p}"' if " " in p else "@" + p)
    return options[i] if i < len(options) else None


def setup_completion():
    try:
        import readline
    except ImportError:
        return
    readline.set_completer_delims(" \t\n")
    readline.set_completer(complete)
    if "libedit" in (readline.__doc__ or ""):  # macOS' Python
        readline.parse_and_bind("bind ^I rl_complete")
        readline.parse_and_bind(f'bind -s ^V "{CLIPBOARD_MARK}"')  # Ctrl+V: paste an image
        bind_paste_markers(readline)
    else:
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind(f'"\\C-v": "{CLIPBOARD_MARK}"')
    if LIVE:
        # bracketed paste on (libedit) - ⌘V with an image in the clipboard then arrives as an empty paste
        print("\033[?2004h" if "libedit" in (readline.__doc__ or "") else "\033[?2004l", end="", flush=True)


PASTE_START, PASTE_END = "\033[200~", "\033[201~"


def bind_paste_markers(readline):
    """⌘V is handled by the terminal: with only an image in the clipboard some (see CMD_V_TERMINALS) send an empty
    "bracketed paste" (start mark directly followed by the end mark), which becomes 📎 - so ⌘V attaches screenshots
    like Ctrl+V there. For text pastes both marks must vanish; libedit cannot bind the start mark alone next to the empty
    paste (it would swallow the first pasted character), so every possible first character gets its own binding."""
    def bind_str(ch):
        return {'"': '\\"', "\\": "\\134", "^": "\\^"}.get(ch, ch)

    readline.parse_and_bind(r'bind -s "\e[201~" ""')
    readline.parse_and_bind(r'bind "\e[299~" ed-redisplay')  # PasteWatcher.REDRAW_KEY
    readline.parse_and_bind(f'bind -s "\\e[200~\\e[201~" "{CLIPBOARD_MARK}"')
    readline.parse_and_bind(r'bind -s "\e[200~\t" "\t"')
    # ASCII, Latin letters with accents and umlauts, punctuation („“ – …), currency, letterlike symbols, arrows,
    # box drawing and dingbats, emoji (all of Unicode would take seconds to bind)
    ranges = ((32, 127), (0xA0, 0x250), (0x2000, 0x2070), (0x20A0, 0x20C0), (0x2100, 0x2200), (0x2500, 0x27C0),
              (0x1F300, 0x1FB00))
    chars = [c for r in ranges for c in range(*r)]
    for c in chars:
        ch = bind_str(chr(c))
        readline.parse_and_bind(f'bind -s "\\e[200~{ch}" "{ch}"')


# AppleScript's "the clipboard" (reading it from JavaScript hung in some terminals): a file copied in Finder, or the
# image data written as PNG to the file given as argument
PASTE_SCRIPT = """
on run argv
  repeat with t in (clipboard info)
    if item 1 of t is «class furl» then return "file:" & POSIX path of (the clipboard as «class furl»)
  end repeat
  try
    set img to (the clipboard as «class PNGf»)
  on error
    return ""
  end try
  set f to open for access POSIX file (item 1 of argv) with write permission
  set eof f to 0
  write img to f
  close access f
  return "image"
end run
"""
CLIPBOARD_TIMEOUT = 10
# ⌘V (with an image in the clipboard) and Ctrl+V put this into the input line; the image is attached when the message
# is sent. One character: Python's line editor draws a longer macro only as far as further keys arrive.
CLIPBOARD_MARK = "📎"
# Terminals known to pass ⌘V on when only an image is in the clipboard (as an empty paste). Others, like macOS'
# own Terminal, send nothing then, so Flashcat never sees the key - Ctrl+V works in all of them.
CMD_V_TERMINALS = ("Hyper",)


def paste_key():
    """The key to show for pasting images in this terminal."""
    return "⌘V" if os.environ.get("TERM_PROGRAM") in CMD_V_TERMINALS else "Ctrl+V"


MAX_CLIPBOARD_TEXT = 100_000
pasted_images = []  # (label, data URL) from /paste, sent with the next message


def read_clipboard():
    """The image in the clipboard: ("image", label, data URL) for a screenshot or an image file copied in Finder,
    or (None, reason)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "clipboard.png")
        try:
            kind = subprocess.run(["osascript", "-", target], input=PASTE_SCRIPT, capture_output=True, text=True,
                                  timeout=CLIPBOARD_TIMEOUT).stdout.rstrip("\n")
        except subprocess.TimeoutExpired:
            return None, ("reading the clipboard took too long – macOS may be asking whether your terminal may "
                          "access the clipboard (look for a dialog), then try again")
        if kind.startswith("file:"):
            try:
                full = resolve(os.path.relpath(kind[5:], ROOT), ask=True)
            except ValueError:
                return None, f"the copied file is outside this folder – only files in {ROOT} can be attached"
            rel = clean(os.path.relpath(full, ROOT))
            if os.path.splitext(full)[1].lower() not in IMAGE_EXT:
                return None, f"the copied file is no image – attach it with @{rel}"
            return "image", rel, image_data_url(full)
        if kind == "image" and os.path.getsize(target):
            return "image", "clipboard", image_data_url(target)
    return None, "there is no image in the clipboard"


def paste_image():
    """/paste: queues the image in the clipboard for the next message."""
    got = read_clipboard()
    if got[0] != "image":
        print(f"Nothing attached: {got[1]}. Screenshot to the clipboard: ⌘⇧4 while holding Ctrl.\n")
        return
    _, label, url = got
    pasted_images.append((label, url))
    print(f"  {DIM}◇{RESET} image from the {'clipboard' if label == 'clipboard' else label} attached "
          f"{DIM}– now type your question{RESET}\n")


class PasteWatcher:
    """While the user types: when ⌘V / Ctrl+V put a 📎 into the line, the image is taken from the clipboard right
    away and its name ("[Image #1]", "[Image #2: photo.jpg]") is written behind the 📎.

    The line editor only redraws when a key arrives, so afterwards a key that does nothing but redraw the line is
    put into the terminal's input (TIOCSTI)."""

    REDRAW_KEY = b"\033[299~"  # bound to ed-redisplay in bind_paste_markers

    def __init__(self):
        self.done = threading.Event()
        if LIVE and sys.stdin.isatty() and "readline" in sys.modules:
            threading.Thread(target=self._watch, daemon=True).start()

    def stop(self):
        self.done.set()

    def _type(self, keys):
        for b in keys:
            fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSTI, bytes([b]))

    def _shorten_path(self, readline, line):
        """A pasted or dragged image path in the line (a photo copied on the iPhone arrives as a very long one) is
        replaced by "📎[Image #N: name]", the image taken right away. Done with keys put into the terminal's input:
        to the end of the line (^E), back to the path (^B), delete it, type the name. Images that need a question
        first (private places) keep their path and are handled when the message is sent."""
        for m in TYPED_PATH.finditer(line):
            found = typed_image(m)
            if not found or not os.access(found[0], os.R_OK):
                continue
            full, tail = found
            shared = inside(full.lower(), SHARED_CLIPBOARD.lower())
            if locked(full) and not shared:
                continue
            label = "clipboard" if shared else clean(os.path.relpath(full, ROOT) if inside(full, ROOT)
                                                     else os.path.basename(full))
            try:
                url = image_data_url(full)
            except (OSError, subprocess.SubprocessError):
                continue
            if self.done.is_set() or readline.get_line_buffer() != line:
                return None  # the line changed meanwhile: the positions are no longer right
            n = state.get("images", 0) + 1
            name = "" if shared else ": " + (label if len(label) <= 40 else label[:39] + "…").replace("]", ")")
            end = m.end() - len(tail)
            token = f"{CLIPBOARD_MARK}[Image #{n}{name}]"
            try:
                self._type(b"\x05" + b"\x02" * (len(line) - end) + b"\x7f" * (end - m.start())
                           + (token + ("" if line[end:] else " ")).encode()
                           + (b"\x06" if line[end:end + 1] == " " else b""))  # behind the space that follows
            except OSError:
                return None  # the path stays and is attached when the message is sent
            state["images"] = n
            pending_paste[n] = (label, url)
            if not shared and not inside(full, ROOT):
                pending_outside.add(n)
            return token
        return None

    def _watch(self):
        import readline
        seen, last, checked = 0, "", ""
        while not self.done.wait(0.03):
            line = readline.get_line_buffer()
            # only once the line stands still - a paste arrives character by character
            token = None
            if line == last and line != checked and "/" in line:
                token = self._shorten_path(readline, line)
                checked = line
            last = line
            if token:
                deadline = time.time() + 2
                while token not in readline.get_line_buffer() and time.time() < deadline and not self.done.wait(0.01):
                    pass
                seen = readline.get_line_buffer().count(CLIPBOARD_MARK)  # this 📎 is not a paste to look at
                continue
            marks = line.count(CLIPBOARD_MARK)
            if marks <= seen:
                seen = marks
                continue
            seen = marks
            got = read_clipboard()
            if got[0] != "image" or self.done.is_set():
                continue  # no image (e.g. a 📎 inside pasted text): decided again when the message is sent
            state["images"] = state.get("images", 0) + 1
            n = state["images"]
            pending_paste[n] = got[1:]
            name = "" if got[1] == "clipboard" else f": {got[1]}"
            readline.insert_text(f"[Image #{n}{name}] ")
            try:
                for b in self.REDRAW_KEY:
                    fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSTI, bytes([b]))
            except OSError:
                pass  # the name appears with the next key


pending_paste = {}  # image number -> (label, data URL), taken from the clipboard when it was pasted
pending_outside = set()  # image numbers of dragged or pasted image files from outside the start folder
IMAGE_TOKEN = re.compile(re.escape(CLIPBOARD_MARK) + r"?\[Image #(\d+)(?:: [^\]]*)?\]")


def insert_clipboard(text):
    """Attaches the images pasted with ⌘V / Ctrl+V: "📎[Image #1]" names an image taken from the clipboard when it
    was pasted; a 📎 without a name (sent before the clipboard was read) takes the clipboard now. Without an image
    the text stays as it is (it may contain a 📎 of its own)."""
    def attach(m):
        n = int(m.group(1))
        if n not in pending_paste:
            return m.group(0)
        pasted_images.append(pending_paste.pop(n))
        if n in pending_outside:
            outside.append(pasted_images[-1])
        return f"[Image #{n}]"

    before = len(pasted_images)  # images queued by /paste were announced already
    outside = []
    text = IMAGE_TOKEN.sub(attach, text)
    pending_paste.clear()
    pending_outside.clear()
    if CLIPBOARD_MARK in text:
        got = read_clipboard()
        if got[0] == "image":
            pasted_images.append(got[1:])
            text = text.replace(CLIPBOARD_MARK, "[image]")
        else:
            print(f"  {DIM}📎 nothing attached: {got[1]}{RESET}")
    for image in pasted_images[before:]:
        label = image[0]
        shown = f"{label} {DIM}(from outside the folder){RESET}" if image in outside else file_link(label)
        print(f"  {DIM}◇{RESET} attaches {'the image from the clipboard' if label == 'clipboard' else shown}")
        state["tools_shown"] = True
    return text


# where macOS keeps a photo copied on the iPhone or iPad (Universal Clipboard); ⌘V inserts its path there
SHARED_CLIPBOARD = os.path.join(HOME, "Library", "Group Containers", "group.com.apple.coreservices.useractivityd",
                                "shared-pasteboard")
# an absolute path the way terminals insert a dragged or pasted file: '…', "…" or with \ before spaces
TYPED_PATH = re.compile(r"""(?<!@)'((?:/|~/)[^'\n]+)'|(?<!@)"((?:/|~/)[^"\n]+)"|(?:^|(?<=\s))((?:/|~/)(?:\\.|[^\s\\])+)""")


def typed_image(m):
    """The image file a TYPED_PATH match names: (real path, characters behind the path that are not part of it)."""
    raw = m.group(1) or m.group(2) or re.sub(r"\\(.)", r"\1", m.group(3))
    for cand in ([raw, raw.rstrip(".,;:!?)")] if m.group(3) else [raw]):
        full = os.path.realpath(HOME + cand[1:] if cand.startswith("~/") else cand)
        if os.path.splitext(full)[1].lower() in IMAGE_EXT and os.path.isfile(full):
            return full, raw[len(cand):]
    return None


def attach_typed_images(text):
    """Images whose path the user put into the message - dragged into the terminal, or a photo copied on the iPhone
    and pasted with ⌘V. Returns the text (path replaced by "[Image: name]") and the images as (label, data URL).

    The only way a file outside the start folder gets in: images only, and only from the user's own words - the
    model's tools stay inside the folder. Private places ask first (nothing is unlocked for the tools), except the
    folder of the shared clipboard."""
    images = []

    def attach(m):
        found = typed_image(m)
        if not found:
            return m.group(0)
        full, tail = found
        label = clean(os.path.basename(full))
        try:
            if inside(full, ROOT):
                label = clean(os.path.relpath(resolve(full, ask=True), ROOT))
            elif locked(full) and not inside(full.lower(), SHARED_CLIPBOARD.lower()):
                if not confirm_private(full, "Attach this image?"):
                    raise ValueError("you answered No")
            if not os.access(full, os.R_OK):
                raise ValueError("macOS does not let your terminal read it")
            images.append((label, image_data_url(full)))
        except Exception as e:
            reason = "it could not be converted" if isinstance(e, subprocess.SubprocessError) else e
            print(f"  {RED}✗{RESET} {label} {DIM}not attached: {reason}{RESET}")
            return m.group(0)
        shown = file_link(label) if inside(full, ROOT) else f"{label} {DIM}(from outside the folder){RESET}"
        print(f"  {DIM}◇{RESET} attaches {shown}")
        state["tools_shown"] = True
        return f"[Image: {label}]" + tail

    return TYPED_PATH.sub(attach, text), images


MENTION = re.compile(r'(?:^|(?<=\s))@(?:"([^"]+)"|(\S+))')


def attach_mentions(text):
    """Resolves @file / @"file name" and the paths of dragged or pasted images in the user's text. Returns message
    content (str or multi-part list)."""
    text = insert_clipboard(text)
    text, images = attach_typed_images(text)
    blocks = []
    for m in MENTION.finditer(text):
        name = m.group(1) or m.group(2)
        candidates = [name] if m.group(1) else [name, name.rstrip(".,;:!?)")]
        for cand in candidates:
            if not os.path.isfile(os.path.join(ROOT, cand)):
                continue
            try:
                full = resolve(cand, ask=True)
            except ValueError as e:
                print(f"  {RED}✗{RESET} {clean(cand)} {DIM}not attached: {e}{RESET}")
                break
            if os.path.isfile(full):
                rel = os.path.relpath(full, ROOT)
                try:
                    if os.path.splitext(full)[1].lower() in IMAGE_EXT:
                        images.append((rel, image_data_url(full)))
                    else:
                        blocks.append(f"\n\n--- File: {rel} ---\n{read_file(rel)}\n--- End of {rel} ---")
                    print(f"  {DIM}◇{RESET} attaches {file_link(rel)}")
                    state["tools_shown"] = True
                except Exception as e:
                    print(f"  {RED}✗{RESET} {rel} {DIM}could not be read: {e}{RESET}")
                break
    content = text + "".join(blocks)
    images += pasted_images
    pasted_images.clear()
    if not images:
        return content
    parts = [{"type": "text", "text": content}]
    for rel, url in images:
        parts += [{"type": "text", "text": f"(Image: {rel})"}, {"type": "image_url", "image_url": {"url": url}}]
    return parts


HELP_COMMANDS = [
    ("/help", "this overview", ""),
    ("/undo", "undo the last file change", "/undo list shows all"),
    ("/copy", "copy the last answer", ""),
    ("/save", "save the last answer as a file", "/save name.md"),
    ("/export", "save the whole chat as Markdown", "/export name.md"),
    ("/paste", "attach the image in the clipboard", "or Ctrl+V while typing"),
    ("/remember", "note something for all chats", "/remember I use metric units"),
    ("/resume", "earlier chats in this folder", "/resume 2 loads no. 2"),
    ("/clear", "new chat", ""),
    ("/compact", "summarize the chat", "frees context"),
    ("/context", "context usage", ""),
    ("/think", "think thoroughly on/off", "slower"),
    ("/exit", "quit", "continue with flashcat --continue"),
    ("Esc", "cancel the answer", ""),
]
HELP_TIPS = [
    ("@file", "attach a file", "Tab completes · @\"with spaces.pdf\""),
    ('"""', "multi-line input", "start and end with a line of \"\"\""),
    ("⌘V / Ctrl+V" if paste_key() == "⌘V" else "Ctrl+V", "paste a screenshot or image",
     "⌘⇧4 + Ctrl copies a screenshot"),
    ("drag in", "drag an image in from Finder", "also from outside the folder"),
    ("FLASHCAT.md", "standing instructions", "in the folder or ~/.flashcat/"),
    ('"question"', "answer once, no chat", 'cat log | flashcat "why?"'),
    ("--model", "another model", "flashcat --models lists them"),
    ("--update", "newest version", "flashcat --update"),
]


def show_help():
    text_width = max(len(text) for _, text, _ in HELP_COMMANDS + HELP_TIPS) + 3

    extra_width = max(len(extra) for _, _, extra in HELP_COMMANDS + HELP_TIPS)
    # narrow window: drop the grey hint column, keeping room for the card border and the peeking cat
    show_extra = 13 + text_width + extra_width + 4 + 2 + 6 <= term_width()

    key_width = max(len(key) for key, _, _ in HELP_COMMANDS + HELP_TIPS) + 2

    def rows(entries):
        return [f"{ORANGE}{key:<{key_width}}{RESET}" + (f"{text:<{text_width}}{DIM}{extra}{RESET}" if show_extra else text)
                for key, text, extra in entries]

    lines = card(f"{ORANGE}✻{RESET} Commands", rows(HELP_COMMANDS) + ["", f"{DIM}Tips{RESET}"] + rows(HELP_TIPS)).split("\n")
    # a small cat sitting below the right end of the card
    pad = " " * max(0, visible_len(lines[0]) - 10)
    lines += [f"{pad}{DIM} /\\_/\\{RESET}", f"{pad}{DIM}({RESET} {ORANGE}o.o{RESET} {DIM}){RESET}", f"{pad}{DIM} > ^ <{RESET}"]
    print()
    print("\n".join(lines))
    print()


def chat_markdown(messages):
    """The chat as a Markdown document: questions, answers and the tools used (not their raw results)."""
    out = [f"# Flashcat chat · {time.strftime('%Y-%m-%d %H:%M')}", "",
           f"Folder: `{ROOT.replace(HOME, '~', 1)}` · model: {MODEL_NAME}", ""]
    for m in messages[1:]:
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        content = (content or "").strip()
        if m.get("role") == "user":
            if content.startswith(("(Info:", "(Please answer", "(Image from view_image")) or \
                    content.startswith("Summary of our conversation"):
                continue
            content = re.sub(r"\n\n--- File: (.+?) ---\n.*?\n--- End of \1 ---", r"\n\n📎 \1", content, flags=re.S)
            content = re.sub(r"\n\n--- Input \(piped\) ---\n.*?\n--- End of input ---", "\n\n📎 piped input",
                             content, flags=re.S)
            out += ["## You", "", content, ""]
        elif m.get("role") == "assistant":
            used = [f"*{c['function']['name']}*" for c in m.get("tool_calls") or []]
            if used:
                out += [f"> used {', '.join(used)}", ""]
            if content and content not in INTERNAL_REPLIES:
                out += ["## Flashcat", "", content, ""]
    return "\n".join(out).rstrip() + "\n"


def save_text(name, text, default):
    """Writes `text` as a new file in the folder (never replaces one: name-2.md, name-3.md, …)."""
    name = name or default
    if not os.path.splitext(name)[1]:
        name += ".md"
    try:
        full, rel = check_target(name, WRITE_EXT)
    except ValueError as e:
        print(f"{e}\n")
        return
    base, ext = os.path.splitext(full)
    n = 2
    while os.path.exists(full):
        full, n = f"{base}-{n}{ext}", n + 1
    save_with_backup(full, os.path.relpath(full, ROOT), text)
    print()


def remember(note):
    """/remember: adds a line to the global ~/.flashcat/FLASHCAT.md, which every chat loads."""
    path = os.path.join(HOME_DIR, "FLASHCAT.md")
    if not note:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            text = ""
        print(card(f"{ORANGE}✻{RESET} ~/.flashcat/FLASHCAT.md", [clean(l) for l in text.splitlines()] or
                   [f"{DIM}(nothing yet – /remember <note> adds a line){RESET}"]))
        print(f"{DIM}  used in every chat · edit the file to change or remove notes{RESET}\n")
        return None
    note = clean(" ".join(note.split()))
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    with open(path, "a", encoding="utf-8") as f:
        f.write(("" if not existing or existing.endswith("\n") else "\n") + f"- {note}\n")
    print(f"  {GREEN}✓{RESET} {DIM}noted for all chats in ~/.flashcat/FLASHCAT.md{RESET}\n")
    return note


def last_answer(messages):
    """Newest real reply (not the internal notes added by /undo or /compact)."""
    return state["last_answer"] or next(
        (m["content"] for m in reversed(messages) if m.get("role") == "assistant" and isinstance(m.get("content"), str)
         and m["content"].strip() and m["content"] not in INTERNAL_REPLIES), "")


def handle_command(user, messages):
    """Runs a /command. Returns the (possibly replaced) message list, or None to quit."""
    cmd, _, arg = user.partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    cmd = COMMAND_ALIASES.get(cmd, cmd)
    if cmd == "/exit":
        return None
    if cmd == "/help":
        show_help()
    elif cmd == "/clear":
        messages = messages[:1]
        state.update(used=0, session=new_session_id(), last_answer="")
        journal.clear()
        print(f"  {GREEN}✓{RESET} {DIM}new chat – the previous one is under /resume{RESET}\n")
    elif cmd == "/context":
        state["turn_start"] = 0.0
        if state["used"]:
            show_context()
        else:
            print(f"{DIM}  Not known yet – only after the next answer.{RESET}\n")
    elif cmd == "/think":
        state["thinking"] = not state["thinking"]
        print(f"Thinking is now {'ON (more thorough, slower)' if state['thinking'] else 'OFF (faster)'}.\n")
    elif cmd == "/compact":
        messages = compact(messages)
        save_session(messages)
    elif cmd == "/undo" and arg.lower() in ("list", "ls"):
        show_journal()
    elif cmd == "/undo":
        desc = undo()
        if desc:
            messages += [{"role": "user", "content": f"(Info: I have undone this: {desc}.)"},
                         {"role": "assistant", "content": INTERNAL_REPLIES[0]}]
            save_session(messages)
    elif cmd == "/copy":
        text = last_answer(messages)
        if text:
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            print(f"{DIM}  ✓ last answer copied to the clipboard ({len(text)} characters){RESET}\n")
        else:
            print("There is no answer to copy yet.\n")
    elif cmd == "/save":
        text = last_answer(messages)
        if not text:
            print("There is no answer to save yet.\n")
            return messages
        save_text(arg, text + "\n", f"answer-{time.strftime('%Y%m%d-%H%M')}.md")
    elif cmd == "/export":
        if len(messages) < 2:
            print("There is nothing to export yet.\n")
            return messages
        save_text(arg, chat_markdown(messages), f"chat-{time.strftime('%Y%m%d-%H%M')}.md")
    elif cmd == "/paste":
        paste_image()
    elif cmd == "/remember":
        note = remember(arg)
        if note:
            messages[0] = {**messages[0], "content": messages[0]["content"] + f"\n\nThe user asked you to remember: {note}"}
    elif cmd == "/resume":
        sessions = list_sessions()
        if arg.isdigit() and 1 <= int(arg) <= len(sessions):
            session_id, data = sessions[int(arg) - 1]
            messages = resume(messages, session_id, data)
            print()
        elif not sessions:
            print("There are no saved chats for this folder yet.\n")
        else:
            for i, (session_id, data) in enumerate(sessions[:20], 1):
                mark = " ◀ current" if session_id == state["session"] else ""
                q = first_question(data)
                print(f"  {i:>2}. {data.get('saved', '?')}  {q[:60]}{'…' if len(q) > 60 else ''}{DIM}{mark}{RESET}")
            print(f"{DIM}  load with /resume <number>{RESET}\n")
    else:
        print("Unknown command – /help shows all.\n")
    return messages


BROAD_FOLDERS = {"/", "/Users", "/Volumes", HOME}


def confirm_broad_folder():
    """True if Flashcat may start in this folder; in very broad folders (home, /, …) the user is asked first.
    The launcher calls this (flashcat-chat.py --confirm-folder) before it loads the model."""
    if ROOT not in BROAD_FOLDERS:
        return True
    print(card(f"{ORANGE}! Warning{RESET}", [
        f"Here Flashcat would have access to {BOLD}all{RESET} your documents, photos and downloads in {ROOT}.",
        f"{DIM}Keys, passwords, shell history and ~/Library stay blocked unless you allow them.{RESET}",
        f"{DIM}Better: first change into a project folder, e.g. cd ~/Documents/my-project{RESET}"]))
    try:
        return ask(f"  {ORANGE}?{RESET} Start here anyway? {DIM}[Y/N]{RESET} ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def main():
    global TTY_ANSWERS
    answer_out = sys.stdout
    if QUESTION:
        TTY_ANSWERS = not sys.stdin.isatty()  # stdin carries data: questions are answered in the terminal
        if not answer_out.isatty():
            sys.stdout = sys.stderr  # only the answer goes to the file or program
    os.makedirs(HOME_DIR, exist_ok=True)
    os.chmod(HOME_DIR, 0o700)  # saved chats contain file contents; other accounts on this Mac must not read them
    if not os.environ.get("FLASHCAT_FOLDER_CONFIRMED") and not confirm_broad_folder():
        return
    system, loaded = system_prompt()
    messages = [{"role": "system", "content": system}]
    state["context"] = loaded_context_length()
    state["session"] = new_session_id()
    sessions = list_sessions()
    if QUESTION:
        if RESUME and sessions:
            state["session"] = sessions[0][0]
            messages = messages[:1] + (sessions[0][1].get("messages") or [])
            journal.extend(e for e in sessions[0][1].get("journal") or [] if valid_journal_entry(e))
        return one_shot(messages, answer_out)
    setup_completion()
    set_title(True)
    start_card(loaded, 0 if RESUME else len(sessions))
    if RESUME:
        state["cat_rows_up"] = None  # more lines follow the card, the cat's position is not known exactly
        if sessions:
            messages = resume(messages, *sessions[0])
            print()
        else:
            print(f"  {DIM}No saved chat for this folder – starting a new one.{RESET}\n")
    try:
        chat_loop(messages)
    finally:
        receipt()
        set_title(False)


def chat_loop(messages):
    first = True
    while True:
        blink_stop = threading.Event()
        if first:  # the cat blinks until the first input; afterwards it scrolls away
            threading.Thread(target=blink_cat, args=(blink_stop,), daemon=True).start()
            first = False
        try:
            user = read_input()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        finally:
            blink_stop.set()
        if not user:
            continue
        if user.startswith("/"):
            try:
                messages = handle_command(user, messages)
            except Exception as e:
                print(f"\nError: {e}\n")
            if messages is None:
                return
            continue

        turn_start = len(messages)
        try:
            run_turn(messages, user)
            save_session(messages)
            show_context()
        except KeyboardInterrupt:
            del messages[turn_start:]
            pending_images.clear()
            ui_break()
            print(f"\n  {YELLOW}✗{RESET} {DIM}cancelled{RESET}\n")
        except Exception as e:
            del messages[turn_start:]
            pending_images.clear()
            ui_break()
            print(f"\n  {RED}✗ Error:{RESET} {e}\n")


def run_turn(messages, user, show=True, attachment=""):
    """One question: sends it, runs the tools the model calls and appends everything to `messages`. `attachment`
    (piped input) is added as it is: @file and 📎 in it are not resolved - only the user's own words are."""
    state.update(turn_start=time.time(), tok_s=0.0, tools_shown=False)
    stats["questions"] += 1
    if show:
        print()
    content = attach_mentions(user)
    if attachment:
        if isinstance(content, list):
            content[0]["text"] += attachment
        else:
            content += attachment
    messages.append({"role": "user", "content": content})
    nudged = False
    for step in range(15):
        state["phase"] = "thinking" if step == 0 else "working"
        msg = call_model(messages, show=show)
        if "tool_calls" not in msg and not msg["content"].strip() and not nudged:
            nudged = True  # empty reply: ask once more instead of showing nothing
            messages.append({"role": "user", "content": "(Please answer my last question now.)"})
            continue
        messages.append(msg)
        if msg["content"].strip():
            state["last_answer"] = msg["content"].strip()
        if "tool_calls" not in msg:
            break
        for c in msg["tool_calls"]:
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": run_tool(c)})
        while pending_images:
            rel, url = pending_images.pop(0)
            messages.append({"role": "user", "content": [
                {"type": "text", "text": f"(Image from view_image: {rel})"},
                {"type": "image_url", "image_url": {"url": url}}]})


MAX_PIPED = 200_000


def one_shot(messages, answer_out):
    """flashcat "question": answers once and exits. Piped input is attached to the question. When the output goes
    to a file or another program, only the answer is written there (as Markdown); everything else goes to stderr."""
    text, piped = QUESTION, ""
    if not sys.stdin.isatty():
        data = sys.stdin.read(MAX_PIPED + 1)
        if len(data) > MAX_PIPED:
            data = data[:MAX_PIPED] + f"\n… (shortened, only the first {MAX_PIPED} characters)"
        if data.strip():
            piped = f"\n\n--- Input (piped) ---\n{clean(data)}\n--- End of input ---"
    to_terminal = answer_out.isatty()
    try:
        run_turn(messages, text, show=to_terminal, attachment=piped)
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}✗{RESET} {DIM}cancelled{RESET}", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"  {RED}✗ Error:{RESET} {e}", file=sys.stderr)
        return 1
    save_session(messages)
    if not to_terminal:
        answer_out.write(state["last_answer"] + "\n")
        answer_out.flush()
    else:
        print()
    return 0


if __name__ == "__main__":
    if MODEL == "--version":
        print(f"Flashcat {VERSION}")
        sys.exit(0)
    if MODEL == "--confirm-folder":
        TTY_ANSWERS = True  # the launcher may get a question with piped input: answer in the terminal
        sys.exit(0 if confirm_broad_folder() else 1)
    sys.exit(main() or 0)
