# Paris du jour

Page web (PWA, style Liquid Glass) qui propose les paris sportifs du jour à partir des cotes publiques de Betclic.fr,
classés par niveau de risque, avec un calcul de mise selon le solde.

- `scripts/fetch_odds.py` relève les cotes avant-match depuis les pages publiques de betclic.fr et écrit `data.json`.
- `scripts/refresh_and_push.sh` relève les cotes et publie `data.json` (Betclic bloque les serveurs GitHub Actions, la relève tourne donc ailleurs ; `.github/workflows/refresh.yml` reste lançable à la main).
- Aucune donnée personnelle : le solde reste dans le navigateur (localStorage).

Les probabilités affichées sont les probabilités implicites des cotes (marge retirée), pas des prédictions.
Jeu responsable : Joueurs Info Service 09 74 75 13 13. Interdit aux moins de 18 ans.
