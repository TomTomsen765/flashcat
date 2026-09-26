#!/usr/bin/env bash
# Removes Flashcat. Keeps LM Studio and downloaded models; asks before deleting saved chats.
#
#   curl -fsSL https://raw.githubusercontent.com/TomTomsen765/flashcat/main/uninstall.sh | bash
set -euo pipefail
BIN="$HOME/.local/bin"
DIM=$'\033[2m' GREEN=$'\033[32m' ORANGE=$'\033[1;38;2;217;119;87m' RESET=$'\033[0m'

for f in flashcat flashcat-chat.py flashcat-cleanup; do
  rm -f "$BIN/$f"
done
printf '  %s✓%s Flashcat removed from %s\n' "$GREEN" "$RESET" "$BIN"

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
