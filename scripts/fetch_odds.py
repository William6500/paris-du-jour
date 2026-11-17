#!/usr/bin/env python3
"""Relève les cotes Betclic (betclic.fr) depuis l'état SSR des pages publiques et écrit data.json."""
import json, re, sys, gzip, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PAGES = [
    ("football", "https://www.betclic.fr/football-sfootball"),
    ("football", "https://www.betclic.fr/"),
    ("tennis", "https://www.betclic.fr/tennis-stennis"),
    ("basketball", "https://www.betclic.fr/basket-ball-sbasketball"),
    ("rugby", "https://www.betclic.fr/rugby-srugby_union"),
    ("football", "https://www.betclic.fr/football-sfootball/ligue-1-mcdonald-s-c4"),
    ("football", "https://www.betclic.fr/football-sfootball/angl-premier-league-c3"),
    ("football", "https://www.betclic.fr/football-sfootball/espagne-laliga-c7"),
    ("football", "https://www.betclic.fr/football-sfootball/italie-serie-a-c6"),
    ("football", "https://www.betclic.fr/football-sfootball/ligue-des-champions-c8"),
    ("football", "https://www.betclic.fr/football-sfootball/ligue-des-nations-uefa-c22676"),
    ("basketball", "https://www.betclic.fr/basketball-sbasketball/nba-c13"),
    ("tennis", "https://www.betclic.fr/tennis-stennis/nitto-atp-finals-c1"),
]
STATE_RE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def walk_matches(state):
    for v in state.values():
        if isinstance(v, dict) and isinstance(v.get("response"), dict):
            p = v["response"].get("payload")
            if isinstance(p, dict) and isinstance(p.get("matches"), list):
                yield from p["matches"]


def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=40)
    matches, errors, ok_pages = {}, [], 0
    for sport_hint, url in PAGES:
        try:
            m = STATE_RE.search(get(url))
            if not m:
                errors.append(f"{url}: pas d'état SSR"); continue
            ok_pages += 1
            for x in walk_matches(json.loads(m.group(1))):
                mk = x.get("market") or {}
                sels = [s for s in mk.get("mainSelections", []) if s.get("odds")]
                if x.get("isLive") or len(sels) < 2:
                    continue
                if any(s.get("status") != 1 for s in sels):
                    continue  # marché suspendu
                start = datetime.fromisoformat(x["matchDateUtc"].replace("Z", "")[:19]).replace(tzinfo=timezone.utc)
                if not (now < start < horizon):
                    continue
                comp = x.get("competition") or {}
                matches[x["matchId"]] = {
                    "id": x["matchId"],
                    "name": x["name"],
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "sport": (comp.get("sport") or {}).get("code", sport_hint),
                    "sportName": (comp.get("sport") or {}).get("name", sport_hint),
                    "competition": comp.get("name", ""),
                    "market": mk.get("name", ""),
                    "selections": [{"name": s["name"], "odds": float(s["odds"])} for s in sels],
                    "form": [
                        {"name": c.get("name"), "results": c.get("formResults") or []}
                        for c in x.get("contestants", [])
                    ],
                    "matchOfTheDay": bool(x.get("isMatchOfTheDay")),
                }
        except Exception as e:  # noqa
            errors.append(f"{url}: {e}")
    if ok_pages == 0 or not matches:
        print("Échec de la relève:", errors, file=sys.stderr)
        sys.exit(1)
    out = {
        "source": "Betclic.fr (pages publiques, cotes avant-match)",
        "sourceUrl": "https://www.betclic.fr/",
        "fetchedAt": now.isoformat().replace("+00:00", "Z"),
        "fetchedAtParis": now.astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
        "matches": sorted(matches.values(), key=lambda m: m["start"]),
        "errors": errors,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{len(out['matches'])} matchs écrits, {ok_pages} pages OK, erreurs: {errors}")


if __name__ == "__main__":
    main()
