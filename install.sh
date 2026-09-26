#!/usr/bin/env bash
# Flashcat installer - a local AI assistant for the macOS terminal.
#
#   curl -fsSL https://raw.githubusercontent.com/TomTomsen765/flashcat/main/install.sh | bash
#
# Installs flashcat into ~/.local/bin and downloads the default model (Gemma 4 26B, ~15.6 GB) through
# LM Studio. LM Studio itself must be installed first: https://lmstudio.ai
# Options (environment variables): FLASHCAT_SKIP_MODEL=1 skips the model download.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/TomTomsen765/flashcat/main"
MODEL_KEY="gemma-4-26b-a4b-it-qat"
MODEL_URL="https://huggingface.co/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF"
BIN="$HOME/.local/bin"
LMS="$HOME/.lmstudio/bin/lms"
SERVICE_PATTERN="/Contents/MacOS/(LM Studio|Bionic) --run-as-service"
FILES=(flashcat flashcat-chat.py flashcat-cleanup)

ORANGE=$'\033[1;38;2;217;119;87m' DIM=$'\033[2m' GREEN=$'\033[32m' RED=$'\033[31m' BOLD=$'\033[1m' RESET=$'\033[0m'
step() { printf '\n%s✻%s %s\n' "$ORANGE" "$RESET" "$1"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
fail() { printf '\n  %s✗%s %s\n\n' "$RED" "$RESET" "$1" >&2; exit 1; }
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

[[ -x $LMS ]] || fail "LM Studio was not found. Install it from ${BOLD}https://lmstudio.ai${RESET}, open it once,
    then run this installer again."
ok "LM Studio"

# ---------- program files ----------
step "Installing Flashcat"
mkdir -p "$BIN"
here=""  # set when run from a downloaded copy of the repository (not when piped from curl)
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
for f in "${FILES[@]}"; do
  if [[ -n $here && -f $here/bin/$f ]]; then  # running from a downloaded copy of the repository
    cp "$here/bin/$f" "$BIN/$f.new"
  else
    curl -fsSL "$REPO_RAW/bin/$f" -o "$BIN/$f.new" || fail "Download of $f failed."
  fi
  chmod +x "$BIN/$f.new"
  mv "$BIN/$f.new" "$BIN/$f"  # replace atomically, running Flashcat windows are not disturbed
done
ok "flashcat → $BIN"

if /usr/bin/python3 -m pip install --user --quiet --disable-pip-version-check pygments >/dev/null 2>&1; then
  ok "syntax highlighting (pygments)"
else
  note "pygments could not be installed – code is shown without colors."
fi

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
pgrep -f "$SERVICE_PATTERN" >/dev/null || pgrep -xq "LM Studio|Bionic" || service_running_before=
if "$LMS" ls --llm --json 2>/dev/null | grep -q "\"$MODEL_KEY\""; then
  ok "Gemma 4 26B is already downloaded"
elif [[ ${FLASHCAT_SKIP_MODEL:-} == 1 ]]; then
  note "Skipped. Download it later with:  $LMS get $MODEL_URL"
else
  note "Downloading Gemma 4 26B (about 15.6 GB) through LM Studio – this can take a while."
  "$LMS" get "$MODEL_URL" -y || fail "The model download failed. Try again later or run:  $LMS get $MODEL_URL"
  ok "Gemma 4 26B downloaded"
fi
if [[ -z $service_running_before ]]; then  # leave LM Studio as we found it
  sleep 1
  pkill -TERM -f "$SERVICE_PATTERN" 2>/dev/null || true
fi

# ---------- done ----------
cat <<EOF

  ${GREEN}✓${RESET} ${BOLD}Flashcat is installed.${RESET}

  Change into a project folder and start it:
      ${ORANGE}cd ~/Documents/my-project${RESET}
      ${ORANGE}flashcat${RESET}

  ${DIM}Update later with:  flashcat --update${RESET}

EOF
if [[ -n ${path_hint:-} ]]; then
  note "Open a new terminal window first (or run: source ~/.zshrc) so the flashcat command is found."
  echo
fi
