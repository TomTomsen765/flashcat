#!/usr/bin/env python3
"""Flashcat - terminal chat with a local LM Studio model that can work in the current folder.

Named after Flash the cat.

Tools: list, read (text/PDF/Word/Excel, scans via macOS text recognition), search, write + edit (text
files), write Word and PDF documents, move/rename, view images, fetch web pages, web search. Every change is confirmed by the
user, backed up and can be undone with /undo. File access is sandboxed to the folder the chat was
started in. Talks to LM Studio's OpenAI-compatible server on localhost (port from FLASHCAT_PORT). Stdlib only.

Usage: flashcat-chat.py MODEL [--continue]
"""

import base64
import datetime
import difflib
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

SERVER = f"http://localhost:{os.environ.get('FLASHCAT_PORT') or 1234}"
API_KEY = os.environ.get("FLASHCAT_API_KEY", "")  # only needed if LM Studio requires authentication
URL = SERVER + "/v1/chat/completions"
MODEL = sys.argv[1]
NAME = "Flashcat"
RESUME = any(a in ("--continue", "-c") for a in sys.argv[2:])
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
    "you create Word documents (.docx) with write_docx and PDF documents with write_pdf; rename or move files with move_file. "
    "For changes to existing text files use edit_file: read the file first and give old_text exactly "
    "as it appears in the file. "
    "IMPORTANT: The writing and internet tools ask the user for confirmation themselves. "
    "So NEVER ask in the chat 'Shall I …?', but call the right tool right away "
    "as soon as the user wants something written, changed or moved. "
    "You cannot delete anything or run commands. "
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


def private_item(full):
    """The private item `full` belongs to - a key file, or the hidden file/folder in ~ or ~/Library - or None."""
    if PRIVATE_NAME.fullmatch(os.path.basename(full)):
        return full
    if full == HOME or not inside(full, HOME):
        return None
    top = os.path.join(HOME, os.path.relpath(full, HOME).split(os.sep)[0])
    name = os.path.basename(top)
    return top if (name.startswith(".") or name == "Library") and not inside(ROOT, top) else None


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
        answer = input(f"  {RED}? {what} [Y/N]{RESET} ").strip().lower()
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


def check_target(path, allowed_ext):
    """Returns (full, rel) for a file that may be written, or raises ValueError with a refusal reason."""
    full = resolve(path, ask=True)
    rel = os.path.relpath(full, ROOT)
    if os.path.splitext(full)[1].lower() not in allowed_ext:
        raise ValueError(f"Refused: only {', '.join(sorted(allowed_ext))} are allowed.")
    if rel.split(os.sep)[0] == BACKUP_DIR:
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
        answer = input(f"  {ORANGE}?{RESET} {question} {DIM}{choices}{RESET} ").strip().lower()
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


def move_file(source, destination):
    src = resolve(source, ask=True)
    if src == ROOT:
        return "Refused: the start folder itself cannot be moved."
    if not os.path.exists(src):
        return f"Error: {source} does not exist."
    dst = resolve(destination, ask=True)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    src_rel, dst_rel = clean(os.path.relpath(src, ROOT)), clean(os.path.relpath(dst, ROOT))
    if BACKUP_DIR in (src_rel.split(os.sep)[0], dst_rel.split(os.sep)[0]):
        return "Refused: the backup folder is off limits."
    if os.path.exists(dst):
        return f"Refused: {dst_rel} already exists – nothing is overwritten."
    if dst.startswith(src + os.sep):
        return "Refused: a folder cannot be moved into itself."
    rename = os.path.dirname(src) == os.path.dirname(dst)
    ui_break()
    print()
    print(card(f"{ORANGE}{'renames' if rename else 'moves'}{RESET}", [f"{src_rel}  {DIM}→{RESET}  {dst_rel}"]))
    if not confirm("Rename?" if rename else "Move?"):
        print()
        return "The user declined. Nothing was moved."
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    journal.append({"type": "move", "src": src, "dst": dst, "src_rel": src_rel, "dst_rel": dst_rel})
    stats["moved"] += 1
    print(f"  {GREEN}✓{RESET} {DIM}moved:{RESET} {src_rel} {DIM}→{RESET} {file_link(dst_rel)}")
    return f"Moved: {src_rel} → {dst_rel}"


def undo():
    """Reverts the newest change of this chat after confirmation. Returns a description or None."""
    if not journal:
        print("Nothing has been changed in this chat that could be undone.\n")
        return None
    e = journal[-1]
    if e["type"] == "move":
        desc = f"move {e['dst_rel']} back to {e['src_rel']}"
    elif e["backup"]:
        desc = f"restore {clean(e['rel'])} to the version before the change"
    else:
        desc = f"remove the newly created file {clean(e['rel'])} (a copy goes to {BACKUP_DIR})"
    if not confirm(f"Undo: {desc}?"):
        print()
        return None
    if e["type"] == "move":
        if os.path.exists(e["src"]):
            print(f"Not possible: {e['src_rel']} exists again by now.\n")
            return None
        os.makedirs(os.path.dirname(e["src"]), exist_ok=True)
        shutil.move(e["dst"], e["src"])
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
         "view_image": view_image, "web_search": web_search, "fetch_url": fetch_url}


# ---------- model ----------

INTERNAL_REPLIES = ("Understood.", "All right, I have the conversation so far in mind.")
state = {"thinking": False, "context": 32768, "used": 0, "last_answer": "", "tok_s": 0.0, "turn_start": 0.0,
         "phase": "thinking", "tools_shown": False}
# for the receipt shown when the chat ends
stats = {"start": time.time(), "questions": 0, "created": set(), "changed": set(), "moved": 0, "undone": 0}


def loaded_context_length():
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


CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # everything except tab and newline


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

    def _inline(self, text):
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
    if not state["thinking"]:
        payload["reasoning_effort"] = "none"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(URL, json.dumps(payload).encode(), headers)
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
                if e.code in (401, 403):
                    raise RuntimeError("LM Studio requires an API key. Turn off \"Require authentication\" in "
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
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg


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
              "move_file": "moves", "view_image": "looks at", "web_search": "searches the web for",
              "fetch_url": "opens"}
INTERACTIVE_TOOLS = {"write_file", "edit_file", "write_docx", "write_pdf", "move_file"}
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
    raw = clean(str(args.get("path") or args.get("source") or args.get("pattern") or args.get("query")
                    or args.get("url") or "."))
    verb = TOOL_VERBS.get(name, name)
    room = max(10, term_width() - len(verb) - 22)
    short = raw if len(raw) <= room else raw[: room - 1] + "…"
    if name in ("search", "web_search"):
        target = f"“{short}”"
    elif name == "fetch_url":
        target = urllib.parse.urlparse(raw).netloc or short
    elif raw == ".":
        target = "the folder"
    else:
        target = link(short, os.path.join(ROOT, raw)) if os.path.exists(os.path.join(ROOT, raw)) else short
    text = f"  {DIM}◇{RESET} {verb} {target}"
    if name not in ("search", "web_search", "fetch_url") and private_item(os.path.realpath(os.path.join(ROOT, raw))):
        text = f"  {RED}◇ {verb} {short} · private{RESET}"
    print(text, end="", flush=True)
    tool_line.update(open=True, text=text)
    state["tools_shown"] = True
    started = time.time()
    try:
        result = str(FUNCS[name](**args))
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
                   "messages": without_images(messages[1:])}, f, ensure_ascii=False)


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


def resume(messages, session_id, data):
    state.update(session=session_id, used=0, last_answer="")
    journal.clear()
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
    known = {"gemma-4-26b-a4b-it-qat": "Gemma 4 · 26B"}
    return known.get(MODEL, MODEL)


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
    hints.append("/help for commands")
    rest = [f"{DIM}{pretty_model()} · {round(state['context'] / 1024)}k context{RESET}",
            f"{DIM}{folder}{RESET}", f"{DIM}{' · '.join(hints)}{RESET}"]
    title = f"{ORANGE}✻{RESET} {BOLD}Flashcat{RESET}"
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
    first = input(f"{ORANGE}❯{RESET} ")
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
    return "\n".join(lines).strip()


COMMANDS = ["/help", "/undo", "/copy", "/save", "/resume", "/clear", "/compact", "/context", "/think", "/exit"]
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
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


MENTION = re.compile(r'(?:^|(?<=\s))@(?:"([^"]+)"|(\S+))')


def attach_mentions(text):
    """Resolves @file / @"file name" in the user's text. Returns message content (str or multi-part list)."""
    blocks, images = [], []
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
    if not images:
        return content
    parts = [{"type": "text", "text": content}]
    for rel, url in images:
        parts += [{"type": "text", "text": f"(Image: {rel})"}, {"type": "image_url", "image_url": {"url": url}}]
    return parts


HELP_COMMANDS = [
    ("/help", "this overview", ""),
    ("/undo", "undo the last file change", ""),
    ("/copy", "copy the last answer", ""),
    ("/save", "save the last answer as a file", "/save name.md"),
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
    ("FLASHCAT.md", "standing instructions", "in the folder or ~/.flashcat/"),
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
        name = arg or f"answer-{time.strftime('%Y%m%d-%H%M')}.md"
        if not os.path.splitext(name)[1]:
            name += ".md"
        try:
            full, rel = check_target(name, WRITE_EXT)
        except ValueError as e:
            print(f"{e}\n")
            return messages
        base, ext = os.path.splitext(full)
        n = 2
        while os.path.exists(full):
            full, n = f"{base}-{n}{ext}", n + 1
        save_with_backup(full, os.path.relpath(full, ROOT), text + "\n")
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
        return input(f"  {ORANGE}?{RESET} Start here anyway? {DIM}[Y/N]{RESET} ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def main():
    os.makedirs(HOME_DIR, exist_ok=True)
    os.chmod(HOME_DIR, 0o700)  # saved chats contain file contents; other accounts on this Mac must not read them
    if not os.environ.get("FLASHCAT_FOLDER_CONFIRMED") and not confirm_broad_folder():
        return
    setup_completion()
    system, loaded = system_prompt()
    messages = [{"role": "system", "content": system}]
    state["context"] = loaded_context_length()
    state["session"] = new_session_id()
    sessions = list_sessions()
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
        state.update(turn_start=time.time(), tok_s=0.0, tools_shown=False)
        stats["questions"] += 1
        print()
        messages.append({"role": "user", "content": attach_mentions(user)})
        try:
            nudged = False
            for step in range(15):
                state["phase"] = "thinking" if step == 0 else "working"
                msg = call_model(messages)
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


if __name__ == "__main__":
    if MODEL == "--confirm-folder":
        sys.exit(0 if confirm_broad_folder() else 1)
    main()
