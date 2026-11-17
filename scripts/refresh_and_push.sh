#!/usr/bin/env bash
# Relève les cotes Betclic et publie data.json sur GitHub Pages (à lancer depuis une machine que Betclic ne bloque pas).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull -q --rebase origin main
python3 scripts/fetch_odds.py
git add data.json
if git diff --cached --quiet; then echo "Pas de changement"; exit 0; fi
git -c user.name="paris-du-jour-bot" -c user.email="William6500@users.noreply.github.com" commit -qm "Cotes Betclic du $(TZ=Europe/Paris date '+%d/%m %H:%M')"
git push -q origin main
echo "Publié"
