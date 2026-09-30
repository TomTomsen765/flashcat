#!/usr/bin/env bash
# Removes Flashcat. Keeps LM Studio and downloaded models; asks before deleting saved chats.
#
#   curl -fsSL https://raw.githubusercontent.com/TomTomsen765/flashcat/main/uninstall.sh | bash
{ # everything in braces: bash reads it completely before running it, so a download that
  # breaks off in the middle runs nothing instead of half the script
set -euo pipefail
BIN="$HOME/.local/bin"
DIM=$'\033[2m' GREEN=$'\033[32m' ORANGE=$'\033[1;38;2;217;119;87m' RESET=$'\033[0m'

for f in flashcat flashcat-chat.py flashcat-cleanup; do
  rm -f "$BIN/$f"
done
printf '  %s✓%s Flashcat removed from %s\n' "$GREEN" "$RESET" "$BIN"
if command -v brew >/dev/null 2>&1 && brew list flashcat >/dev/null 2>&1; then
  printf '  %sInstalled with Homebrew as well – remove that with: brew uninstall flashcat%s\n' "$DIM" "$RESET"
fi
if command -v ollama >/dev/null 2>&1; then  # Flashcat's small model settings copies (the model itself stays)
  for m in $(ollama list 2>/dev/null | awk 'NR > 1 && $1 ~ /^flashcat-/ {print $1}'); do
    ollama rm "$m" >/dev/null 2>&1 || true
  done
fi

if [[ -d $HOME/.flashcat ]]; then
  answer=""
  [[ -r /dev/tty ]] && read -r -p "  ${ORANGE}?${RESET} Also delete saved chats and settings in ~/.flashcat? ${DIM}[Y/N]${RESET} " answer </dev/tty || true
  if [[ $answer == [yY]* ]]; then
    rm -rf "$HOME/.flashcat"
    printf '  %s✓%s ~/.flashcat deleted\n' "$GREEN" "$RESET"
  else
    printf '  %s~/.flashcat kept%s\n' "$DIM" "$RESET"
  fi
fi
printf '  %sLM Studio and the downloaded model stay. Delete the model in LM Studio if you no longer need it.%s\n' "$DIM" "$RESET"
}
