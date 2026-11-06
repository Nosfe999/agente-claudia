import calendar as pycal
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import feedparser
import pandas as pd
import requests

import config

log = logging.getLogger("sources")
UA = {"User-Agent": "mercado-agent/1.0"}


def _get(url, timeout=15):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


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
        out.append({
            "id": _news_id(title), "title": title, "link": e.get("link", ""),
            "source": src, "ts": ts, "summary": _clean(e.get("summary", ""))[:220], "hint": hint,
        })
    return out


def _google_url(q):
    return f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=en-US&gl=US&ceid=US:en"


def _fetch_one(args):
    url, hint, src = args
    try:
        return True, parse_feed(_get(url).content, hint, src)
    except Exception as e:
        log.warning("feed falló %s: %s", url[:80], e)
        return False, []


def fetch_news():
    jobs = []
    for asset, cfg in config.ASSETS.items():
        for q in cfg["queries"]:
            jobs.append((_google_url(q), asset, ""))
    for q in config.MACRO_QUERIES:
        jobs.append((_google_url(q), "MACRO", ""))
    for name, url in config.EXTRA_FEEDS.items():
        jobs.append((url, "MACRO", name))
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_one, jobs))
    if not any(ok for ok, _ in results):
        raise RuntimeError("todas las fuentes de noticias fallaron")
    seen, out = set(), []
    for _, items in results:
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                out.append(it)
    return out


# ---------------- precios ----------------
def _history(ticker, period, interval):
    import yfinance as yf
    return yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)


def build_snapshot(asset, intra, daily, now=None):
    """Función pura (testeable): calcula métricas intradía a partir de velas 15m y diarias."""
    if intra is None or daily is None or len(intra) < 30 or len(daily) < 3:
        return None
    now = now or time.time()
    c, h, l = (intra[k].astype(float) for k in ("Close", "High", "Low"))
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[-15:-1].mean())
    rng = h - l
    price = float(c.iloc[-1])

    def move(n):
        ref = float(c.iloc[-1 - n])
        return price - ref, (price / ref - 1) * 100

    d1h, p1h = move(4)
    d4h, p4h = move(16)
    prev_day = daily.iloc[-2]
    pdc = float(prev_day["Close"])
    last_ts = intra.index[-1].timestamp()
    age_min = (now - last_ts) / 60
    max_age = 90 if config.ASSETS[asset].get("h24") else 180
    return {
        "asset": asset, "price": round(price, 5), "atr15": round(atr, 5),
        "d_1h": d1h, "chg_1h": round(p1h, 3), "chg_4h": round(p4h, 3),
        "chg_day": round((price / pdc - 1) * 100, 3),
        "last_range": float(rng.iloc[-1]), "avg_range": float(rng.iloc[-41:-1].mean()),
        "pdh": float(prev_day["High"]), "pdl": float(prev_day["Low"]), "pdc": pdc,
        "week_high": float(h.max()), "week_low": float(l.min()),
        "ts": last_ts, "age_min": round(age_min, 1), "stale": age_min > max_age,
    }


def snapshot(asset):
    t = config.ASSETS[asset]["yf"]
    return build_snapshot(asset, _history(t, "5d", "15m"), _history(t, "1mo", "1d"))


# ---------------- calendario económico ----------------
_cal = {"ts": 0, "data": []}


def fetch_calendar(force=False):
    if not force and time.time() - _cal["ts"] < 25 * 60 and _cal["data"]:
        return _cal["data"]
    try:
        raw = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json").json()
    except Exception:
        if _cal["data"]:
            return _cal["data"]
        raise
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
    _cal.update(ts=time.time(), data=out)
    return out


# ---------------- sentimiento cripto ----------------
_fg = {"ts": 0, "data": None}


def fetch_fear_greed():
    if time.time() - _fg["ts"] < 30 * 60:
        return _fg["data"]
    try:
        d = _get("https://api.alternative.me/fng/?limit=1").json()["data"][0]
        _fg.update(ts=time.time(), data={"value": int(d["value"]), "label": d["value_classification"]})
    except Exception as e:
        log.warning("fear&greed falló: %s", e)
    return _fg["data"]
