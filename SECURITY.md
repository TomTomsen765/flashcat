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
- Starting in a very broad folder (home folder, `/`, `/Users`, `/Volumes`) asks first, before
  anything is loaded.
- Flashcat's own file tools cannot delete anything. They only write text files of common types, Word
  and PDF documents; they refuse to write through hard links; moving (also many files at once) never
  overwrites, and every move list is checked completely before you are asked.
- Every write, change and move shows a preview and needs your `Y`. Old versions go to
  `.flashcat-backup/`, which Flashcat itself cannot write to or move, which must not be a link to
  somewhere else, and which gets its own `.gitignore`.

**Commands**
- `run_command` shows the full command and runs only after your `Y`. Commands that can delete or
  overwrite files are marked in red.
- It runs in a macOS sandbox (`sandbox-exec`) that Flashcat builds for the start folder. The sandbox
  enforces, for the command and everything it starts:
  - no network at all – no connections, not even name lookups (which could carry data out)
  - writing only inside the start folder and temporary folders – never in `.flashcat-backup/`
  - no reading of user files outside the start folder (home folders, other users, external drives);
    only system files and developer tools (`~/.local/bin`, `~/.cargo`, `~/.nvm`, `~/.gitconfig`, …)
  - private data (`~/.*`, `~/Library`) and key files stay locked, even when you unlocked them for the
    file tools in this chat
  - no opening of apps or web pages (Launch Services, Apple Events), no clipboard, no keychain
- No input (stdin is empty), the API key is removed from the environment, and the command is stopped
  after 2 minutes (the model can ask for up to 10).

**Internet**
- Every web search and page fetch needs your `Y` – before any network traffic, including the DNS
  lookup, so nothing can be sent out inside a host name before you agree.
- The full address or search term is shown (up to 1000 characters; longer ones are refused).
- Addresses that are not public (this Mac, the local network, link-local, …) are refused, and
  every redirect is checked again.
- Control characters and invisible text-direction marks are removed from everything shown in the
  terminal, so a file name or address cannot be disguised.

**Instructions**
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
  `main` branch. Every push runs the safety tests in `tests/` on GitHub Actions.
- These protect against a broken or half-finished state reaching you – they do not protect against a
  compromised GitHub account (whoever controls it could publish a release). The account uses two-factor
  authentication. For full control, read the code and install from a copy you checked (see README).

### Limits

- **Your answers are the last line of defense.** If you allow a change, a command, a web request or
  access to private data, it happens. Read what the prompt shows before you answer `Y`.
- A command you allowed can delete or change anything **inside the start folder** (e.g. `rm -rf .`),
  and `/undo` cannot bring that back. Use git or a backup for folders you care about.
- Everything you or Flashcat open is given to the local model, including files you attach with `@`.
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
