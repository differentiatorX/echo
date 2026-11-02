#!/usr/bin/env bash
# Echo one-line installer for macOS and Linux
#   curl -fsSL https://raw.githubusercontent.com/differentiatorX/echo/main/install.sh | bash
set -e
say() { printf "\n\033[36m> %s\033[0m\n" "$1"; }
printf "\n  echo  -  the few Reels that echo\n"

say "Checking ffmpeg"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg; else sudo apt-get update && sudo apt-get install -y ffmpeg; fi
fi

say "Checking Python"
command -v python3 >/dev/null || { echo "Install Python 3.10+ first: https://www.python.org/downloads/"; exit 1; }

say "Installing Echo"
DIR="$HOME/echo"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone https://github.com/differentiatorX/echo "$DIR"; fi
python3 -m pip install -q -e "$DIR" || python3 -m pip install -q --user -e "$DIR"

say "Connecting your AI agents"
python3 -m echoclip.cli connect all
python3 -m echoclip.cli install-skill --force

say "Checking everything"
python3 -m echoclip.cli doctor

printf "\n\033[32mAll set. Start with:  echoclip start\nOr ask your agent:     Use echo to make 4 Reels from <link>\033[0m\n\n"
