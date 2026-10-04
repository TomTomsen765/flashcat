# Security

Flashcat works with your files and can reach the internet, so security matters.

## How Flashcat protects you

Flashcat is driven by a language model. A model can be wrong, and a document or web page can try to
trick it ("prompt injection") – for example into sending the contents of your files somewhere. So
Flashcat does not rely on the model behaving well. The protections below are enforced by Flashcat's
own code, whatever the model asks for.

**Files**
- Access is limited to the start folder and its subfolders. Every path is resolved first (including
  `../` and symbolic links) and refused if it ends up outside.
- Private data is locked even inside the start folder: everything hidden directly in the home folder
  (`~/.*`), `~/Library`, and private key and credential files (`id_rsa`, `id_ed25519`, `*.pem`,
  `*.p12`, `*.pfx`, `*.keychain`, `.netrc`, `.git-credentials`). The check ignores upper/lower case,
  like macOS does. Unlocking needs your answer to a red prompt, covers only that one item and ends
  with the chat. Listing, searching and Tab completion skip locked items without asking.
- One exception, for the user only: an image file whose absolute path stands in the message you typed
  (dragged into the terminal, or a photo copied on the iPhone and pasted) is attached from outside the
  start folder, and Flashcat prints that it did. Only image types, judged by where the file really is
  (links are resolved); only your own words are searched – not piped input, files, web pages or anything
  the model writes, and the model's tools still cannot open that path. An image in a private place
  (`~/.*`, `~/Library`) asks in red and unlocks nothing; only the folder where macOS keeps the shared
  clipboard of your devices is attached without that question.
- Starting in a very broad folder (home folder, `/`, `/Users`, `/Volumes`) asks first, before
  anything is loaded.
- Flashcat's own file tools cannot delete anything. They only write plain text files of known types (documents,
  data, web pages, templates, code, settings – never types that macOS runs with a double click, such as
  `.command`), Word and PDF documents; they refuse to write through hard links; moving (also many files at once) never
  overwrites, and every move list is checked completely before you are asked.
- Every write, change and move shows a preview and needs your `Y`. Old versions go to
  `.flashcat-backup/`, which Flashcat itself cannot write to or move, which must not be a link to
  somewhere else, and which gets its own `.gitignore`.

**Commands**
- In plan mode (`/plan`) the model is offered the reading tools only, and the code refuses every tool
  that changes or runs something – without asking you, whatever the model calls.
- `run_command` shows the full command and runs only after your `Y`. Commands that can delete or
  overwrite files are marked in red.
- It runs in a macOS sandbox (`sandbox-exec`) that Flashcat builds for the start folder. The sandbox
  enforces, for the command and everything it starts:
  - no network at all – no connections, not even name lookups (which could carry data out)
  - writing only inside the start folder and in a temporary folder of its own (deleted afterwards) –
    never in `.flashcat-backup/`
  - no reading of user files outside the start folder (home folders, other users, external drives,
    other apps' temporary files); only system files and developer tools (`~/.local/bin`, `~/.cargo`,
    `~/.nvm`, `~/.gitconfig`, …)
  - private data (`~/.*`, `~/Library`) and key files stay locked, even when you unlocked them for the
    file tools in this chat
  - no system services except a short list that ordinary tools need to run at all (user names,
    time zone, logging, temporary folders, file types, system-wide settings). Everything else that
    macOS could do for the command outside the sandbox is out of reach: opening apps or web pages,
    the clipboard, the keychain, Shortcuts, notifications, Spotlight, disks
  - no settings of apps: `defaults write` is refused (macOS would write them in `~/Library` on the
    command's behalf, and some settings start programs), and only the system-wide settings
    (language, …) can be read
  - no signals to programs other than its own
  - no writing to or reading from terminal windows (text written there would bypass Flashcat's
    cleaning of control characters and could fake a question)
- No input (stdin is empty), the API key is removed from the environment, and the command is stopped
  after 2 minutes (the model can ask for up to 10).
- Nothing keeps running afterwards: when the command ends, everything it started is stopped – also
  processes that detached themselves (Flashcat finds them by a mark in their environment).
- The one command that runs without a question is the test command you set yourself with
  `/test <command>`: after Flashcat changed files it runs in the same sandbox (shown in a card), and
  its output goes back to the model, at most three times per question. It is stored per folder in
  `~/.flashcat/test-commands.json` – outside the start folder, where neither a downloaded project nor
  the model's tools nor a sandboxed command can write. What it runs (your test files) can of course
  have been changed by the model, with your confirmation.
- git can run programs by itself (hooks, and settings like `core.fsmonitor`, `core.pager` or
  `alias.x = !…`) – the next time *you* use git, outside the sandbox. So after every command Flashcat
  compares the git hooks and settings of the repositories in the folder: new hooks and settings that
  can run programs are shown in red and undone unless you keep them. Normal changes (`git init`,
  `user.name`, remotes, branches) pass without a question. A git folder behind a `.git` file
  (`gitdir: …`) is checked too.

**Internet**
- Every web search and page fetch needs your `Y` – before any network traffic, including the DNS
  lookup, so nothing can be sent out inside a host name before you agree.
- The full address or search term is shown (up to 1000 characters; longer ones are refused).
- Addresses that are not public (this Mac, the local network, link-local, …) are refused, and
  every redirect is checked again.
- Control characters and invisible text-direction marks are removed from everything shown in the
  terminal, so a file name or address cannot be disguised.

**Instructions**
- Your own commands (`/name`) come from `~/.flashcat/commands/` only, never from the start folder,
  and cannot replace built-in commands.
- When the context is nearly full, the chat is summarized by the model itself (also with `/compact`).
  The summary replaces the earlier messages; text from a document or web page read before can end up
  in it like in any answer. Confirmations stay as they are.
- A `FLASHCAT.md` in the start folder could come from someone else. It is shown and only used after
  you allow it; Flashcat remembers its checksum and asks again when it changes. Links pointing
  outside the folder are ignored.

**Your data**
- The model runs locally in LM Studio; Flashcat talks to it on `localhost` only.
- Chats and settings are stored in `~/.flashcat`, readable only by your user account.

**Installer**
- `install.sh` and `uninstall.sh` are read completely before anything runs, so a download that
  breaks off does nothing.
- The installer and `flashcat --update` install the newest **release** (a tagged version), not the
  `main` branch. The install command itself loads the copy of `install.sh` that is attached to the
  newest release. Every push runs the safety tests in `tests/` on GitHub Actions.
- Releases are immutable on GitHub: a published release and the files attached to it cannot be changed,
  and a repository rule stops version tags (`v*`) from being moved or deleted.
- The installer checks that the files it downloaded really are the release's version before it
  replaces anything, and that the version name GitHub returned looks like one (`v1.2.3`) before it
  becomes part of a download address.
- All downloads refuse anything but https, also after a redirect (`curl --proto '=https' --tlsv1.2`).
- The only third-party code is the optional `pygments` package (colored code). The installer installs
  one fixed version and only if the file's SHA-256 matches the one written in `install.sh`
  (`pip --require-hashes`, wheel only, so no setup script runs). The Homebrew formula does not install it.
- The GitHub Actions workflow is pinned to a fixed commit of `actions/checkout` and has read-only
  access; the repository only allows GitHub's own actions, pinned to a commit. Releases are made by
  hand, not by a workflow.
- The Homebrew formula names the release archive with its SHA-256.
- These protect against a broken or half-finished state reaching you – they do not protect against a
  compromised GitHub account (whoever controls it could publish a release). The account uses two-factor
  authentication. For full control, read the code and install from a copy you checked (see README).

### Limits

- **Your answers are the last line of defense.** If you allow a change, a command, a web request or
  access to private data, it happens. Read what the prompt shows before you answer `Y`.
- A command you allowed can delete or change anything **inside the start folder** (e.g. `rm -rf .`),
  and `/undo` cannot bring that back. Use git or a backup for folders you care about.
- For system services the sandbox allows only a short list; for everything else (which files can be
  read, which other operations are possible) it starts from "allowed" and blocks what is known to
  lead outside. A way out that nobody has thought of yet is possible – reports are very welcome.
- A few programs do not work inside the sandbox because they need a blocked service or a sandbox of
  their own: converting HTML with `textutil`, `swift build` (works with `--disable-sandbox`), tools
  that open windows or play sound.
- A command can also change files that *other* programs later run outside the sandbox – for example
  a `Makefile`, `package.json` scripts or a build script. Flashcat guards git's own hooks and settings
  (see above), not these. Look at what a command changed before you run the project yourself.
- Everything you or Flashcat open is given to the local model, including files you attach with `@`.
- Text you paste into your message counts as your own words: if it contains the path of an image on
  your Mac, that image is attached (you see the line "attaches … (from outside the folder)").
- Files in the start folder that are not in the private list (for example `.env` files in a project)
  are readable. Start Flashcat in the folder you want to work on, not a bigger one.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead, report them privately:
go to the [Security tab](https://github.com/TomTomsen765/flashcat/security) of this repository and click
**"Report a vulnerability"**.

Useful things to include: what you did, what happened, and what you expected to happen. I will get back
to you as soon as I can.

## What counts as a vulnerability

Anything that breaks Flashcat's safety promises, for example:

- reading or writing files outside the folder Flashcat was started in – also from a command
- a command reaching the network, private data, the clipboard or other apps despite the sandbox
- changing files, running commands or accessing the internet **without** the confirmation prompt
- reaching addresses on the local computer or network
- reading private data (see above) without the red confirmation
- using a folder's `FLASHCAT.md` without asking
- the installer doing anything other than what it describes

## Supported versions

Only the latest release is supported. Update with `flashcat --update` (or `brew upgrade flashcat`).
