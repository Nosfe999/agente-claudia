"""Fuentes 100 % gratuitas: Google News/RSS, Yahoo Finance (+ Kraken y Twelve Data de respaldo),
calendario económico de Forex Factory y Fear & Greed de cripto."""
import calendar as pycal
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import requests

from . import config

log = logging.getLogger("sources")
UA = {"User-Agent": "Mozilla/5.0 (compatible; agente-claudia/2.0)"}


def get(url, timeout=15, tries=2, **kw):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


# ---------------- noticias ----------------
def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _news_id(title):
    t = re.sub(r"\s+-\s+[^-]+$", "", title.lower().strip())  # quita " - Fuente" de Google News
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return hashlib.sha1(t.encode()).hexdigest()[:16]


def parse_feed(content, hint, default_source="", limit=15):
    d = feedparser.parse(content)
    out = []
    for e in d.entries[:limit]:
        title = _clean(e.get("title", ""))
        if not title:
            continue
        pp = e.get("published_parsed") or e.get("updated_parsed")
        ts = pycal.timegm(pp) if pp else time.time()
        src = (e.get("source") or {}).get("title") or default_source or d.feed.get("title", "")
        out.append({"id": _news_id(title), "title": title, "link": e.get("link", ""),
                    "source": src, "ts": ts, "summary": _clean(e.get("summary", ""))[:220], "hint": hint})
    return out


def _google_url(q):
    return f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=en-US&gl=US&ceid=US:en"


def _fetch_feed(args):
    url, hint, src = args
    try:
        return True, parse_feed(get(url, tries=1).content, hint, src)
    except Exception as e:
        log.warning("feed falló %s: %s", url[:70], e)
        return False, []


def fetch_news():
    jobs = [(_google_url(q), a, "") for a, c in config.ASSETS.items() for q in c["queries"]]
    jobs += [(_google_url(q), "MACRO", "") for q in config.MACRO_QUERIES]
    jobs += [(u, "MACRO", n) for n, u in config.EXTRA_FEEDS.items()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_feed, jobs))
    ok = sum(1 for k, _ in results if k)
    if ok == 0:
        raise RuntimeError("todas las fuentes de noticias fallaron")
    seen, out = set(), []
    for _, items in results:
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                out.append(it)
    return out, ok, len(jobs)


# ---------------- velas (OHLC) ----------------
def _bars(rows):
    """rows: [(t,o,h,l,c)] -> lista de dicts ordenada, sin huecos None."""
    out = [{"t": t, "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in rows
           if None not in (t, o, h, l, c)]
    out.sort(key=lambda b: b["t"])
    return out


def yahoo_candles(sym, rng, interval):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range={rng}&interval={interval}"
    res = get(url).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return _bars(zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"]))


def kraken_candles(pair, interval_min):
    d = get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}").json()
    if d.get("error"):
        raise RuntimeError(d["error"])
    key = next(k for k in d["result"] if k != "last")
    return _bars((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in d["result"][key])


def twelve_candles(sym, interval, size):
    d = get("https://api.twelvedata.com/time_series?symbol=%s&interval=%s&outputsize=%d&timezone=UTC&apikey=%s"
            % (quote(sym), interval, size, config.TWELVEDATA_KEY)).json()
    if "values" not in d:
        raise RuntimeError(d.get("message", "sin datos"))
    rows = []
    for v in d["values"]:
        t = v["datetime"] if " " in v["datetime"] else v["datetime"] + " 00:00:00"
        ts = pycal.timegm(datetime.strptime(t, "%Y-%m-%d %H:%M:%S").timetuple())
        rows.append((ts, float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
    return _bars(rows)


def candles(asset):
    """Cadena de respaldo. Devuelve (velas_15m, velas_diarias, fuente)."""
    c, errs = config.ASSETS[asset], []
    chain = [("yahoo", lambda: (yahoo_candles(c["yf"], "5d", "15m"), yahoo_candles(c["yf"], "1mo", "1d")))]
    if c.get("kraken"):
        chain.append(("kraken", lambda: (kraken_candles(c["kraken"], 15), kraken_candles(c["kraken"], 1440))))
    if config.TWELVEDATA_KEY and c.get("td"):
        chain.append(("twelvedata", lambda: (twelve_candles(c["td"], "15min", 200),
                                             twelve_candles(c["td"], "1day", 10))))
    for name, fn in chain:
        try:
            intra, daily = fn()
            if len(intra) >= 30 and len(daily) >= 3:
                return intra, daily, name
            errs.append(f"{name}: pocas velas ({len(intra)}/{len(daily)})")
        except Exception as e:
            errs.append(f"{name}: {str(e)[:80]}")
    raise RuntimeError("; ".join(errs))


SESSION_HOURS = {"asia": (20, 21, 22, 23), "lon": (2, 3, 4), "ny": (7, 8, 9)}  # killzones, hora NY


def chart_levels(intra):
    """Niveles calculados de velas 15m: rango 24h, rango de la última killzone de cada sesión y
    el FVG (3 velas) sin rellenar más cercano al precio. Función pura."""
    tz = ZoneInfo(config.MARKET_TZ)
    last24 = intra[-96:]
    out = {"h24": max(b["h"] for b in last24), "l24": min(b["l"] for b in last24)}
    groups = {}
    for b in intra:
        t = datetime.fromtimestamp(b["t"], tz)
        for name, hours in SESSION_HOURS.items():
            if t.hour in hours:
                g = groups.setdefault((name, t.date()), [])
                g.append(b)
    for name in SESSION_HOURS:
        keys = [k for k in groups if k[0] == name]
        if keys:
            g = groups[max(keys, key=lambda k: k[1])]
            out[f"{name}_h"], out[f"{name}_l"] = max(b["h"] for b in g), min(b["l"] for b in g)
    price, best = intra[-1]["c"], None
    seg = intra[-96:]
    for i in range(2, len(seg)):
        a, c = seg[i - 2], seg[i]
        if c["l"] > a["h"]:
            kind, lo, hi = "alcista", a["h"], c["l"]
        elif c["h"] < a["l"]:
            kind, lo, hi = "bajista", c["h"], a["l"]
        else:
            continue
        later = seg[i + 1:]
        if kind == "alcista" and any(b["l"] <= lo for b in later):
            continue  # ya rellenado
        if kind == "bajista" and any(b["h"] >= hi for b in later):
            continue
        dist = 0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
        if best is None or dist < best[0]:
            best = (dist, {"fvg": kind, "fvg_lo": lo, "fvg_hi": hi})
    if best:
        out.update(best[1])
    return out


def build_snapshot(asset, intra, daily, source="", now=None):
    """Función pura (testeable): métricas intradía a partir de velas 15m y diarias."""
    if not intra or not daily or len(intra) < 30 or len(daily) < 3:
        return None
    now = now or time.time()
    n = len(intra)
    trs = []
    for i in range(n - 15, n - 1):
        b, p = intra[i], intra[i - 1]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])))
    atr = sum(trs) / len(trs)
    ranges = [b["h"] - b["l"] for b in intra]
    price = intra[-1]["c"]

    def move(k):
        ref = intra[-1 - k]["c"]
        return price - ref, (price / ref - 1) * 100

    d1h, p1h = move(4)
    _, p4h = move(16)
    prev = daily[-2]
    last_ts = intra[-1]["t"]
    age = (now - last_ts) / 60
    max_age = 90 if config.ASSETS[asset].get("h24") else 180
    return {
        **chart_levels(intra),
        "asset": asset, "price": round(price, 5), "atr15": round(atr, 5),
        "d_1h": d1h, "chg_1h": round(p1h, 3), "chg_4h": round(p4h, 3),
        "chg_day": round((price / prev["c"] - 1) * 100, 3),
        "last_range": ranges[-1], "avg_range": sum(ranges[-41:-1]) / 40,
        "pdh": prev["h"], "pdl": prev["l"], "pdc": prev["c"],
        "ts": last_ts, "age_min": round(age, 1), "stale": age > max_age, "source": source,
    }


def snapshot(asset):
    intra, daily, src = candles(asset)
    return build_snapshot(asset, intra, daily, src)


# ---------------- calendario económico ----------------
def fetch_calendar():
    raw = get("https://nfs.faireconomy.media/ff_calendar_thisweek.json").json()
    out = []
    for e in raw:
        if e.get("impact") != "High" or e.get("country") not in ("USD", "EUR"):
            continue
        try:
            ts = datetime.fromisoformat(e["date"]).timestamp()
        except (KeyError, ValueError):
            continue
        out.append({"title": e.get("title", ""), "country": e["country"], "ts": ts,
                    "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    out.sort(key=lambda x: x["ts"])
    return out


# ---------------- sentimiento cripto ----------------
def fetch_fear_greed():
    d = get("https://api.alternative.me/fng/?limit=1").json()["data"][0]
    return {"value": int(d["value"]), "label": d["value_classification"]}
