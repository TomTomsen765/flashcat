#!/usr/bin/env bash
# Flashcat installer - a local AI assistant for the macOS terminal.
#
#   curl --proto '=https' --tlsv1.2 -fsSL https://github.com/TomTomsen765/flashcat/releases/latest/download/install.sh | bash
#
# (That address is the copy of this file attached to the newest release.)
# Installs the newest release of flashcat into ~/.local/bin and downloads the default model (Gemma 4 26B,
# ~15.6 GB) through LM Studio (or Ollama). LM Studio, LM Studio Bionic (https://lmstudio.ai/download),
# Ollama (https://ollama.com) or llama.cpp (brew install llama.cpp) must be installed first.
# Options (environment variables): FLASHCAT_SKIP_MODEL=1 skips the model download,
#   FLASHCAT_REF=<tag or branch> installs that version instead of the newest release.
{ # everything in braces: bash reads it completely before running it, so a download that
  # breaks off in the middle runs nothing instead of half the script
set -euo pipefail

REPO="TomTomsen765/flashcat"
MODEL_KEY="gemma-4-26b-a4b-it-qat"
OLLAMA_MODEL="gemma4:26b"
LLAMACPP_FILE="gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
MODEL_URL="https://huggingface.co/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF"
BIN="$HOME/.local/bin"
LMS="$HOME/.lmstudio/bin/lms"
SERVICE_PATTERN="/Contents/MacOS/(LM Studio|Bionic) --run-as-service"
# is LM Studio running in any form: its background service, the app, or the headless version (llmster)?
lmstudio_running() { pgrep -f "$SERVICE_PATTERN" >/dev/null || pgrep -xq "LM Studio|Bionic|llmster"; }
FILES=(flashcat flashcat-chat.py flashcat-cleanup)
CURL=(curl --proto '=https' --tlsv1.2 -fsSL)  # https only, also when a download is redirected
# the one outside package (colored code): exactly this file from PyPI, checked by its SHA-256
PYGMENTS="pygments==2.21.0 --hash=sha256:2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9"

ORANGE=$'\033[1;38;2;217;119;87m' DIM=$'\033[2m' GREEN=$'\033[32m' RED=$'\033[31m' BOLD=$'\033[1m' RESET=$'\033[0m'
step() { printf '\n%s✻%s %s\n' "$ORANGE" "$RESET" "$1"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
fail() { printf '\n  %s✗%s %s\n\n' "$RED" "$RESET" "$1" >&2; exit 1; }
valid_ref() {  # a version name must look like one before it becomes part of a download address
  if [[ $2 == release ]]; then
    [[ $1 =~ ^v[0-9]+(\.[0-9]+)*$ ]]
  else
    [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && $1 != *..* ]]
  fi
}
ask()  {  # works even when the script is piped into bash (reads from the terminal)
  local answer=""
  if [[ -r /dev/tty ]]; then read -r -p "  ${ORANGE}?${RESET} $1 ${DIM}[Y/N]${RESET} " answer </dev/tty || true; fi
  [[ $answer == [yY] || $answer == [yY][eE][sS] ]]
}

cat <<EOF

${DIM}      /\\_/\\${RESET}
${DIM}     (${RESET} ${ORANGE}o.o${RESET} ${DIM})${RESET}    ${BOLD}Flashcat${RESET} installer
${DIM}      > ^ <${RESET}     ${DIM}a local AI assistant for your terminal${RESET}
EOF

# ---------- requirements ----------
step "Checking requirements"
[[ $(uname -s) == Darwin ]] || fail "Flashcat runs on macOS only (it uses macOS text recognition, PDF and Word tools)."
ok "macOS $(sw_vers -productVersion)"

if [[ $(uname -m) == arm64 ]]; then
  ok "Apple Silicon"
else
  note "This is an Intel Mac – Flashcat works, but the model will be very slow."
fi

ram_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if (( ram_gb >= 24 )); then
  ok "${ram_gb} GB memory"
elif (( ram_gb >= 16 )); then
  note "${ram_gb} GB memory – the default model needs about 16 GB, so other apps will have little room."
  ask "Continue anyway?" || fail "Cancelled. Tip: use a smaller model later with: flashcat --model <name>"
else
  fail "${ram_gb} GB memory is not enough for the default model (it needs about 16 GB)."
fi

if ! xcode-select -p >/dev/null 2>&1; then
  fail "Python is missing. Install Apple's command line tools with:  xcode-select --install
    Then run this installer again."
fi
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null \
  || fail "Python 3.8 or newer is needed (/usr/bin/python3). Update the command line tools and try again."
ok "Python $(/usr/bin/python3 -c 'import platform; print(platform.python_version())')"

# ---------- model server ----------
have() {
  case $1 in
    lmstudio) [[ -x $LMS ]] ;;
    ollama)   command -v ollama >/dev/null 2>&1 ;;
    llamacpp) command -v llama-server >/dev/null 2>&1 ;;
    *)        return 1 ;;
  esac
}
backend_name() { case $1 in lmstudio) echo "LM Studio" ;; ollama) echo "Ollama" ;; llamacpp) echo "llama.cpp" ;; esac; }
gguf_in() { [[ -n $(find "$@" -name "$LLAMACPP_FILE" 2>/dev/null | head -1) ]]; }
has_model() {  # is Gemma 4 already there? (checked in the model folders: asking the apps would start them)
  local store="${OLLAMA_MODELS:-$HOME/.ollama/models}/manifests/registry.ollama.ai/library"
  case $1 in
    lmstudio) gguf_in "$HOME/.lmstudio/models" ;;
    ollama)   [[ -f $store/${OLLAMA_MODEL%%:*}/${OLLAMA_MODEL#*:} || -f $store/gemma4-26b-lmstudio/latest ]] ;;
    llamacpp) gguf_in "$HOME/.lmstudio/models" "$HOME/.flashcat/models" ;;
  esac
}
backend_note() {
  if has_model "$1"; then
    if [[ $1 == llamacpp ]]; then echo "Gemma 4 downloaded ✓, needs the least memory"; else echo "Gemma 4 downloaded ✓"; fi
  elif [[ $1 == ollama ]] && has_model lmstudio; then echo "can use LM Studio's download of Gemma 4"
  elif [[ $1 == ollama ]]; then echo "not downloaded yet (about 18 GB)"
  else echo "not downloaded yet (15.6 GB)"; fi
}

step "Model server"
installed=""
for b in lmstudio ollama llamacpp; do
  if have "$b"; then installed="$installed $b"; ok "$(backend_name "$b")"; fi
done
if [[ -z $installed ]]; then
  note "Flashcat needs a program that runs the model on your Mac. None is installed yet:"
  note "  LM Studio   an app with a window, the one Flashcat is built and tested with   https://lmstudio.ai/download"
  note "  Ollama      a small app in the menu bar                                       https://ollama.com"
  note "  llama.cpp   no app at all, needs the least memory                             brew install llama.cpp"
  if command -v brew >/dev/null 2>&1 && ask "Install llama.cpp now with Homebrew?"; then
    brew install llama.cpp || fail "Installing llama.cpp failed. Try:  brew install llama.cpp"
    have llamacpp || fail "llama.cpp was installed, but llama-server is not on your PATH. Open a new terminal and run this installer again."
    installed=" llamacpp"
    ok "llama.cpp"
  else
    fail "No model server found. Install one of the three (LM Studio: open it once afterwards), then run this
    installer again."
  fi
fi
# which one: FLASHCAT_BACKEND, else an earlier choice, else the only one - with several, ask once and remember
backend="${FLASHCAT_BACKEND:-}"
[[ -z $backend && -f $HOME/.flashcat/backend ]] && backend=$(<"$HOME/.flashcat/backend")
if ! have "$backend"; then
  set -- $installed
  backend=$1
  if (( $# > 1 )) && { : </dev/tty; } 2>/dev/null; then  # only with a terminal to ask in
    default=1 i=0
    for b in "$@"; do
      i=$((i + 1))
      has_model "$b" && { default=$i; break; }
    done
    i=0
    for b in "$@"; do
      i=$((i + 1))
      printf '  %s  %-11s %s%s%s\n' "$i" "$(backend_name "$b")" "$DIM" "$(backend_note "$b")" "$RESET"
    done
    answer=""
    read -r -p "  ${ORANGE}?${RESET} Which one should Flashcat use? ${DIM}[1–$#, Enter = $default]${RESET} " answer </dev/tty || true
    answer=${answer:-$default}
    [[ $answer =~ ^[1-9]$ ]] && (( answer <= $# )) || fail "Please answer with a number from 1 to $#. Run the installer again."
    backend=${!answer}
    mkdir -p -m 700 "$HOME/.flashcat"
    printf '%s\n' "$backend" > "$HOME/.flashcat/backend"
    note "Change it later with:  flashcat --backend"
  fi
fi
ok "Flashcat uses $(backend_name "$backend")"

# ---------- program files ----------
step "Installing Flashcat"
mkdir -p "$BIN"
here=""  # set when run from a downloaded copy of the repository (not when piped from curl)
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [[ -z $here ]]; then
  # downloaded: install a published release, not the development state of the main branch
  ref=${FLASHCAT_REF:-}
  if [[ -n $ref ]]; then
    valid_ref "$ref" any || fail "FLASHCAT_REF is not a valid tag or branch name."
  else
    ref=$("${CURL[@]}" "https://api.github.com/repos/$REPO/releases/latest" \
          | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null) \
      || fail "GitHub could not be reached. Check the internet connection and try again."
    valid_ref "$ref" release || fail "GitHub returned an unexpected version name – nothing was changed."
  fi
  REPO_RAW="https://raw.githubusercontent.com/$REPO/$ref"
  note "version ${ref#v}"
fi
for f in "${FILES[@]}"; do
  if [[ -n $here && -f $here/bin/$f ]]; then  # running from a downloaded copy of the repository
    cp "$here/bin/$f" "$BIN/$f.new"
  else
    "${CURL[@]}" "$REPO_RAW/bin/$f" -o "$BIN/$f.new" || fail "Download of $f failed."
  fi
  chmod +x "$BIN/$f.new"
done
installed=$(/usr/bin/python3 "$BIN/flashcat-chat.py.new" --version | cut -d' ' -f2)
if [[ -z $here && ${ref:-} == v[0-9]* && $installed != "${ref#v}" ]]; then
  # right after a release, GitHub's file servers can still deliver the previous version for a few minutes
  for f in "${FILES[@]}"; do rm -f "$BIN/$f.new"; done
  fail "GitHub still delivered version $installed instead of ${ref#v} – nothing was changed. Please try again in a few minutes."
fi
for f in "${FILES[@]}"; do
  mv "$BIN/$f.new" "$BIN/$f"  # replace atomically, running Flashcat windows are not disturbed
done
ok "flashcat $installed → $BIN"

requirements=$(mktemp)
printf '%s\n' "$PYGMENTS" > "$requirements"
# --require-hashes: a file with another checksum is refused; --only-binary: no setup script is run
if /usr/bin/python3 -m pip install --user --quiet --disable-pip-version-check --require-hashes \
     --only-binary :all: --no-deps -r "$requirements" >/dev/null 2>&1; then
  ok "syntax highlighting (pygments)"
else
  note "pygments could not be installed – code is shown without colors."
fi
rm -f "$requirements"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  rc="$HOME/.zshrc"
  if ! grep -qs 'local/bin' "$rc"; then
    printf '\n# Flashcat\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
    ok "added ~/.local/bin to your PATH in ~/.zshrc"
  fi
  path_hint=1
fi

# ---------- model ----------
step "Model"
service_running_before=1
lmstudio_running || service_running_before=
if [[ $backend == llamacpp ]]; then
  if has_model llamacpp; then
    ok "Gemma 4 26B is already downloaded"
  else
    note "Gemma 4 26B (about 15.6 GB) is not downloaded yet – flashcat offers it on the first start."
  fi
elif [[ $backend == ollama ]]; then
  if has_model ollama || ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx "$OLLAMA_MODEL"; then
    ok "Gemma 4 26B is already downloaded"
  elif has_model lmstudio; then
    note "LM Studio has already downloaded Gemma 4 – flashcat offers to use it with Ollama on the first start (no download)."
  elif [[ ${FLASHCAT_SKIP_MODEL:-} == 1 ]]; then
    note "Skipped. Download it later with:  ollama pull $OLLAMA_MODEL (or when flashcat starts)"
  elif ! ollama list >/dev/null 2>&1; then
    note "Ollama is not running, so the model is not downloaded now – flashcat offers it on the first start."
  else
    note "Downloading Gemma 4 26B (about 18 GB) through Ollama – this can take a while."
    ollama pull "$OLLAMA_MODEL" || fail "The model download failed. Try again later or run:  ollama pull $OLLAMA_MODEL"
    ok "Gemma 4 26B downloaded"
  fi
elif "$LMS" ls --llm --json 2>/dev/null | grep -q "\"$MODEL_KEY\""; then
  ok "Gemma 4 26B is already downloaded"
elif [[ ${FLASHCAT_SKIP_MODEL:-} == 1 ]]; then
  note "Skipped. Download it later with:  $LMS get $MODEL_URL"
else
  note "Downloading Gemma 4 26B (about 15.6 GB) through LM Studio – this can take a while."
  "$LMS" get "$MODEL_URL" -y || fail "The model download failed. Try again later or run:  $LMS get $MODEL_URL"
  ok "Gemma 4 26B downloaded"
fi
if [[ $backend == lmstudio && -z $service_running_before ]]; then  # leave LM Studio as we found it
  sleep 1
  pkill -TERM -f "$SERVICE_PATTERN" 2>/dev/null || true
  pkill -TERM -x llmster 2>/dev/null || true
fi

# ---------- done ----------
cat <<EOF

  ${GREEN}✓${RESET} ${BOLD}Flashcat $("$BIN/flashcat" --version | cut -d' ' -f2) is installed.${RESET}

  Change into a project folder and start it:
      ${ORANGE}cd ~/Documents/my-project${RESET}
      ${ORANGE}flashcat${RESET}

  ${DIM}Update later with:  flashcat --update${RESET}

EOF
if [[ -n ${path_hint:-} ]]; then
  note "Open a new terminal window first (or run: source ~/.zshrc) so the flashcat command is found."
  echo
fi
}
