# Security

Flashcat works with your files and can reach the internet, so security matters.

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
- the installer doing anything other than what it describes

## Supported versions

Only the latest version on the `main` branch is supported. Update by running the install command again.
