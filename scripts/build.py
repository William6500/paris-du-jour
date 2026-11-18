#!/usr/bin/env python3
"""Étape 3 : fusionne cotes Betclic + contexte ESPN + notes web, calcule les probabilités et écrit data.json.

Modèle (volontairement modeste et transparent) :
  p_marché = 1/cote normalisée dans chaque marché (marge Betclic retirée).
  Football 1N2 : signal s ∈ [-1,1] = 0,5·écart de forme (5 derniers, V=3 N=1) + 0,3·écart de points/match
                 au classement + 0,2·bilan des confrontations directes. Victoire dom. +4·s points de %,
                 extérieur −4·s, + ajustement des notes (absences…) borné à ±3 pts ; total borné à ±6 pts.
  Buts : moyenne de buts des 5 derniers matchs des deux équipes vs 2,6 → ±3 pts max sur « + de 2,5 »
         (±2 sur 1,5 / 3,5). Les 2 marquent : fréquence récente → ±3 pts max.
  Basket : 25 % de l'écart entre le prédicteur ESPN et le marché, borné à ±5 pts.
  Sans données → probabilité du marché.  « Value » = p_modèle × cote > 1,02.
"""
import json, re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
TIERS = [("safe", 1.15, 1.60), ("bal", 1.60, 2.50), ("big", 2.50, 5.0), ("poker", 5.0, 15.0)]
PARTITION = {"1N2", "Vainqueur", "1re MT", "2e MT", "1re équipe à marquer", "Les 2 marquent", "2 marquent ou +2,5"}
clip = lambda x, a, b: max(a, min(b, x))


def load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def form_pts(form):
    rs = [f["r"] for f in form if f.get("r") in "WDL"]
    return (sum(3 if r == "W" else 1 if r == "D" else 0 for r in rs) / (3 * len(rs))) if rs else None


def betclic_form(codes):  # Betclic : 1=victoire 2=défaite 3=nul
    return [{"r": {1: "W", 2: "L", 3: "D"}.get(c)} for c in codes if c in (1, 2, 3)]


def ppg(st):
    try:
        if st.get("winPercent"):
            return float(st["winPercent"])
        return float(st["points"]) / float(st["gamesPlayed"]) / 3
    except Exception:
        return None


def goals_stats(form):
    tot, both, n = 0, 0, 0
    for f in form:
        try:
            h, a = int(f["hs"]), int(f["as"])
        except Exception:
            continue
        tot += h + a; both += (h > 0 and a > 0); n += 1
    return (tot / n, both / n) if n else (None, None)


def market_probs(mk):
    inv = [1 / s["odds"] for s in mk["selections"]]
    lab = mk["label"]
    if lab in ("Total buts", "Buts équipe"):
        out = [None] * len(inv)
        lines = {}
        for i, s in enumerate(mk["selections"]):
            m = re.search(r"([+-]) de ([\d,]+)", s["name"])
            if m:
                lines.setdefault(m.group(2), {})[m.group(1)] = i
        for ln, d in lines.items():
            if "+" in d and "-" in d:
                t = inv[d["+"]] + inv[d["-"]]
                out[d["+"]] = inv[d["+"]] / t; out[d["-"]] = inv[d["-"]] / t
        return out
    if lab == "Double chance":
        t = sum(inv)
        return [2 * x / t for x in inv]
    t = sum(inv)
    return [x / t for x in inv]


def main():
    raw = load("raw_odds.json", None)
    ctx = load("context.json", {})
    notes = load("notes.json", {})
    out_matches, picks, pool = [], [], []
    for m in raw["matches"]:
        home, away = (m["name"].split(" - ", 1) + [""])[:2]
        c = ctx.get(m["id"], {})
        teams = c.get("teams", {})
        note = notes.get(m["name"])
        if note and note.get("date") and abs((datetime.fromisoformat(note["date"]) - datetime.fromisoformat(m["start"][:10])).days) > 1:
            note = None
        fH = teams.get("home", {}).get("form") or betclic_form((m["contestants"][0]["form"] if m["contestants"] else []))
        fA = teams.get("away", {}).get("form") or betclic_form((m["contestants"][1]["form"] if len(m["contestants"]) > 1 else []))
        fH = sorted(fH, key=lambda f: f.get("date", "")) if fH and fH[0].get("date") else fH
        fA = sorted(fA, key=lambda f: f.get("date", "")) if fA and fA[0].get("date") else fA
        # --- signal contexte
        comps, why = [], []
        a, b = form_pts(fH), form_pts(fA)
        if a is not None and b is not None:
            comps.append((0.5, a - b))
        sh, sa = teams.get("home", {}).get("standing") or {}, teams.get("away", {}).get("standing") or {}
        if ppg(sh) is not None and ppg(sa) is not None:
            comps.append((0.3, clip((ppg(sh) - ppg(sa)) * 2, -1, 1)))
        h2h = c.get("h2h")
        if h2h and h2h.get("games"):
            w = sum(1 for g in h2h["games"] if (g["winner"] == "home" and g["homeSide"] == "home") or (g["winner"] == "away" and g["homeSide"] == "away"))
            l = sum(1 for g in h2h["games"] if (g["winner"] == "home" and g["homeSide"] == "away") or (g["winner"] == "away" and g["homeSide"] == "home"))
            comps.append((0.2, (w - l) / len(h2h["games"])))
            h2h["record"] = f"{w}V {len(h2h['games']) - w - l}N {l}D"
        s = sum(wt * v for wt, v in comps) / sum(wt for wt, _ in comps) if comps else None
        adj = clip(float(note.get("adj", 0)), -3, 3) / 100 if note else 0
        has_ctx = s is not None or bool(note)
        # --- marchés
        markets = []
        for mk in m["markets"]:
            pm = market_probs(mk)
            markets.append({"label": mk["label"], "name": mk["name"], "sel": [{"name": x["name"], "odds": x["odds"], "pm": p, "pk": p} for x, p in zip(mk["selections"], pm)]})
        main_mk = markets[0] if markets else None
        model_main = None
        if main_mk and m["sport"] == "football" and main_mk["label"] == "1N2" and len(main_mk["sel"]) == 3:
            pH, pD, pA = (x["pm"] for x in main_mk["sel"])
            shift = clip((0.04 * s if s is not None else 0) + adj, -0.06, 0.06)
            pH, pA = max(0.01, pH + shift), max(0.01, pA - shift)
            t = pH + pD + pA
            model_main = (pH / t, pD / t, pA / t)
            for x, p in zip(main_mk["sel"], model_main):
                x["pk"] = p
            gH, bH = goals_stats(fH); gA, bA = goals_stats(fA)
            for mk in markets:
                if mk["label"] == "Double chance":
                    for x in mk["sel"]:
                        n = x["name"]
                        hasH, hasA, hasN = home in n, away in n, "Nul" in n
                        x["pk"] = (model_main[0] if hasH else 0) + (model_main[1] if hasN else 0) + (model_main[2] if hasA else 0)
                if mk["label"] == "Total buts" and gH is not None and gA is not None:
                    g = clip(((gH + gA) / 2 - 2.6) / 1.5, -1, 1)
                    for x in mk["sel"]:
                        mm = re.search(r"([+-]) de ([\d,]+)", x["name"])
                        if mm and x["pm"] is not None:
                            k = 0.03 if mm.group(2) == "2,5" else 0.02 if mm.group(2) in ("1,5", "3,5") else 0
                            x["pk"] = clip(x["pm"] + (k * g if mm.group(1) == "+" else -k * g), 0.01, 0.99)
                if mk["label"] == "Les 2 marquent" and bH is not None and bA is not None:
                    r = clip(((bH + bA) / 2 - 0.5) * 2, -1, 1)
                    for x in mk["sel"]:
                        x["pk"] = clip(x["pm"] + (0.03 * r if x["name"] == "Oui" else -0.03 * r), 0.01, 0.99)
        elif main_mk and m["sport"] == "basketball" and c.get("predictor") and len(main_mk["sel"]) == 2:
            pH = main_mk["sel"][0]["pm"]
            d = clip(0.25 * (c["predictor"]["home"] - pH), -0.05, 0.05)
            main_mk["sel"][0]["pk"], main_mk["sel"][1]["pk"] = pH + d, 1 - pH - d
        # --- raison courte
        if note and note.get("short"):
            reason = note["short"]
        else:
            bits = []
            if fH and fA:
                cnt = lambda f: f"{sum(x['r']=='W' for x in f)}V {sum(x['r']=='D' for x in f)}N {sum(x['r']=='L' for x in f)}D"
                bits.append(f"Forme : {home} {cnt(fH)}, {away} {cnt(fA)}")
            if sh.get("rank") and sa.get("rank"):
                bits.append(f"{sh['rank']}e contre {sa['rank']}e")
            if c.get("predictor"):
                bits.append(f"prédicteur ESPN {round(c['predictor']['home']*100)} % pour {home}")
            reason = " · ".join(bits) if bits else "Pas de contexte trouvé : probabilité du marché seule"
        sources = []
        if c.get("url"):
            sources.append({"label": "ESPN", "url": c["url"]})
        if note:
            sources += note.get("sources", [])
        if m.get("url"):
            sources.append({"label": "Betclic", "url": m["url"]})
        om = {
            "id": m["id"], "name": m["name"], "home": home, "away": away, "start": m["start"], "sport": m["sport"],
            "competition": m["competition"], "round": m.get("round"),
            "form": {"home": [x["r"] for x in fH][-5:], "away": [x["r"] for x in fA][-5:]},
            "lastGames": {"home": [{k: x.get(k) for k in ("r", "score", "opp", "at", "date")} for x in fH][-5:] if fH and fH[0].get("opp") else [],
                          "away": [{k: x.get(k) for k in ("r", "score", "opp", "at", "date")} for x in fA][-5:] if fA and fA[0].get("opp") else []},
            "standing": {"home": sh, "away": sa},
            "h2h": {"summary": h2h.get("summary"), "record": h2h.get("record"), "games": h2h["games"][:5]} if h2h and h2h.get("games") else None,
            "leaders": c.get("leaders", {}), "injuries": c.get("injuries", {}),
            "absents": (note or {}).get("absents", {}), "lineups": (note or {}).get("lineups") or {k: ", ".join(v) for k, v in c.get("lineups", {}).items()},
            "mood": (note or {}).get("mood"), "news": c.get("news", []),
            "predictor": c.get("predictor"), "signal": round(s, 3) if s is not None else None, "adj": adj,
            "hasContext": has_ctx, "reason": reason, "sources": sources, "url": m.get("url"),
            "main": [{"name": x["name"], "odds": x["odds"], "pm": round(x["pm"], 4), "pk": round(x["pk"], 4)} for x in main_mk["sel"]] if main_mk else [],
        }
        out_matches.append(om)
        # --- candidats
        cands = []
        for mk in markets:
            for x in mk["sel"]:
                if x["pk"] is None or not (1.15 <= x["odds"] <= 15):
                    continue
                lab = mk["label"] if mk["label"] != "Buts équipe" else "Buts " + mk["name"].split(" - ")[-1]
                cands.append({"matchId": m["id"], "market": lab, "sel": x["name"], "odds": x["odds"],
                              "pm": round(x["pm"], 4), "pk": round(x["pk"], 4), "ev": round(x["pk"] * x["odds"], 4),
                              "value": x["pk"] * x["odds"] > 1.02 and has_ctx, "start": m["start"]})
        pool += cands
        for key, lo, hi in TIERS:
            inn = [x for x in cands if lo <= x["odds"] < hi]
            if inn:
                best = max(inn, key=lambda x: (x["ev"] + 0.15 * x["pk"]))
                picks.append(dict(best, tier=key))
    # --- combinés multi-matchs (vraies cotes Betclic : produit des cotes, matchs différents)
    combos = []
    def multi(title, n, lo, hi, pct, key):
        by_match = {}
        for x in pool:
            if lo <= x["odds"] <= hi and x["market"] not in ("2 marquent ou +2,5",) and not x["market"].startswith("Buts "):
                cur = by_match.get(x["matchId"])
                if not cur or key(x) > key(cur):
                    by_match[x["matchId"]] = x
        legs, per_mk = [], {}
        for x in sorted(by_match.values(), key=key, reverse=True):
            if per_mk.get(x["market"], 0) >= 2:
                continue  # varier les types de paris
            per_mk[x["market"]] = per_mk.get(x["market"], 0) + 1
            legs.append(x)
            if len(legs) == n:
                break
        if len(legs) == n:
            o = p = 1
            for l in legs:
                o *= l["odds"]; p *= l["pk"]
            combos.append({"kind": "multi", "title": title, "legs": legs, "odds": round(o, 2), "p": round(p, 4), "real": True, "pct": pct})
    multi("Duo sécurité", 2, 1.15, 1.40, 2.0, lambda x: x["pk"])
    multi("Trio confiance", 3, 1.20, 1.55, 1.5, lambda x: x["pk"] + 0.3 * x["ev"])
    multi("Quatuor", 4, 1.30, 1.85, 1.0, lambda x: x["ev"] + 0.3 * x["pk"])
    multi("Gros ticket 6 matchs", 6, 1.35, 2.30, 0.5, lambda x: x["ev"] + 0.2 * x["pk"])
    # --- même match : MyCombi Betclic (cote réelle) + combos maison (cote estimée)
    mc = []
    for m in raw["matches"]:
        margin = sum(1 / s["odds"] for s in m["markets"][0]["selections"]) - 1 if m["markets"] else 0.06
        for cb in m.get("mycombis", []):
            if 1.30 <= cb["odds"] <= 6 and len(cb["legs"]) >= 2:
                mc.append({"kind": "mycombi", "title": "MyCombi Betclic", "matchId": m["id"], "start": m["start"],
                           "legs": [{"matchId": m["id"], "market": l["market"], "sel": l["name"], "odds": l["odds"]} for l in cb["legs"]],
                           "odds": cb["odds"], "p": round(1 / cb["odds"] / (1 + margin), 4), "real": True, "pct": 1.0})
    seen, mc_sel = {}, []
    for x in sorted(mc, key=lambda x: -x["p"] * x["odds"] ** 0.5):
        if seen.get(x["matchId"], 0) < 2:
            seen[x["matchId"]] = seen.get(x["matchId"], 0) + 1
            mc_sel.append(x)
    combos += mc_sel[:12]
    # maison : vainqueur/double chance du modèle + meilleure ligne de buts, seulement si Betclic ne propose pas déjà ce duo
    est = []
    for om in out_matches:
        if om["sport"] != "football":
            continue
        cands = [x for x in pool if x["matchId"] == om["id"]]
        res = [x for x in cands if x["market"] in ("1N2", "Double chance") and x["pk"] >= 0.55]
        gl = [x for x in cands if x["market"] in ("Total buts", "Les 2 marquent") and x["pk"] >= 0.55]
        if not res or not gl:
            continue
        r = max(res, key=lambda x: x["ev"]); g = max(gl, key=lambda x: x["ev"])
        already = any(set((l["sel"]) for l in c["legs"]) == {r["sel"], g["sel"]} for c in mc if c["matchId"] == om["id"])
        if already:
            continue
        est.append({"kind": "estimate", "title": "Combo maison", "matchId": om["id"], "start": om["start"],
                    "legs": [r, g], "odds": round(r["odds"] * g["odds"], 2), "p": round(r["pk"] * g["pk"], 4), "real": False, "pct": 1.0})
    combos += sorted(est, key=lambda x: -x["p"])[:6]
    now = datetime.fromisoformat(raw["fetchedAt"].replace("Z", "+00:00"))
    out = {"source": "Betclic.fr (cotes avant-match) + ESPN (forme, classement, H2H, blessés NBA) + presse (absences, compos)",
           "oddsSource": raw.get("source", "Betclic.fr"),
           "fetchedAt": raw["fetchedAt"], "fetchedAtParis": now.astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
           "builtAtParis": datetime.now(timezone.utc).astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
           "matches": out_matches, "picks": picks, "combos": combos}
    json.dump(out, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print("Build :", len(out_matches), "matchs,", dict(Counter(p["tier"] for p in picks)), "|", dict(Counter(c["kind"] for c in combos)),
          "| value :", sum(p["value"] for p in picks), "| avec contexte :", sum(m["hasContext"] for m in out_matches))


if __name__ == "__main__":
    main()
