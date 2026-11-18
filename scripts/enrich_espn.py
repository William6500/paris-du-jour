#!/usr/bin/env python3
"""Étape 2 : contexte public par match via l'API publique ESPN (site.api.espn.com).
Forme (5 derniers), confrontations directes, classement, meilleur buteur/marqueur, blessés (NBA),
prédicteur ESPN (NBA), compositions si publiées, titres d'actu. Écrit context.json."""
import json, re, unicodedata, urllib.request, gzip
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

API = "https://site.api.espn.com/apis/site/v2/sports"
SOCCER = ["bra.1", "uefa.wchampions", "uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.nations",
          "fra.1", "fra.2", "eng.1", "eng.2", "esp.1", "ita.1", "ger.1", "por.1", "ned.1", "bel.1", "tur.1",
          "sau.1", "uae.1", "alg.1", "usa.1", "mex.1", "arg.1", "fifa.friendly", "concacaf.nations",
          "fifa.worldq.uefa", "fra.coupe_de_france", "eng.fa", "eng.league_cup", "esp.copa_del_rey",
          "ita.coppa_italia", "ger.dfb_pokal", "fra.w.1", "eng.w.1"]
BASKET = ["nba"]
ALIASES = {"paris sg": "paris saint germain", "inter milan": "internazionale", "oud heuverlee": "oud heverlee leuven",
           "philadelphie": "philadelphia", "atletico paranaense": "athletico paranaense", "austria vienne": "austria vienna",
           "bayern munich": "bayern munich", "ol lyonnes": "ol lyonnes", "gremio": "gremio", "bahia salvador": "bahia",
           "la nouvelle orleans": "new orleans"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            b = gzip.decompress(b)
        return json.loads(b)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(f|fc|sc|rs|pr|ba|cf|ac|afc|club|de|the|w)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for k, v in ALIASES.items():
        if s.startswith(k):
            s = v + s[len(k):]
    return s


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a in b or b in a:
        return 1
    return SequenceMatcher(None, a, b).ratio()


def scoreboards(sport_path, leagues, dates):
    evs = []
    def one(args):
        lg, d = args
        try:
            return [(lg, e) for e in get(f"{API}/{sport_path}/{lg}/scoreboard?dates={d}").get("events", [])]
        except Exception:
            return []
    with ThreadPoolExecutor(16) as ex:
        for r in ex.map(one, [(lg, d) for lg in leagues for d in dates]):
            evs += r
    return evs


def team_names(e):
    comp = e["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
    return home["team"]["displayName"], away["team"]["displayName"]


def summarize(sport_path, lg, eid, home_name, away_name):
    d = get(f"{API}/{sport_path}/{lg}/summary?event={eid}")
    out = {"espnEvent": eid, "league": lg, "teams": {}, "h2h": None, "news": [], "injuries": {}, "leaders": {}, "lineups": {}}
    link = None
    for l in (d.get("header", {}).get("links") or []):
        if "desktop" in (l.get("rel") or []) and ("summary" in l.get("rel", []) or "gamecast" in l.get("rel", [])):
            link = l["href"]; break
    out["url"] = link or f"https://www.espn.com/{'soccer/match' if sport_path == 'soccer' else 'nba/game'}/_/gameId/{eid}"
    comp = d["header"]["competitions"][0]
    side = {c["team"]["id"]: c["homeAway"] for c in comp["competitors"]}
    names = {c["team"]["id"]: c["team"]["displayName"] for c in comp["competitors"]}
    for g in d.get("lastFiveGames") or []:
        tid = g["team"]["id"]; s = side.get(tid)
        if not s:
            continue
        res = []
        for ev in g.get("events", []):
            res.append({"r": ev.get("gameResult"), "score": ev.get("score"), "opp": (ev.get("opponent") or {}).get("displayName"),
                        "at": ev.get("atVs"), "date": (ev.get("gameDate") or "")[:10],
                        "hs": ev.get("homeTeamScore"), "as": ev.get("awayTeamScore")})
        out["teams"][s] = {"name": names[tid], "form": res, "logo": g["team"].get("logo")}
    # classement
    try:
        for grp in d["standings"]["groups"]:
            for en in grp["standings"]["entries"]:
                s = side.get(en.get("id"))
                if s and s in out["teams"]:
                    st = {x["name"]: x.get("displayValue") for x in en["stats"]}
                    out["teams"][s]["standing"] = {k: st.get(k) for k in ("rank", "points", "gamesPlayed", "overall", "winPercent", "wins", "losses", "streak") if st.get(k) is not None}
    except Exception:
        pass
    # confrontations directes
    for ss in d.get("seasonseries") or []:
        evs = []
        for ev in ss.get("events", [])[:5]:
            cs = ev.get("competitors", [])
            if len(cs) == 2 and ev.get("statusType", {}).get("completed"):
                h = next((c for c in cs if c["homeAway"] == "home"), cs[0]); a = next((c for c in cs if c["homeAway"] == "away"), cs[1])
                evs.append({"date": ev.get("date", "")[:10], "home": h["team"]["displayName"], "away": a["team"]["displayName"],
                            "score": f"{h.get('score')}-{a.get('score')}",
                            "winner": "home" if h.get("winner") else "away" if a.get("winner") else "draw",
                            "homeSide": side.get(h["team"]["id"])})
        if evs:
            out["h2h"] = {"summary": ss.get("summary"), "games": evs}
        break
    # meilleurs joueurs
    for t in d.get("leaders") or []:
        s = side.get((t.get("team") or {}).get("id"))
        if not s:
            continue
        for cat in t.get("leaders") or []:
            if cat.get("leaders"):
                ld = cat["leaders"][0]
                out["leaders"][s] = {"cat": cat.get("displayName"), "name": ld["athlete"]["displayName"], "value": ld.get("displayValue")}
                break
    # blessés (NBA surtout)
    for t in d.get("injuries") or []:
        s = side.get((t.get("team") or {}).get("id"))
        if s:
            out["injuries"][s] = [{"name": i["athlete"]["displayName"], "status": i.get("status"),
                                   "type": (i.get("details") or {}).get("type")} for i in t.get("injuries", [])][:8]
    # compositions (quand publiées)
    for r in d.get("rosters") or []:
        s = r.get("homeAway")
        starters = [p["athlete"]["displayName"] for p in r.get("roster") or [] if p.get("starter")]
        if s and starters:
            out["lineups"][s] = starters
    # prédicteur ESPN (NBA)
    pr = d.get("predictor")
    if pr and pr.get("homeTeam", {}).get("gameProjection"):
        try:
            out["predictor"] = {"home": float(pr["homeTeam"]["gameProjection"]) / 100}
        except ValueError:
            pass
    # actus qui citent une des équipes
    keys = [norm(n).split(" ")[0] for n in names.values()]
    for a in (d.get("news") or {}).get("articles", []):
        txt = norm((a.get("headline") or "") + " " + (a.get("description") or ""))
        if any(k and len(k) > 3 and k in txt.split() for k in keys):
            out["news"].append({"title": a.get("headline"), "url": ((a.get("links") or {}).get("web") or {}).get("href")})
    out["news"] = out["news"][:3]
    return out


def main():
    raw = json.load(open("raw_odds.json", encoding="utf-8"))
    now = datetime.now(timezone.utc)
    dates = sorted({(now + timedelta(days=i)).strftime("%Y%m%d") for i in range(0, 2)})
    pools = {"football": ("soccer", scoreboards("soccer", SOCCER, dates)),
             "basketball": ("basketball", scoreboards("basketball", BASKET, dates))}
    ctx, jobs = {}, []
    for m in raw["matches"]:
        if m["sport"] not in pools or " - " not in m["name"]:
            continue
        h, a = m["name"].split(" - ", 1)
        t = datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
        best, bs = None, 0
        sport_path, evs = pools[m["sport"]]
        for lg, e in evs:
            et = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if abs((et - t).total_seconds()) > 5400:
                continue
            eh, ea = team_names(e)
            s1, s2 = sim(h, eh), sim(a, ea)
            if min(s1, s2) >= 0.45 and s1 + s2 > bs:
                best, bs = (lg, e, eh, ea), s1 + s2
        if best:
            jobs.append((m["id"], sport_path, best))

    def run(job):
        mid, sp, (lg, e, eh, ea) = job
        try:
            return mid, summarize(sp, lg, e["id"], eh, ea)
        except Exception as ex:
            return mid, {"error": str(ex)}
    with ThreadPoolExecutor(12) as ex:
        for mid, c in ex.map(run, jobs):
            if "error" not in c:
                ctx[mid] = c
    json.dump(ctx, open("context.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"ESPN : contexte trouvé pour {len(ctx)} matchs sur {len(raw['matches'])}")


if __name__ == "__main__":
    main()
