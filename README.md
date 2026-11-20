# Kote

PWA (style Liquid Glass) : paris sportifs du jour avec les cotes publiques de Betclic.fr, le contexte des matchs
et un calcul de mise selon le solde.

Pipeline (`scripts/refresh_and_push.sh`) :
1. `fetch_odds.py` : cotes avant-match Betclic (pages liste + pages match football : 1N2, double chance, total de buts,
   les 2 marquent, buts par équipe, résultat 1re/2e mi-temps, 1re équipe à marquer) et les MyCombi Betclic (vraies cotes même match).
2. `enrich_espn.py` : API publique ESPN : forme (5 derniers), classement, face-à-face, meilleurs joueurs, blessés et prédicteur (NBA).
3. `build.py` : fusionne avec `notes.json` (absences, compos, ambiance, sources presse), calcule les probabilités et écrit `data.json`.

Betclic bloque les serveurs GitHub Actions (403) : la relève tourne depuis une autre machine.
Le solde reste dans le navigateur (localStorage). Jeu responsable : Joueurs Info Service 09 74 75 13 13. 18+.
