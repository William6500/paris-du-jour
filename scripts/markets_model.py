#!/usr/bin/env python3
"""Modèle par marché (automatique, borné) — utilisé par build.py.

Principe : pour chaque match on construit DEUX distributions de résultats du même type :
  • « marché » : calée sur les prix Betclic sans marge (1N2/vainqueur + ligne de total principale) ;
  • « modèle » : la même, déplacée par les données (forme/H2H/classement/actus via la proba principale du modèle,
    moyennes de buts/points marqués-encaissés des 5 derniers matchs ESPN, mélangées au marché).
Pour chaque sélection Betclic on calcule P_modèle − P_marché sur ces distributions et on l'ajoute à la proba
sans marge de la sélection, borné à ±CAP. Les cotes restent les vraies cotes Betclic ; rien n'est inventé.
  Football : Poisson (λ dom., λ ext.), mi-temps = 45 % / 55 % des buts.
  Basket/rugby : loi normale (total, écart). Tennis : proba par set → scores en sets.
"""
import math, re
from statistics import NormalDist

try:
    import numpy as np
except Exception:  # pas de numpy → pas d'ajustement par marché
    np = None

N01 = NormalDist()
CAP = 0.04
clip = lambda x, a, b: max(a, min(b, x))


def nrm(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u2019", "'")).strip().lower()


# ---------- distributions (tableaux h, a, w) ----------
def poisson_dist(lh, la, n=11):
    k = np.arange(n)
    ph = np.exp(-lh) * lh ** k / np.array([math.factorial(i) for i in k])
    pa = np.exp(-la) * la ** k / np.array([math.factorial(i) for i in k])
    h, a = np.meshgrid(k, k, indexing="ij")
    w = np.outer(ph, pa)
    return {"h": h.ravel(), "a": a.ravel(), "w": w.ravel() / w.sum(), "lh": lh, "la": la}


def normal_dist(mt, md, st, sd):
    """Total T et écart D ~ lois normales indépendantes, discrétisées sur les entiers (scores réels entiers)."""
    t = np.arange(math.floor(mt - 4.5 * st), math.ceil(mt + 4.5 * st) + 1)
    d = np.arange(math.floor(md - 4.5 * sd), math.ceil(md + 4.5 * sd) + 1)
    wt = np.exp(-((t - mt) / st) ** 2 / 2); wd = np.exp(-((d - md) / sd) ** 2 / 2)
    T, Dd = np.meshgrid(t, d, indexing="ij")
    w = np.outer(wt, wd).ravel()
    return {"h": ((T + Dd) / 2).ravel(), "a": ((T - Dd) / 2).ravel(), "w": w / w.sum(), "cont": True}


def sets_dist(ps, best5=False):
    q = 1 - ps
    if best5:
        out = [(3, 0, ps ** 3), (3, 1, 3 * ps ** 3 * q), (3, 2, 6 * ps ** 3 * q * q), (2, 3, 6 * q ** 3 * ps * ps), (1, 3, 3 * q ** 3 * ps), (0, 3, q ** 3)]
    else:
        out = [(2, 0, ps * ps), (2, 1, 2 * ps * ps * q), (1, 2, 2 * q * q * ps), (0, 2, q * q)]
    return {"h": np.array([x[0] for x in out], float), "a": np.array([x[1] for x in out], float), "w": np.array([x[2] for x in out])}


def P(dist, f):
    return float((dist["w"] * f(dist["h"], dist["a"])).sum())


# ---------- calage ----------
def bisect(fn, lo, hi, target, it=40):
    """fn croissante ; renvoie x tel que fn(x)≈target."""
    for _ in range(it):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fit_poisson(diff_target, tot, over_line=None, over_p=None):
    """Renvoie (λh, λa). Si over_p fourni, cale λ total sur P(total > ligne)."""
    def lam_for(lt):
        s = bisect(lambda s: (lambda d: P(d, lambda h, a: h > a) - P(d, lambda h, a: h < a))(poisson_dist(lt * s, lt * (1 - s))), 0.02, 0.98, diff_target, 25)
        return lt * s, lt * (1 - s)
    if over_p is not None:
        lt = bisect(lambda lt: P(poisson_dist(*lam_for(lt)), lambda h, a: h + a > over_line), 0.4, 6.5, over_p, 22)
    else:
        lt = tot
    return lam_for(lt)


def ps_for(p_match, best5):
    f = (lambda p: p ** 3 * (10 - 15 * p + 6 * p * p)) if best5 else (lambda p: p * p * (3 - 2 * p))
    return bisect(f, 0.0, 1.0, clip(p_match, 0.01, 0.99), 40)


# ---------- lecture des sélections ----------
def team_side(text, home, away):
    t, h, a = nrm(text), nrm(home), nrm(away)
    if h and t.startswith(h):
        return "home", t[len(h):].strip()
    if a and t.startswith(a):
        return "away", t[len(a):].strip()
    return None, t


def event(mk_name, label, sel, home, away, dist):
    """Renvoie (fonction(h,a)->bool, période) ou None si la sélection n'est pas modélisable."""
    m, s = nrm(mk_name), nrm(sel)
    period = "full"
    if re.search(r"1(è|e)re mi-temps|1re mt", m) or label == "1re MT":
        period = "h1"
    elif re.search(r"2(è|e)me mi-temps|2e mt", m) or label == "2e MT":
        period = "h2"
    elif re.match(r"1er qt", m):
        period = "q"
    elif re.match(r"(2è|3è|4è) qt", m):
        period = "q"
    elif re.match(r"1er set", m) or re.match(r"2(è|e)me set", m):
        period = "set"
    if re.search(r"joueur|buteur|passeur|aces|tie-break|jeux|possède .* d'avance|extra gains|mi-temps / fin|inscrire|perd le|sans prendre|essai", m):
        return None
    eps = 0.5 if dist == "cont" else 0.5
    # totaux
    mt = re.match(r"^([+-]) de ([\d,]+)$", s)
    if mt:
        x = float(mt.group(2).replace(",", "."))
        side, _ = team_side(mk_name.split(" - ")[-1] if " - " in mk_name else "", home, away)
        if not side and " - " in mk_name:
            side, _ = team_side(mk_name.split(" - ")[0], home, away)
        q = (lambda h, a: h) if side == "home" else (lambda h, a: a) if side == "away" else (lambda h, a: h + a)
        if re.search(r"total de buts \(t\. rég\) - |total de points|nombre de points|nombre total de buts|nombre total de points|buts équipe", m) or label in ("Total buts", "Buts équipe"):
            return ((lambda h, a, q=q, x=x: q(h, a) > x) if mt.group(1) == "+" else (lambda h, a, q=q, x=x: q(h, a) < x)), period
        return None
    if label in ("Les 2 marquent",) or m.startswith("les 2 équipes marquent (t. rég)") or m == "les 2 équipes marquent":
        return ((lambda h, a: (h > 0) & (a > 0)) if s == "oui" else (lambda h, a: (h == 0) | (a == 0))), period
    if m.startswith("les 2 équipes marquent ou + de 2,5"):
        f = lambda h, a: ((h > 0) & (a > 0)) | (h + a > 2.5)
        return (f if s == "oui" else (lambda h, a: ~f(h, a))), period
    if re.search(r"première équipe à marquer|dernière équipe à marquer", m):
        side, _ = team_side(sel, home, away)
        if s == "pas de but":
            return (lambda h, a: (h + a) == 0), period
        if side:
            return ("first", side), period
        return None
    gm = re.search(r"gagne au moins 1 set", m)
    if gm:
        side, _ = team_side(mk_name, home, away)
        if not side:
            return None
        f = (lambda h, a: h >= 1) if side == "home" else (lambda h, a: a >= 1)
        return (f if s == "oui" else (lambda h, a: ~f(h, a))), period
    sc = re.match(r"^(\d) - (\d)$", s)
    if m.startswith("les deux joueurs gagnent un set"):
        f = lambda h, a: (h >= 1) & (a >= 1)
        return (f if s == "oui" else (lambda h, a: ~f(h, a))), period
    if sc and ("score final" in m or m.startswith("score exact")):
        x, y = int(sc.group(1)), int(sc.group(2))
        return (lambda h, a: (h == x) & (a == y)), period
    # écarts / handicaps « X gagne de N ou + », « X ne perd pas ou perd de N ou - »
    side, rest = team_side(sel, home, away)
    if side:
        d = (lambda h, a: h - a) if side == "home" else (lambda h, a: a - h)
        g = re.match(r"^gagne de (\d+)(?: buts| points| sets)? ou \+$", rest)
        if g:
            n = int(g.group(1)); return (lambda h, a: d(h, a) > n - eps), period
        g = re.match(r"^gagne de (\d+)(?: buts| points| sets)? exactement$", rest)
        if g:
            n = int(g.group(1)); return (lambda h, a: abs(d(h, a) - n) < eps), period
        g = re.match(r"^ne perd pas ou perd de (\d+)(?: buts| points| sets)? (?:ou -|exactement)$", rest)
        if g:
            n = int(g.group(1)); return (lambda h, a: d(h, a) > -n - eps), period
        if rest == "ne perd pas":
            return (lambda h, a: d(h, a) > -eps), period
        if rest == "" and re.search(r"vainqueur|résultat|^1er set|^2(è|e)me set|équipe à marquer", m):  # résultat / vainqueur (match, MT, QT, set)
            return (lambda h, a: d(h, a) > 0), period
        return None
    if s == "nul":
        return (lambda h, a: abs(h - a) < 0.25), period
    dc = re.match(r"^(.+) ou (.+)$", sel or "")
    if dc and "double chance" in m:
        fs = []
        for part in dc.groups():
            p = nrm(part)
            if p == "nul":
                fs.append(lambda h, a: h == a)
            else:
                sd, _ = team_side(part, home, away)
                if not sd:
                    return None
                fs.append((lambda h, a: h > a) if sd == "home" else (lambda h, a: a > h))
        return (lambda h, a: fs[0](h, a) | fs[1](h, a)), period
    return None


# ---------- par sport ----------
def team_rates(games):
    """Moyennes marqué/encaissé à partir des 5 derniers matchs ESPN (score + dom./ext.)."""
    sc, co, n = 0.0, 0.0, 0
    for g in games or []:
        try:
            hs, as_ = float(g["hs"]), float(g["as"])
        except Exception:
            continue
        if g.get("at") == "@":
            sc += as_; co += hs
        else:
            sc += hs; co += as_
        n += 1
    return (sc / n, co / n, n) if n >= 3 else None


def main_line(markets, pat):
    """Ligne de total la plus équilibrée (proba sans marge la plus proche de 50 %)."""
    best = None
    for mk in markets:
        if not re.search(pat, nrm(mk["name"])):
            continue
        for x in mk["sel"]:
            mt = re.match(r"^\+ de ([\d,]+)$", x["name"])
            if mt and x["pm"] is not None and (best is None or abs(x["pm"] - 0.5) < abs(best[1] - 0.5)):
                best = (float(mt.group(1).replace(",", ".")), x["pm"])
    return best


SIG = {"basketball": (12.0, 17.0), "rugby_union": (14.0, 13.0), "rugby": (14.0, 13.0)}


def build_dists(sport, markets, p_home_mkt, p_home_mod, p_draw, fH, fA, h2h):
    """Renvoie (dists_marché, dists_modèle, info) avec une distribution par période."""
    info = {}
    if sport == "football":
        diff_m = p_home_mkt[0] - p_home_mkt[1]
        diff_k = p_home_mod[0] - p_home_mod[1]
        ln = main_line(markets, r"^nombre total de buts")
        lhm, lam = fit_poisson(diff_m, 2.6, *(ln if ln else (None, None)))
        lt_m = lhm + lam
        rH, rA = team_rates(fH), team_rates(fA)
        lt_k = lt_m
        if rH and rA:
            data = (rH[0] + rA[1]) / 2 + (rA[0] + rH[1]) / 2
            if h2h and len(h2h) >= 3:
                gs = [sum(int(v) for v in g["score"].split("-")) for g in h2h if re.match(r"^\d+-\d+$", g.get("score") or "")]
                if len(gs) >= 3:
                    data = 0.8 * data + 0.2 * (sum(gs) / len(gs))
            lt_k = lt_m + clip(0.35 * (data - lt_m), -0.45, 0.45)
            info["goals"] = {"market": round(lt_m, 2), "data": round(data, 2), "model": round(lt_k, 2)}
        lhk, lak = fit_poisson(diff_k, lt_k)
        mk = lambda lh, la, f: poisson_dist(lh * f, la * f)
        return ({"full": mk(lhm, lam, 1), "h1": mk(lhm, lam, .45), "h2": mk(lhm, lam, .55)},
                {"full": mk(lhk, lak, 1), "h1": mk(lhk, lak, .45), "h2": mk(lhk, lak, .55)}, info)
    if sport in SIG:
        sd, st = SIG[sport]
        if sport == "basketball" and any("nba" in nrm(x) for x in []):
            pass
        pat = r"^nombre total de points$" if sport == "basketball" else r"^nombre de points$"
        ln = main_line(markets, pat)
        if not ln:
            mt_m = None
        else:
            mt_m = ln[0] + st * N01.inv_cdf(clip(ln[1], 0.02, 0.98))
        if mt_m is None:
            mt_m = 160.0 if sport == "basketball" else 45.0
        md_m = sd * N01.inv_cdf(clip(p_home_mkt[0] / max(1e-6, p_home_mkt[0] + p_home_mkt[1]), 0.01, 0.99))
        md_k = sd * N01.inv_cdf(clip(p_home_mod[0] / max(1e-6, p_home_mod[0] + p_home_mod[1]), 0.01, 0.99))
        mt_k = mt_m
        rH, rA = team_rates(fH), team_rates(fA)
        if rH and rA and ln:
            data = (rH[0] + rA[1]) / 2 + (rA[0] + rH[1]) / 2
            if abs(data - mt_m) < 0.25 * mt_m:  # garde-fou : même championnat / même format
                mt_k = mt_m + clip(0.3 * (data - mt_m), -6, 6)
                info["points"] = {"market": round(mt_m, 1), "data": round(data, 1), "model": round(mt_k, 1)}
        d = lambda mt, md, f, g: normal_dist(mt * f, md * f, st * g, sd * g)
        return ({"full": d(mt_m, md_m, 1, 1), "q": d(mt_m, md_m, .25, .5), "h1": d(mt_m, md_m, .5, .7), "h2": d(mt_m, md_m, .5, .7)},
                {"full": d(mt_k, md_k, 1, 1), "q": d(mt_k, md_k, .25, .5), "h1": d(mt_k, md_k, .5, .7), "h2": d(mt_k, md_k, .5, .7)}, info)
    if sport == "tennis":
        best5 = any(nrm(mk["name"]).startswith("score final") and any(x["name"].startswith("3") for x in mk["sel"]) for mk in markets)
        pm = p_home_mkt[0] / max(1e-6, p_home_mkt[0] + p_home_mkt[1])
        pk = p_home_mod[0] / max(1e-6, p_home_mod[0] + p_home_mod[1])
        sm, sk = ps_for(pm, best5), ps_for(pk, best5)
        one = lambda p: {"h": np.array([1.0, 0.0]), "a": np.array([0.0, 1.0]), "w": np.array([p, 1 - p])}
        return ({"full": sets_dist(sm, best5), "set": one(sm)}, {"full": sets_dist(sk, best5), "set": one(sk)}, info)
    return None, None, info


def prob(dists, ev):
    f, period = ev
    d = dists.get(period)
    if d is None:
        return None
    if isinstance(f, tuple) and f[0] == "first":
        lh, la = d.get("lh"), d.get("la")
        if lh is None:
            return None
        p0 = math.exp(-(lh + la))
        return (1 - p0) * ((lh if f[1] == "home" else la) / (lh + la))
    return P(d, f)


def apply(sport, markets, home, away, main_pm, main_pk, fH, fA, h2h):
    """Ajuste x['pk'] pour toutes les sélections modélisables (hors marché principal). Renvoie un résumé."""
    if np is None or not main_pm or len(main_pm) not in (2, 3):
        return {"analysed": 0}
    pmh, pma = main_pm[0], main_pm[-1]
    pkh, pka = main_pk[0], main_pk[-1]
    try:
        dm, dk, info = build_dists(sport, markets, (pmh, pma), (pkh, pka), main_pm[1] if len(main_pm) == 3 else 0, fH, fA, h2h)
    except Exception as e:  # noqa
        return {"analysed": 0, "error": str(e)}
    if not dm:
        return {"analysed": 0}
    kind = "cont" if sport in SIG else "disc"
    n, by = 0, {}
    for i, mk in enumerate(markets):
        if i == 0:
            continue  # marché principal déjà traité par le modèle de base
        done = False
        for x in mk["sel"]:
            if x["pm"] is None:
                continue
            ev = event(mk["name"], mk["label"], x["name"], home, away, kind)
            if not ev:
                continue
            a, b = prob(dm, ev), prob(dk, ev)
            if a is None or b is None:
                continue
            x["pk"] = clip(x["pm"] + clip(b - a, -CAP, CAP), 0.01, 0.99)
            x["adj"] = True
            done = True
        if done:
            n += 1; by[mk["label"]] = by.get(mk["label"], 0) + 1
    if sport == "football" and dm.get("full", {}).get("lh"):
        for lab, k in scorers(markets, dm["full"], dk["full"], home, away).items():
            by[lab] = by.get(lab, 0) + k; n += k
    info.update({"analysed": n, "by": by, "_dk": dk, "_kind": kind})
    return info


# ---------- buteurs / joueurs décisifs (football) ----------
SCORER = [  # (motif du nom de marché, famille, nb de buts/actions)
    (r"^buteur$", "but", 1), (r"^buteur ou son remplaçant$", "but_r", 1),
    (r"^joueur \+ son remplaçant buteur 2 fois", "but_r", 2), (r"^joueur \+ son remplaçant buteur 3 fois", "but_r", 3),
    (r"^joueur décisif \(buteur ou passeur décisif dans le tps rég\.\) · 1 fois", "dec", 1),
    (r"^joueur décisif 2 fois", "dec", 2), (r"^joueur décisif 3 fois", "dec", 3),
    (r"^joueur ou son remplaçant décisif", "dec_r", 1), (r"^joueur \+ son remplaçant décisif 2 fois", "dec_r", 2),
    (r"^joueur \+ son remplaçant décisif 3 fois", "dec_r", 3),
]
TARGET = {"but": 0.9, "but_r": 0.95, "dec": 1.55, "dec_r": 1.6}  # part des buts (ou buts+passes) couverte par la liste


def p_atleast(r, k):
    return 1 - sum(math.exp(-r) * r ** i / math.factorial(i) for i in range(k))


def scorers(markets, dm, dk, home, away):
    """Proba buteur = taux du joueur (déduit des cotes Betclic, marge retirée en calant la somme des taux sur les buts
    attendus de l'équipe) × (buts attendus modèle / buts attendus marché). Renvoie {label: nb de marchés ajustés}."""
    lam = {"home": (dm["lh"], dk["lh"]), "away": (dm["la"], dk["la"])}
    by, rates = {}, {}
    fams = {}
    for mk in markets:
        nm = nrm(re.sub(r"\s*\((t\. rég|tps rég\.?)\)", "", mk["name"]))
        for rx, fam, k in SCORER:
            if re.search(rx, nm):
                fams.setdefault(fam, []).append((k, mk)); break
    teams = [t for t in {x.get("team") for mk in markets for x in mk["sel"]} if t]
    side_of = {}
    from difflib import SequenceMatcher
    for t in teams:
        sh, sa = SequenceMatcher(None, nrm(t), nrm(home)).ratio(), SequenceMatcher(None, nrm(t), nrm(away)).ratio()
        if max(sh, sa) >= 0.5 and sh != sa:
            side_of[t] = "home" if sh > sa else "away"
    for fam, lst in fams.items():
        base = next((mk for k, mk in lst if k == 1), None)
        if not base:
            continue
        f_team = {}
        for t, side in side_of.items():
            raw = [1 / x["odds"] for x in base["sel"] if x.get("team") == t]
            if not raw:
                continue
            tgt = TARGET[fam] * lam[side][0]
            tot = lambda f: sum(-math.log(max(1e-9, 1 - min(0.97, f * p))) for p in raw)
            f = 1.0 if tot(1.0) <= tgt else bisect(tot, 0.3, 1.0, tgt, 30)
            f_team[t] = f
        for k, mk in lst:
            done = False
            for x in mk["sel"]:
                t = x.get("team")
                if t not in f_team:
                    continue
                side = side_of[t]; ratio = lam[side][1] / lam[side][0]
                pm = clip(f_team[t] / x["odds"], 0.005, 0.97)
                if k == 1:
                    r = -math.log(1 - pm); rates[(fam, x["name"])] = r
                else:
                    r = rates.get((fam, x["name"]))
                    if r is None:
                        continue
                a, b = p_atleast(r, k), p_atleast(r * ratio, k)
                x["pm"] = pm
                x["pk"] = clip(pm + clip(b - a, -CAP, CAP), 0.005, 0.99)
                x["adj"] = True; done = True
            if done:
                by[mk["label"]] = by.get(mk["label"], 0) + 1
    # double chance buteur « H. Kane / M. Olise »
    full = {}
    for (fam, name), r in rates.items():
        if fam == "but":
            full[nrm(name).split(" ")[-1]] = (name, r)
    team_of = {x["name"]: side_of.get(x.get("team")) for mk in markets for x in mk["sel"] if x.get("team")}
    for mk in markets:
        if not nrm(mk["name"]).startswith("double chance buteur"):
            continue
        done = False
        for x in mk["sel"]:
            parts = [nrm(p).split(" ")[-1] for p in x["name"].split(" / ")]
            if len(parts) != 2 or not all(p in full for p in parts):
                continue
            (n1, r1), (n2, r2) = full[parts[0]], full[parts[1]]
            s1, s2 = team_of.get(n1), team_of.get(n2)
            if not s1 or not s2:
                continue
            q1, q2 = lam[s1][1] / lam[s1][0], lam[s2][1] / lam[s2][0]
            a = 1 - math.exp(-r1 - r2); b = 1 - math.exp(-r1 * q1 - r2 * q2)
            pm = clip((1 / x["odds"]) / 1.12, 0.005, 0.97)  # marge type des marchés joueurs (~12 %)
            x["pm"] = pm; x["pk"] = clip(pm + clip(b - a, -CAP, CAP), 0.005, 0.99); x["adj"] = True; done = True
        if done:
            by[mk["label"]] = by.get(mk["label"], 0) + 1
    return by


def joint(dist_by_period, evs):
    """Proba jointe de plusieurs événements du même match (même période) sur la distribution du modèle."""
    if np is None or not evs:
        return None
    per = {e[1] for e in evs}
    if len(per) != 1 or any(isinstance(e[0], tuple) for e in evs):
        return None
    d = dist_by_period.get(per.pop())
    if d is None:
        return None
    mask = np.ones_like(d["w"], dtype=bool)
    for f, _ in evs:
        mask &= f(d["h"], d["a"])
    return float((d["w"] * mask).sum())
