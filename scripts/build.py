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
  Autres sports (et basket sans prédicteur) : forme récente → ±3 pts max.
  Actus automatiques (news.json, Google Actualités 7 j) : mots-clés blessure/suspension, crise d'entraîneur,
         dynamique → ±2 pts max, inclus dans les bornes ci-dessus.
  Sans données → probabilité du marché.  « Value » = p_modèle × cote > 1,02.
"""
import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markets_model as MM
from collections import Counter
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


LINE_MK = ("Total buts", "Buts équipe", "Total points", "Points équipe", "1er QT total")
NONPART = ("Écart de buts", "Écart de points", "Écart de sets")


PART_LABELS = {"1N2", "Vainqueur", "1re MT", "2e MT", "1re équipe à marquer", "Dernière équipe à marquer", "1er QT", "1er set",
               "Score en sets", "Gagne 1 set", "Les 2 marquent", "2 marquent ou +2,5"}
LINE_RX = re.compile(r"^(.*?)([+-]) de ([\d,]+)$")


ECART_RX = re.compile(r"^(.+?) (gagne de (\d+)(?: buts| points| sets)? ou \+|ne perd pas ou perd de (\d+)(?: buts| points| sets)? (?:ou -|exactement)|ne perd pas)$")


def ecart_pairs(sels, home, away):
    """Écarts : « A gagne de N ou + » et « B ne perd pas ou perd de N-1 ou - » sont complémentaires → paires normalisées."""
    keyed = {}
    for i, s in enumerate(sels):
        m = ECART_RX.match(s["name"])
        if not m:
            continue
        side = 1 if m.group(1) == home else -1 if m.group(1) == away else 0
        if not side:
            continue
        if m.group(3):  # équipe gagne de N ou + : écart(équipe) >= N
            k = (side * 1, int(m.group(3)))
            keyed.setdefault((side, int(m.group(3))), {})["ge"] = i
        else:  # ne perd pas ou perd de N ou - : écart(équipe) >= -N  <=> écart(autre) <= N  <=> complément de « autre gagne de N+1 ou + »
            n = int(m.group(4)) if m.group(4) else 0
            keyed.setdefault((-side, n + 1), {})["lt"] = i
    return [(d["ge"], d["lt"]) for d in keyed.values() if "ge" in d and "lt" in d]


def market_probs(mk, margin=0.06, home=None, away=None):
    """Proba sans marge de chaque sélection.
    • paires « + de X » / « - de X » (totaux, joueurs) : normalisées deux à deux ;
    • marchés qui couvrent toutes les issues (1N2, vainqueur, Oui/Non, mi-temps, sets…) : normalisés ;
    • double chance : somme = 2 ;
    • le reste (écarts, score exact, buteurs, joueurs, spéciaux) : marge du marché principal retirée (≈),
      puis les buteurs sont recalés dans markets_model sur les buts attendus."""
    sels = mk["selections"]
    inv = [1 / s["odds"] for s in sels]
    lab = mk["label"]
    pairs, out = {}, [None] * len(inv)
    for i, s in enumerate(sels):
        m = LINE_RX.match(s["name"])
        if m:
            pairs.setdefault((m.group(1).strip(), m.group(3)), {})[m.group(2)] = i
    if pairs and len(pairs) * 2 >= len(sels) - 1:
        for d in pairs.values():
            if "+" in d and "-" in d:
                t = inv[d["+"]] + inv[d["-"]]
                out[d["+"]] = inv[d["+"]] / t; out[d["-"]] = inv[d["-"]] / t
        return out
    names = {s["name"] for s in sels}
    if lab.startswith("Écart") and home:
        prs = ecart_pairs(sels, home, away)
        for a, b in prs:
            t = inv[a] + inv[b]; out[a] = inv[a] / t; out[b] = inv[b] / t
        if prs:
            return out  # sélections sans complément (« exactement ») : pas de proba fiable → ignorées
    if lab == "Double chance":
        t = sum(inv); return [2 * x / t for x in inv]
    if lab in PART_LABELS or names == {"Oui", "Non"} or (re.search(r"Vainqueur|Résultat", mk["name"]) and len(sels) in (2, 3)):
        t = sum(inv); return [x / t for x in inv]
    if len(sels) == 1:
        return [None]  # pari spécial (cote boostée / combinaison) : pas de proba propre
    m = margin * (2.5 if re.search(r"Score exact|Buteur|buteur|Joueur|joueur|décisif", mk["name"]) else 1)
    return [min(0.97, x / (1 + m)) for x in inv]


SHORT = [(r"^Joueur décisif \(buteur ou passeur décisif dans le tps rég\.\) · 1 fois ou \+$", "Décisif (but ou passe)"),
         (r"^Joueur décisif (\d) fois ou \+.*", r"Décisif \1 fois ou +"),
         (r"^Joueur ou son remplaçant décisif.*", "Décisif ou son remplaçant"),
         (r"^Joueur \+ son remplaçant décisif (\d) fois.*", r"Décisif + remplaçant \1 fois ou +"),
         (r"^Joueur \+ son remplaçant buteur (\d) fois.*", r"Buteur + remplaçant \1 buts ou +"),
         (r"^Double chance décisif.*", "Double chance décisif"),
         (r"^Nombre de points du joueur \(\+/-\)", "Points joueur"),
         (r"^Performance \(points \+ rebonds \+ passes\) du joueur.*", "Points+rebonds+passes"),
         (r"^(\d)è QT - Nombre total de points", r"\1e QT total"), (r"^(\d)è QT - Vainqueur", r"\1e QT"),
         (r"^2ème set - Vainqueur", "2e set"), (r"^Nombre total de jeux", "Total jeux")]


# Paris « soit… soit… » (double chance, handicaps « ne perd pas ou perd de N », « ou son remplaçant », buteur double chance,
# joueur décisif but OU passe, « 2 marquent ou +2,5 »…) : jamais recommandés, seulement visibles dans la liste complète.
COMPOSITE = re.compile(r"\bou\b|double chance|décisif|remplaçant|d'avance|ne perd pas", re.I)


def is_single(mk_name, label, sel):
    return not (COMPOSITE.search(mk_name) or COMPOSITE.search(label) or COMPOSITE.search(sel))


def short_label(lab):
    for rx, rp in SHORT:
        if re.search(rx, lab):
            return re.sub(rx, rp, lab)
    return lab


def main():
    raw = load("raw_odds.json", None)
    ctx = load("context.json", {})
    notes = load("notes.json", {})
    news = load("news.json", {})
    out_matches, picks, pool = [], [], []
    global MSTAT, ALLSTAT, EVS, DKS, MDIR
    MSTAT, ALLSTAT, EVS, DKS = Counter(), Counter(), {}, {}
    MDIR = "m"
    os.makedirs(MDIR, exist_ok=True)
    for f in os.listdir(MDIR):
        if f.endswith(".json"):
            os.remove(os.path.join(MDIR, f))
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
        nw = news.get(m["id"]) or {}
        nadj = clip(float(nw.get("adj", 0)), -2, 2) / 100
        adj = clip(adj + nadj, -0.04, 0.04)
        has_ctx = s is not None or bool(note) or bool(nadj)
        # --- marchés
        markets = []
        margin = (sum(1 / x["odds"] for x in m["markets"][0]["selections"]) - 1) if m["markets"] else 0.06
        for mk in m["markets"]:
            pm = market_probs(mk, max(0.02, margin), home, away)
            markets.append({"label": short_label(mk["label"]), "name": mk["name"],
                            "sel": [dict({"name": x["name"], "odds": x["odds"], "pm": p, "pk": p}, **({"team": x["team"]} if x.get("team") else {})) for x, p in zip(mk["selections"], pm)]})
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
        elif main_mk and m["sport"] == "basketball" and c.get("predictor") and len(main_mk["sel"]) == 2:
            pH = main_mk["sel"][0]["pm"]
            d = clip(0.25 * (c["predictor"]["home"] - pH) + nadj, -0.05, 0.05)
            main_mk["sel"][0]["pk"], main_mk["sel"][1]["pk"] = pH + d, 1 - pH - d
        elif main_mk and len(main_mk["sel"]) in (2, 3) and all(x["pm"] for x in main_mk["sel"]):
            d = clip((0.03 * s if s is not None else 0) + adj, -0.04, 0.04)
            sel = main_mk["sel"]
            if d:
                sel[0]["pk"] = clip(sel[0]["pm"] + d, 0.01, 0.99); sel[-1]["pk"] = clip(sel[-1]["pm"] - d, 0.01, 0.99)
                t = sum(x["pk"] for x in sel)
                for x in sel:
                    x["pk"] /= t
        # --- tous les autres marchés : modèle par marché (Poisson foot, normale basket/rugby, sets tennis)
        mm = {"analysed": 0}
        if main_mk and main_mk["sel"] and all(x["pm"] is not None for x in main_mk["sel"]):
            mm = MM.apply(m["sport"], markets, home, away, [x["pm"] for x in main_mk["sel"]], [x["pk"] for x in main_mk["sel"]],
                          fH, fA, (h2h or {}).get("games"))
        for k, v in (mm.get("by") or {}).items():
            MSTAT[k] += v
        MSTAT["_main"] += 1 if main_mk else 0
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
            if mm.get("goals"):
                g = mm["goals"]; bits.append(f"buts attendus {str(g['model']).replace('.', ',')} (Betclic {str(g['market']).replace('.', ',')}, 5 derniers {str(g['data']).replace('.', ',')})")
            if mm.get("points"):
                g = mm["points"]; bits.append(f"points attendus {g['model']:.0f} (Betclic {g['market']:.0f}, 5 derniers {g['data']:.0f})")
            reason = " · ".join(bits) if bits else "Pas de contexte trouvé : probabilité du marché seule"
        sources = []
        if c.get("url"):
            sources.append({"label": "ESPN", "url": c["url"]})
        if note:
            sources += note.get("sources", [])
        if m.get("url"):
            sources.append({"label": "Betclic", "url": m["url"]})
        auto_news = [dict(it, side="match") for it in nw.get("match", [])[:3]]
        for side in ("home", "away"):
            auto_news += [dict(it, side=side) for it in nw.get(side, [])[:2]]
        auto_news = [{k: it.get(k) for k in ("title", "url", "source", "date", "side")} for it in auto_news[:5]]
        form_src = "ESPN" if teams.get("home", {}).get("form") or teams.get("away", {}).get("form") else ("Betclic" if fH or fA else None)
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
            "espn": {"event": c.get("espnEvent"), "league": c.get("league"), "sport": "soccer" if m["sport"] == "football" else "basketball"} if c.get("espnEvent") else None,
            "model": {k: mm[k] for k in ("goals", "points") if k in mm}, "analysedMarkets": mm.get("analysed", 0), "nMarkets": len(markets),
            "started": bool(m.get("started")), "oddsAt": m.get("oddsAt"),
            "auto": {"news": auto_news, "signals": nw.get("signals", []), "adj": round(nadj * 100, 1)},
            "data": {"form": form_src, "h2h": bool(h2h and h2h.get("games")), "news": bool(auto_news), "espn": bool(c)},
            "hasContext": has_ctx, "reason": reason, "sources": sources, "url": m.get("url"),
            "main": [{"name": x["name"], "odds": x["odds"], "pm": round(x["pm"], 4), "pk": round(x["pk"], 4)} for x in main_mk["sel"]] if main_mk else [],
        }
        out_matches.append(om)
        # --- détail complet des marchés : fichier séparé m/<id>.json (chargé à l'ouverture de la carte)
        if main_mk:
            for x in main_mk["sel"]:
                x["adj"] = x["pk"] != x["pm"] or has_ctx
        n_adj = sum(1 for mk in markets if any(x.get("adj") for x in mk["sel"]))
        for mk in markets:
            ALLSTAT[mk["label"] if not mk["label"].startswith(("Buts ", "Points ")) else mk["label"]] += 1
        # format compact : {"m":[[libellé, nom, [[sélection, cote, p_betclic, p_analyse, ajusté(0/1), équipe], …]], …]}
        # (marchés joueurs très longs : les 40 sélections les plus probables)
        r4 = lambda v: round(v, 3) if v is not None else None
        detail = {"id": m["id"], "m": [[mk["label"], mk["name"], [
            [x["name"], x["odds"], r4(x["pm"]), r4(x["pk"]), 1 if x.get("adj") else 0] + ([x["team"]] if x.get("team") else [])
            for x in (sorted(mk["sel"], key=lambda y: y["odds"])[:40] if len(mk["sel"]) > 40 else mk["sel"])]] for mk in markets]}
        json.dump(detail, open(os.path.join(MDIR, f"{m['id']}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        om["nMarkets"] = len(markets); om["analysedMarkets"] = n_adj
        om["nSel"] = sum(len(mk["sel"]) for mk in markets)
        dk = mm.pop("_dk", None); kind = mm.pop("_kind", None)
        # --- candidats
        cands = []
        for mk in markets:
            for x in mk["sel"]:
                if x["pk"] is None or x["pm"] is None or not (1.15 <= x["odds"] <= 15):
                    continue
                lab = mk["label"]
                if lab == "Buts équipe":
                    lab = "Buts " + mk["name"].split(" - ")[-1]
                elif lab == "Points équipe":
                    lab = "Points " + (mk["name"].split(" - ")[-1] if mk["name"].startswith("Nombre") else mk["name"].split(" - ")[0])
                elif lab == "Gagne 1 set":
                    lab = mk["name"]
                c = {"matchId": m["id"], "market": lab, "sel": x["name"], "odds": x["odds"],
                     "pm": round(x["pm"], 4), "pk": round(x["pk"], 4), "ev": round(x["pk"] * x["odds"], 4),
                     "value": x["pk"] * x["odds"] > 1.02 and has_ctx and bool(x.get("adj")), "adj": bool(x.get("adj")), "start": m["start"]}
                if not is_single(mk["name"], lab, x["name"]):
                    continue  # pari composite « ou » : exclu des recommandations et des combinés
                cands.append(c)
                if dk is not None:
                    EVS[(m["id"], lab, x["name"])] = (mk["name"], mk["label"], x["name"])
        pool += cands
        if dk is not None:
            DKS[m["id"]] = (dk, kind, home, away, m["sport"])
        for key, lo, hi in TIERS:
            inn = [x for x in cands if lo <= x["odds"] < hi and x["adj"]] or [x for x in cands if lo <= x["odds"] < hi]
            if inn:
                best = max(inn, key=lambda x: (x["ev"] + 0.15 * x["pk"] + (0.02 if x["adj"] else 0)))
                picks.append(dict(best, tier=key))
    # ===================== combinés =====================
    fetched = raw["fetchedAt"]
    upcoming = lambda x: x["start"] > fetched
    combos = []
    tier_of = lambda o: "safe" if o < 1.6 else "bal" if o < 2.5 else "big" if o < 5 else "poker"
    def add(c):
        c["tier"] = tier_of(c["odds"]); combos.append(c)
    # --- plusieurs matchs : cote = produit des vraies cotes Betclic (matchs différents)
    BAD = re.compile(r"^(Buts |Points |Écart|1er QT|\de QT|Dernière|Score|Double chance (buteur|décisif)|Décisif|Buteur \+|2 marquent ou)")
    def multi(title, n, lo, hi, pct, key, variants=1, only=None):
        used = set()
        for v in range(variants):
            by_match = {}
            for x in pool:
                if not upcoming(x) or x["matchId"] in used or not (lo <= x["odds"] <= hi):
                    continue
                if only:
                    if not only(x):
                        continue
                elif BAD.match(x["market"]) or not x["adj"]:
                    continue
                cur = by_match.get(x["matchId"])
                if not cur or key(x) > key(cur):
                    by_match[x["matchId"]] = x
            legs, per_mk = [], {}
            for x in sorted(by_match.values(), key=key, reverse=True):
                if per_mk.get(x["market"], 0) >= max(2, n // 2):
                    continue  # varier les types de paris
                per_mk[x["market"]] = per_mk.get(x["market"], 0) + 1
                legs.append(x)
                if len(legs) == n:
                    break
            if len(legs) < n:
                return
            used |= {l["matchId"] for l in legs}
            o = p = 1
            for l in legs:
                o *= l["odds"]; p *= l["pk"]
            add({"kind": "multi", "title": title + (f" #{v + 1}" if variants > 1 else ""), "legs": legs, "odds": round(o, 2), "p": round(p, 4), "real": True, "pct": pct})
    multi("Duo sécurité", 2, 1.15, 1.40, 2.0, lambda x: x["pk"], variants=3)
    multi("Trio confiance", 3, 1.18, 1.50, 1.5, lambda x: x["pk"] + 0.3 * x["ev"], variants=2)
    multi("Quatuor", 4, 1.25, 1.75, 1.0, lambda x: x["ev"] + 0.3 * x["pk"], variants=2)
    multi("Duo value", 2, 1.50, 2.40, 1.0, lambda x: x["ev"] + 0.1 * x["pk"], variants=2)
    multi("Ticket 5 matchs", 5, 1.30, 2.00, 0.5, lambda x: x["ev"] + 0.2 * x["pk"], variants=2)
    multi("Gros ticket 6 matchs", 6, 1.35, 2.30, 0.5, lambda x: x["ev"] + 0.2 * x["pk"])
    multi("Ticket 8 matchs", 8, 1.30, 2.20, 0.25, lambda x: x["ev"] + 0.2 * x["pk"])
    multi("Trio buteurs", 3, 1.60, 4.50, 0.25, lambda x: x["ev"] + 0.3 * x["pk"], variants=2, only=lambda x: x["market"] == "Buteur" and x["adj"])
    multi("Ticket 10 matchs", 10, 1.25, 2.40, 0.25, lambda x: x["ev"] + 0.2 * x["pk"])
    # --- même match : MyCombi Betclic (vraie cote Betclic)
    mc = []
    for m in raw["matches"]:
        if m["start"] <= fetched:
            continue
        margin = sum(1 / s["odds"] for s in m["markets"][0]["selections"]) - 1 if m["markets"] else 0.06
        for cb in m.get("mycombis", []):
            if 1.20 <= cb["odds"] <= 40 and len(cb["legs"]) >= 2 and all(is_single(l["market"], "", l["name"]) for l in cb["legs"]):
                mc.append({"kind": "mycombi", "title": "MyCombi Betclic", "matchId": m["id"], "start": m["start"],
                           "legs": [{"matchId": m["id"], "market": l["market"], "sel": l["name"], "odds": l["odds"]} for l in cb["legs"]],
                           "odds": cb["odds"], "p": round(1 / cb["odds"] / (1 + 2 * max(0.03, margin)), 4), "real": True, "pct": 1.0})
    def pick_tiers(lst, per_tier, per_match):
        seen, cnt = Counter(), Counter()
        for x in sorted(lst, key=lambda x: -x["p"] * x["odds"] ** 0.5):
            t = tier_of(x["odds"])
            if seen[x["matchId"]] < per_match and cnt[t] < per_tier:
                seen[x["matchId"]] += 1; cnt[t] += 1; add(x)
    pick_tiers(mc, 10, 2)
    # --- même match : combos maison (proba jointe du modèle, cote estimée = produit, la vraie cote MyCombi diffère)
    est = []
    byM = {}
    for x in pool:
        if x["matchId"] in DKS and upcoming(x):
            byM.setdefault(x["matchId"], []).append(x)
    def jp(mid, legs):
        dk, kind, home, away, sport = DKS[mid]
        evs = []
        for l in legs:
            k = EVS.get((mid, l["market"], l["sel"]))
            ev = MM.event(k[0], k[1], k[2], home, away, kind) if k else None
            if not ev:
                return None
            evs.append(ev)
        return MM.joint(dk, evs)
    def mk_est(mid, title, legs, p=None):
        p = jp(mid, legs) if p is None else p
        if p is None or p < 0.05:
            return
        o = 1
        for l in legs:
            o *= l["odds"]
        # cote estimée : produit des cotes Betclic corrigé de la corrélation (∏ p_i / p_jointe), comme le fait un bookmaker
        pi = 1
        for l in legs:
            pi *= l["pk"]
        o = o * min(1.0, pi / p)
        if 1.3 <= o <= 30:
            est.append({"kind": "estimate", "title": title, "matchId": mid, "start": legs[0]["start"], "legs": legs,
                        "odds": round(o, 2), "p": round(p, 4), "real": False, "pct": 1.0})
    for mid, cs in byM.items():
        sport = DKS[mid][4]
        best = lambda f, minp=0.0: max([x for x in cs if f(x) and x["pk"] >= minp], key=lambda x: x["ev"] + 0.2 * x["pk"], default=None)
        res = best(lambda x: x["market"] in ("1N2", "Vainqueur", "Double chance"), 0.45)
        if sport == "football":
            tot = best(lambda x: x["market"] == "Total buts", 0.45)
            if res and tot:
                mk_est(mid, "Résultat + buts", [res, tot])
            ov = next((x for x in cs if x["market"] == "Total buts" and x["sel"] == "+ de 2,5"), None)
            bt = next((x for x in cs if x["market"] == "Les 2 marquent" and x["sel"] == "Oui"), None)
            if ov and bt:
                mk_est(mid, "+2,5 buts & les 2 marquent", [ov, bt])
            ht = best(lambda x: x["market"] == "1re MT", 0.35)
            o15 = next((x for x in cs if x["market"] == "Total buts" and x["sel"] == "+ de 1,5"), None)
            if ht and o15:
                mk_est(mid, "Mi-temps + buts", [ht, o15])
            sc = best(lambda x: x["market"] == "Buteur" and x["adj"], 0.3)
            win = next((x for x in cs if x["market"] == "1N2" and x["pk"] >= 0.5), None)
            if sc and win:  # buteur de l'équipe favorite + victoire : corrélés, approximation transparente
                mk_est(mid, "Buteur + victoire", [win, sc], p=min(win["pk"], sc["pk"]) * min(1.0, max(win["pk"], sc["pk"]) * 1.15))
        else:
            tot = best(lambda x: x["market"] in ("Total points",), 0.45)
            if res and tot:
                mk_est(mid, "Vainqueur + total", [res, tot])
    pick_tiers(est, 8, 2)
    now = datetime.fromisoformat(raw["fetchedAt"].replace("Z", "+00:00"))
    iso = datetime.now(PARIS).isocalendar()
    out = {"source": "Betclic.fr (cotes avant-match) + ESPN (forme, classement, H2H, blessés NBA) + Google Actualités (titres 7 j, analyse auto) + notes presse",
           "week": iso[1],
           "oddsSource": raw.get("source", "Betclic.fr"),
           "fetchedAt": raw["fetchedAt"], "fetchedAtParis": now.astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
           "builtAtParis": datetime.now(timezone.utc).astimezone(PARIS).strftime("%d/%m/%Y %H:%M"),
           "matches": out_matches, "picks": picks, "combos": combos}
    json.dump(out, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("Marchés relevés :", sum(ALLSTAT.values()), dict(ALLSTAT.most_common()))
    print("Marchés analysés :", MSTAT["_main"], "principaux +", {k: v for k, v in MSTAT.items() if k != "_main"})
    print("Build :", len(out_matches), "matchs,", dict(Counter(p["tier"] for p in picks)), "|", dict(Counter(c["kind"] for c in combos)),
          "| value :", sum(p["value"] for p in picks), "| avec contexte :", sum(m["hasContext"] for m in out_matches))


if __name__ == "__main__":
    main()
