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
- Flashcat cannot delete files or run commands. It only writes text files of common types, Word and
  PDF documents; it refuses to write through hard links; moving never overwrites.
- Every write, change and move shows a preview and needs your `Y`. Old versions go to
  `.flashcat-backup/`, which Flashcat itself cannot write to or move, which must not be a link to
  somewhere else, and which gets its own `.gitignore`.

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
  breaks off does nothing. `flashcat --update` runs the same installer from this repository.

### Limits

- **Your answers are the last line of defense.** If you allow a change, a web request or access to
  private data, it happens. Read what the prompt shows before you answer `Y`.
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

- reading or writing files outside the folder Flashcat was started in
- changing files or accessing the internet **without** the confirmation prompt
- reaching addresses on the local computer or network
- reading private data (see above) without the red confirmation
- using a folder's `FLASHCAT.md` without asking
- the installer doing anything other than what it describes

## Supported versions

Only the latest version on the `main` branch is supported. Update with `flashcat --update`.
