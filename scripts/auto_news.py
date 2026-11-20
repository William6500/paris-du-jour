#!/usr/bin/env python3
"""Étape 2 bis : actus automatiques par match (Google Actualités RSS, 7 derniers jours).
Pour chaque match : une recherche « Équipe A Équipe B » + une recherche par équipe (langue du championnat).
Garde les titres qui citent vraiment l'équipe, avec lien, source et date. Signaux simples par mots-clés
(blessure/suspension, crise d'entraîneur, bonne dynamique) → ajustement modeste borné à ±2 pts (appliqué dans build.py).
Cache par requête (.cache/news.json, 6 h) pour garder des relèves rapides. Écrit news.json."""
import json, os, re, time, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

CACHE_P, TTL = ".cache/news.json", 6 * 3600
LANG = [("Premier League|MLS|NBA|A-League|NBL|Australie|Angl|États-Unis|EAU|Hong Kong|ATP|WTA|Billie|Nations Championship", "en"),
        ("LaLiga|Espagne", "es"), ("Serie A$|Italie", "it"), ("Bundesliga|Allemagne", "de"),
        ("Brésil|Liga Betclic|Portugal", "pt"), ("Eredivisie|Pays-Bas", "nl")]
ED = {"fr": ("fr", "FR", "FR:fr"), "en": ("en-GB", "GB", "GB:en"), "es": ("es", "ES", "ES:es"), "it": ("it", "IT", "IT:it"),
      "de": ("de", "DE", "DE:de"), "pt": ("pt-PT", "PT", "PT:pt-150"), "nl": ("nl", "NL", "NL:nl")}
SPORT_WORD = {"football": "football OR foot OR match OR soccer OR fútbol OR calcio OR fußball OR futebol OR voetbal",
              "basketball": "basket OR basketball OR NBA", "tennis": "tennis", "rugby_union": "rugby"}
KW = {
    "injury": r"bless|forfait|absent|incertain|suspendu|suspension|carton rouge|injur|ruled out|out for|doubtful|sidelined|suspen|lesi[oó]n|baja|infortun|squalific|verletz|ausfall|gesperrt|lesionad|desfalque|blessure|geblesseerd|schors",
    "coach": r"limog|viré|démission|sur la sellette|crise|sacked|fired|under pressure|crisis|ultimatum|destitu|cesad|esoner|entlassen|krise|despedid|demitid|ontslagen",
    "good": r"invaincu|série de victoires|enchaîne|unbeaten|winning run|winning streak|racha|imbattut|serie positiva|ungeschlagen|siegesserie|invicto|ongeslagen|retour de|returns|back in training|rientr|zurück",
}
STOP = {"fc", "f", "sc", "ac", "as", "cf", "club", "de", "real", "united", "city", "the", "and", "sporting", "athletic", "atletico", "olympique", "stade", "racing", "sv", "vfb", "vfl", "rc", "us", "cd", "ud", "ca", "afc", "ss", "fk"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def keys(team):
    toks = [t for t in norm(team).split() if len(t) >= 3 and t not in STOP]
    return toks or [t for t in norm(team).split() if len(t) >= 3] or [norm(team)]


def mentions(title, team):
    t = " " + norm(title) + " "
    return any(f" {k} " in t for k in keys(team))


def clean_team(n):
    return re.sub(r"\s+(F\.|U\d\d|II|B)$", "", n.strip())


def lang_of(comp):
    for rx, l in LANG:
        if re.search(rx, comp):
            return l
    return "fr"


def rss(q, lang, cache):
    key = f"{lang}|{q}"
    c = cache.get(key)
    if c and time.time() - c["t"] < TTL:
        return c["items"]
    hl, gl, ceid = ED[lang]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:7d", "hl": hl, "gl": gl, "ceid": ceid})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (paris-du-jour; usage personnel)"})
    items = []
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                root = ET.fromstring(r.read())
            for it in root.iter("item"):
                title = it.findtext("title") or ""
                src = it.findtext("source") or ""
                if src and title.endswith(" - " + src):
                    title = title[: -len(src) - 3]
                try:
                    d = parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d")
                except Exception:
                    d = None
                items.append({"title": title.strip(), "url": it.findtext("link"), "source": src, "date": d})
            break
        except Exception:
            time.sleep(1.5)
    else:
        return None  # échec : pas mis en cache
    cache[key] = {"t": time.time(), "items": items[:25]}
    return items[:25]


JUNK = re.compile(r"pronostic|cotes? |cote &|tipp|quoten|wett|billets?|tickets?|streaming|regarder|en direct|live|watch|prediction|odds|betting|apuesta|scommess|pronóstico|prognose|voorspelling|sur quelle chaîne|à quelle heure|horario|tv-", re.I)


def flags(items):
    out = {k: [] for k in KW}
    for it in items:
        for k, rx in KW.items():
            if re.search(rx, it["title"], re.I):
                out[k].append(it["title"])
    return out


def main():
    raw = json.load(open("raw_odds.json", encoding="utf-8"))
    try:
        cache = json.load(open(CACHE_P, encoding="utf-8"))
    except Exception:
        cache = {}
    cache = {k: v for k, v in cache.items() if time.time() - v["t"] < TTL}
    jobs, plan = set(), {}
    for m in raw["matches"]:
        if " - " not in m["name"]:
            continue
        h, a = (clean_team(x) for x in m["name"].split(" - ", 1))
        lang = lang_of(m["competition"]); sw = SPORT_WORD.get(m["sport"], "")
        qm = f'"{h}" "{a}"'
        qh, qa = f'"{h}" ({sw})', f'"{a}" ({sw})'
        plan[m["id"]] = (h, a, [(qm, "fr")] + ([(qm, lang)] if lang != "fr" else []), (qh, lang), (qa, lang))
        jobs |= {(qm, "fr"), (qm, lang), (qh, lang), (qa, lang)}
    t0 = time.time()
    todo = [j for j in jobs if f"{j[1]}|{j[0]}" not in cache]
    res = {}
    with ThreadPoolExecutor(10) as ex:
        for j, items in zip(todo, ex.map(lambda j: rss(j[0], j[1], cache), todo)):
            res[j] = items
    os.makedirs(".cache", exist_ok=True)
    json.dump(cache, open(CACHE_P, "w", encoding="utf-8"), ensure_ascii=False)
    get = lambda j: (cache.get(f"{j[1]}|{j[0]}") or {}).get("items")
    out, n_news, failed = {}, 0, 0
    for mid, (h, a, qms, qh, qa) in plan.items():
        if any(get(j) is None for j in qms + [qh, qa]):
            failed += 1
        seen = set()
        def pick(items, test, n):
            r = []
            for it in sorted(items or [], key=lambda x: x["date"] or "", reverse=True):
                k = norm(it["title"])[:60]
                if k in seen or JUNK.search(it["title"]) or not test(it["title"]):
                    continue
                seen.add(k); r.append(it)
                if len(r) == n:
                    break
            return r
        both = pick(sum((get(j) or [] for j in qms), []), lambda t: mentions(t, h) and mentions(t, a), 4)
        th = pick(get(qh), lambda t: mentions(t, h) and not mentions(t, a), 3)
        ta = pick(get(qa), lambda t: mentions(t, a) and not mentions(t, h), 3)
        fh, fa = flags(th), flags(ta)
        score = lambda f: -min(len(f["injury"]), 2) - 1.5 * min(len(f["coach"]), 1) + 0.5 * min(len(f["good"]), 1)
        adj = max(-2.0, min(2.0, score(fh) - score(fa)))
        sig = []
        for side, name, f in (("home", h, fh), ("away", a, fa)):
            if f["injury"]: sig.append(f"{name} : {len(f['injury'])} titre(s) blessure/suspension")
            if f["coach"]: sig.append(f"{name} : titre(s) crise/entraîneur")
            if f["good"]: sig.append(f"{name} : dynamique positive dans la presse")
        if both or th or ta:
            n_news += 1
        out[mid] = {"match": both, "home": th, "away": ta, "adj": round(adj, 2), "signals": sig}
    json.dump(out, open("news.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Actus : {n_news}/{len(plan)} matchs avec titres, {len(todo)} requêtes ({len(jobs) - len(todo)} en cache), "
          f"{sum(1 for v in res.values() if v is None)} échecs, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
