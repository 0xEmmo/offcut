#!/usr/bin/env bash
# one command: installs into .venv on first run, then starts offcut on :7878
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "offcut needs ffmpeg on PATH (brew/apt install ffmpeg)"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
[ -f .venv/.ok ] || { pip install -q -r requirements.txt && touch .venv/.ok; }
exec python -m offcut "$@"
