#!/usr/bin/env python3
"""Source de secours pour le serveur : cotes Betclic FR (bookmaker « betclic_fr ») via The Odds API.
Utilisée seulement si betclic.fr refuse le serveur ET si la variable d'environnement ODDS_API_KEY existe
(clé gardée côté serveur, jamais dans la page ni dans le dépôt). Écrit raw_odds.json au même format que fetch_odds.py.
Marchés : vainqueur/1N2 (h2h) et total de buts (totals, foot). Coût : 1 crédit par sport et par marché."""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("ODDS_API_KEY", "")
API = "https://api.the-odds-api.com/v4"
WANTED = os.environ.get("ODDS_SPORTS", "soccer_france_ligue_one,soccer_epl,soccer_spain_la_liga,soccer_italy_serie_a,"
                        "soccer_germany_bundesliga,soccer_uefa_champs_league,soccer_uefa_europa_league,"
                        "soccer_brazil_campeonato,basketball_nba,basketball_euroleague,tennis_atp").split(",")
MARKETS = os.environ.get("ODDS_MARKETS", "h2h,totals")


def get(path, **q):
    q["apiKey"] = KEY
    with urllib.request.urlopen(f"{API}{path}?{urllib.parse.urlencode(q)}", timeout=25) as r:
        return json.loads(r.read()), r.headers.get("x-requests-remaining")


def main():
    if not KEY:
        print("ODDS_API_KEY absente", file=sys.stderr); sys.exit(2)
    now = datetime.now(timezone.utc)
    sports, _ = get("/sports")  # gratuit
    keys = [s["key"] for s in sports if s.get("active") and any(s["key"] == w or s["key"].startswith(w + "_") for w in WANTED)]
    matches, remaining, errors = [], None, []
    for k in keys:
        try:
            evs, remaining = get(f"/sports/{k}/odds", bookmakers="betclic_fr", oddsFormat="decimal", dateFormat="iso",
                                 markets=MARKETS if k.startswith("soccer") else "h2h",
                                 commenceTimeFrom=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 commenceTimeTo=(now + timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        except Exception as e:
            errors.append(f"{k}: {e}"); continue
        sport = "football" if k.startswith("soccer") else "basketball" if k.startswith("basketball") else "tennis"
        for e in evs:
            bk = next((b for b in e.get("bookmakers", []) if b["key"] == "betclic_fr"), None)
            if not bk:
                continue
            mk = {m["key"]: m for m in bk.get("markets", [])}
            h2h = mk.get("h2h")
            if not h2h:
                continue
            o = {x["name"]: x["price"] for x in h2h["outcomes"]}
            sels = [{"name": e["home_team"], "odds": o.get(e["home_team"])}]
            if "Draw" in o:
                sels.append({"name": "Nul", "odds": o["Draw"]})
            sels.append({"name": e["away_team"], "odds": o.get(e["away_team"])})
            if any(not s["odds"] for s in sels):
                continue
            markets = [{"name": "Résultat du match", "label": "1N2" if len(sels) == 3 else "Vainqueur", "selections": sels}]
            if "totals" in mk and sport == "football":
                tot = []
                for x in mk["totals"]["outcomes"]:
                    pt = str(x.get("point")).replace(".", ",")
                    tot.append({"name": f"{'+' if x['name'] == 'Over' else '-'} de {pt}", "odds": x["price"]})
                if len(tot) >= 2:
                    markets.append({"name": "Nombre total de buts (t. rég)", "label": "Total buts", "selections": tot})
            matches.append({"id": "oa-" + e["id"], "name": f"{e['home_team']} - {e['away_team']}", "start": e["commence_time"],
                            "sport": sport, "competition": e.get("sport_title", ""), "contestants": [], "markets": markets,
                            "mycombis": [], "url": None})
    if not matches:
        print("The Odds API : aucun match Betclic", errors, file=sys.stderr); sys.exit(1)
    out = {"fetchedAt": now.isoformat().replace("+00:00", "Z"), "source": "Betclic FR via The Odds API",
           "matches": sorted(matches, key=lambda m: m["start"]), "errors": errors}
    json.dump(out, open("raw_odds.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"The Odds API : {len(matches)} matchs Betclic, {len(keys)} sports, crédits restants {remaining}")


if __name__ == "__main__":
    main()
