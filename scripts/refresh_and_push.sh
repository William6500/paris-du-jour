#!/usr/bin/env bash
# Analyse automatique de la semaine : relève les cotes Betclic (7 jours) + contexte ESPN + actus, reconstruit data.json et publie sur GitHub Pages.
# À lancer depuis une machine que Betclic ne bloque pas (Betclic répond 403 aux serveurs GitHub Actions).
# Étape facultative avant ce script : mettre à jour notes.json (absences, compos, ambiance) par recherche web.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull -q --rebase origin main
python3 scripts/fetch_odds.py                       # 1. cotes Betclic (obligatoire)
python3 scripts/enrich_espn.py || echo "ESPN indisponible : contexte ESPN ignoré"   # 2. forme, H2H, classement (ESPN)
python3 scripts/auto_news.py || echo "Actus indisponibles : analyse auto des actus ignorée"   # 2 bis. actus 7 j (Google Actualités, cache 6 h)
python3 scripts/build.py                            # 3. modèle, picks, combinés -> data.json
git add -A data.json notes.json m
if git diff --cached --quiet; then echo "Pas de changement"; exit 0; fi
git -c user.name="paris-du-jour-bot" -c user.email="William6500@users.noreply.github.com" commit -qm "Cotes Betclic du $(TZ=Europe/Paris date '+%d/%m %H:%M')"
git push -q origin main
echo "Publié"
