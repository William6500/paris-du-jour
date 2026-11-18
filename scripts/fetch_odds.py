#!/usr/bin/env python3
"""Étape 1 : relève les cotes Betclic (betclic.fr) depuis l'état SSR des pages publiques.
Écrit raw_odds.json : liste des matchs + marchés détaillés (pages match football) + MyCombi Betclic."""
import json, re, sys, gzip, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BASE = "https://www.betclic.fr"
PAGES = [
    "/football-sfootball", "/", "/tennis-stennis", "/basket-ball-sbasketball", "/rugby-srugby_union",
    "/football-sfootball/ligue-1-mcdonald-s-c4", "/football-sfootball/angl-premier-league-c3",
    "/football-sfootball/espagne-laliga-c7", "/football-sfootball/italie-serie-a-c6",
    "/football-sfootball/allemagne-bundesliga-c5", "/football-sfootball/ligue-des-champions-c8",
    "/football-sfootball/ligue-des-nations-uefa-c22676", "/basketball-sbasketball/nba-c13",
    "/tennis-stennis/nitto-atp-finals-c1",
]
STATE_RE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
HREF_RE = re.compile(r'href="(/[^"?#]+-m(\d+))"')
HORIZON_H = 40
MAX_MATCH_PAGES = 40
# Marchés gardés (pages match football). Le reste (buteurs, score exact...) est ignoré.
KEEP = [
    (r"^Résultat du match", "1N2"),
    (r"^Double chance \(t\. rég\)$", "Double chance"),
    (r"^Nombre total de buts", "Total buts"),
    (r"^Les 2 équipes marquent \(t\. rég\)$", "Les 2 marquent"),
    (r"^Les 2 équipes marquent ou \+ de 2,5", "2 marquent ou +2,5"),
    (r"^Total de buts \(t\. rég\) - ", "Buts équipe"),
    (r"^1ère mi-temps \(seule\) - Résultat", "1re MT"),
    (r"^2ème mi-temps \(seule\) - Résultat", "2e MT"),
    (r"^Première équipe à marquer", "1re équipe à marquer"),
]


def get(path):
    req = urllib.request.Request(BASE + path if path.startswith("/") else path,
                                 headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def state_of(html):
    m = STATE_RE.search(html)
    return json.loads(m.group(1)) if m else None


def payloads(state):
    for v in state.values():
        if isinstance(v, dict) and isinstance(v.get("response"), dict):
            p = v["response"].get("payload")
            if isinstance(p, dict):
                yield p


def sel_list(mk):
    out = []
    for s in mk.get("mainSelections") or []:
        out.append(s)
    for row in mk.get("selectionMatrix") or []:
        for c in row.get("selections", []):
            s = (c.get("selectionOneof") or {}).get("selection")
            if s:
                out.append(s)
    return [{"name": s["name"], "odds": float(s["odds"])} for s in out if s.get("odds") and s.get("status") == 1]


def label_for(name):
    for rx, lab in KEEP:
        if re.search(rx, name):
            return lab
    return None


def parse_match_page(html):
    st = state_of(html)
    if not st:
        return None
    for p in payloads(st):
        if "match" in p and isinstance(p["match"], dict):
            m = p["match"]
            markets = []
            for sc in m.get("subCategories") or []:
                for mk in sc.get("markets") or []:
                    subs = mk.get("groupMarkets") or [mk]
                    for g in subs:
                        name = g.get("betslipName") or g.get("name") or ""
                        lab = label_for(name)
                        sels = sel_list(g)
                        if lab and len(sels) >= 2:
                            markets.append({"name": name, "label": lab, "selections": sels})
            combis = []
            for c in p.get("topMycombis") or []:
                legs = [{"market": s["marketName"], "name": s["selection"]["name"], "odds": float(s["selection"]["odds"])}
                        for s in c.get("selections", []) if s.get("selection")]
                if c.get("computedOdds") and legs and not any(re.search(r"Buteur", l["market"]) for l in legs):
                    combis.append({"odds": float(c["computedOdds"]), "legs": legs})
            return {"markets": markets, "mycombis": combis, "round": (m.get("competitionInfo") or {}).get("roundName")}
    return None


def main():
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HORIZON_H)
    matches, urls, errors, ok = {}, {}, [], 0
    def fetch_page(path):
        try:
            return path, get(path)
        except Exception as e:  # noqa
            return path, e
    with ThreadPoolExecutor(8) as ex:
        pages = list(ex.map(fetch_page, PAGES))
    for path, html in pages:
        if isinstance(html, Exception):
            errors.append(f"{path}: {html}"); continue
        try:
            for u, mid in HREF_RE.findall(html):
                urls.setdefault(mid, u)
            st = state_of(html)
            if not st:
                errors.append(f"{path}: pas d'état SSR"); continue
            ok += 1
            for p in payloads(st):
                for x in p.get("matches") or []:
                    mk = x.get("market") or {}
                    sels = [s for s in mk.get("mainSelections", []) if s.get("odds")]
                    if x.get("isLive") or len(sels) < 2 or any(s.get("status") != 1 for s in sels):
                        continue
                    start = datetime.fromisoformat(x["matchDateUtc"].replace("Z", "")[:19]).replace(tzinfo=timezone.utc)
                    if not (now < start < horizon):
                        continue
                    comp = x.get("competition") or {}
                    matches[x["matchId"]] = {
                        "id": x["matchId"], "name": x["name"],
                        "start": start.isoformat().replace("+00:00", "Z"),
                        "sport": (comp.get("sport") or {}).get("code", ""),
                        "competition": comp.get("name", ""),
                        "contestants": [{"name": c.get("name"), "form": c.get("formResults") or []} for c in x.get("contestants", [])],
                        "markets": [{"name": mk.get("name", ""), "label": "1N2" if len(sels) == 3 else "Vainqueur",
                                     "selections": [{"name": s["name"], "odds": float(s["odds"])} for s in sels]}],
                        "mycombis": [],
                    }
        except Exception as e:  # noqa
            errors.append(f"{path}: {e}")
    if not ok or not matches:
        print("Échec de la relève:", errors, file=sys.stderr); sys.exit(1)
    for mid, m in matches.items():
        m["url"] = BASE + urls[mid] if mid in urls else None
    todo = [m for m in sorted(matches.values(), key=lambda m: m["start"]) if m["sport"] == "football" and m["url"]][:MAX_MATCH_PAGES]

    def detail(m):
        try:
            return m, parse_match_page(get(m["url"]))
        except Exception as e:  # noqa
            return m, e
    with ThreadPoolExecutor(8) as ex:
        for m, res in ex.map(detail, todo):
            if isinstance(res, dict) and res["markets"]:
                m["markets"] = res["markets"]; m["mycombis"] = res["mycombis"]; m["round"] = res["round"]
            elif isinstance(res, Exception):
                errors.append(f"{m['url']}: {res}")
    out = {"fetchedAt": now.isoformat().replace("+00:00", "Z"), "source": "betclic.fr",
           "matches": sorted(matches.values(), key=lambda m: m["start"]), "errors": errors}
    json.dump(out, open("raw_odds.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Betclic : {len(matches)} matchs, {len(todo)} pages match, {ok} pages liste, erreurs: {len(errors)}")


if __name__ == "__main__":
    main()
