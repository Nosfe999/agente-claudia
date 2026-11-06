import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from agent import config, llm, signals, sources  # noqa: E402
from agent import run as runmod  # noqa: E402


def bars(n=200, jump=0.0, age_min=0, seed=1):
    import random
    random.seed(seed)
    now = time.time() - age_min * 60
    c, out = 2000.0, []
    for i in range(n):
        c += random.gauss(0, 0.3)
        out.append({"t": now - (n - 1 - i) * 900, "o": c, "h": c + 0.3, "l": c - 0.3, "c": c})
    for k in range(1, 5):  # salto en la última hora
        out[-5 + k]["c"] += jump * k / 4
        out[-5 + k]["h"] += jump * k / 4
        out[-5 + k]["l"] += jump * k / 4
    return out


DAILY = [{"t": 0, "o": 1, "h": 2005, "l": 1980, "c": 1990},
         {"t": 1, "o": 1, "h": 2010, "l": 1990, "c": 2000},
         {"t": 2, "o": 1, "h": 2012, "l": 1995, "c": 2001}]


def test_signal_impulse_down():
    s = sources.build_snapshot("XAUUSD", bars(jump=-15), DAILY, "test")
    assert s and not s["stale"]
    kinds = {x["kind"]: x for x in signals.check_signals("XAUUSD", s)}
    assert kinds["impulso_1h"]["direction"] == "bajista"


def test_no_signal_when_calm_or_stale_or_missing():
    calm = sources.build_snapshot("XAUUSD", bars(jump=0), DAILY, "t")
    assert not any(x["kind"] == "impulso_1h" for x in signals.check_signals("XAUUSD", calm))
    stale = sources.build_snapshot("XAUUSD", bars(jump=-15, age_min=600), DAILY, "t")
    assert stale["stale"] and signals.check_signals("XAUUSD", stale) == []
    assert sources.build_snapshot("XAUUSD", [], [], "t") is None
    assert sources.build_snapshot("XAUUSD", bars(10), DAILY, "t") is None


def test_parse_feed_and_dedupe_id():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><title>Riot Platforms sells 8,962 BTC - Reuters</title><link>https://e.com/1</link>
    <pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;x&lt;/b&gt; hola</description></item>
    <item><title>Riot Platforms sells 8,962 BTC - CoinDesk</title><link>https://e.com/2</link></item></channel></rss>"""
    it = sources.parse_feed(rss, "BTC")
    assert len(it) == 2 and it[0]["id"] == it[1]["id"] and it[0]["summary"] == "x hola"


def test_parse_classification_is_robust():
    items = [{"title": "x"}, {"title": "y"}]
    txt = '```json\n[{"i":0,"assets":["BTC","FOO"],"direction":"bajista","severity":9,"confidence":2,"reason":"r"},{"i":7,"assets":["BTC"]},{"i":1,"assets":[]}]\n```'
    out = llm.parse_classification(txt, items)
    assert len(out) == 1 and out[0]["assets"] == ["BTC"] and out[0]["severity"] == 5 and out[0]["confidence"] == 1.0
    assert llm.parse_classification("basura", items) == []
    assert llm.parse_classification("[{rota", items) == []


def test_fallback_classifier():
    items = [
        {"title": "Riot Platforms sells 8,962 BTC to fund data centers", "summary": "", "hint": "BTC", "source": "x", "link": ""},
        {"title": "Cooking recipes for winter", "summary": "", "hint": "BTC", "source": "x", "link": ""},
        {"title": "Stocks tumble as tariffs hit tech", "summary": "", "hint": "MACRO", "source": "x", "link": ""},
    ]
    out = llm.fallback_classify(items)
    by = {o["item"]["title"][:5]: o for o in out}
    assert by["Riot "]["direction"] == "bajista" and by["Riot "]["assets"] == ["BTC"]
    assert by["Stock"]["assets"] == ["NASDAQ", "SP500"]
    assert len(out) == 2


def test_llm_unavailable_without_key():
    config.GEMINI_API_KEY = ""
    try:
        llm.call({}, "s", "u")
        assert False
    except llm.LLMUnavailable:
        pass


def test_cycle_end_to_end_with_fakes(monkeypatch):
    """Ciclo completo sin red: noticia -> clasificación IA caída -> respaldo; señal de precio; archivos JSON."""
    pushed = []
    monkeypatch.setattr(runmod.notify, "push", lambda *a, **k: pushed.append(a[0]) or True)
    now = time.time()
    news = [{"id": "n1", "title": "Riot Platforms sells 8,962 BTC", "link": "https://e.com", "source": "Reuters",
             "ts": now - 600, "summary": "", "hint": "BTC"}]
    monkeypatch.setattr(runmod.sources, "fetch_news", lambda: (news, 1, 1))
    monkeypatch.setattr(runmod.sources, "fetch_calendar", lambda: [])
    monkeypatch.setattr(runmod.sources, "fetch_fear_greed", lambda: {"value": 40, "label": "Fear"})
    snap = sources.build_snapshot("XAUUSD", bars(jump=-15), DAILY, "fake")

    def fake_snapshot(asset):
        if asset == "XAUUSD":
            return snap
        raise RuntimeError("sin datos")
    monkeypatch.setattr(runmod.sources, "snapshot", fake_snapshot)

    # 1er ciclo: bootstrap (marca vistos, no alerta por noticias)
    runmod.main()
    st = json.load(open(os.path.join(config.DATA_DIR, "state.json")))
    assert "n1" in st["seen"]
    alerts = json.load(open(os.path.join(config.DATA_DIR, "alerts.json")))
    assert any(a["kind"] == "impulso_1h" for a in alerts) and not any(a["kind"] == "noticia" for a in alerts)

    # 2º ciclo con noticia nueva y sin clave de IA -> respaldo por reglas
    st["seen"] = {"otra": now}
    st["last"].pop("noticias", None)
    json.dump(st, open(os.path.join(config.DATA_DIR, "state.json"), "w"))
    runmod.main()
    alerts = json.load(open(os.path.join(config.DATA_DIR, "alerts.json")))
    n = [a for a in alerts if a["kind"] == "noticia"]
    assert n and n[0]["direction"] == "bajista" and n[0]["by"] == "reglas-ia"
    health = json.load(open(os.path.join(config.DATA_DIR, "health.json")))
    assert health["llm"].startswith("respaldo") and health["fails"]["precios"] == 0
    market = json.load(open(os.path.join(config.DATA_DIR, "market.json")))
    assert market["price_status"]["BTC"]["ok"] is False and market["price_status"]["XAUUSD"]["ok"] is True
    assert pushed  # hubo al menos un push


def test_all_prices_failing_counts_as_failure(monkeypatch):
    monkeypatch.setattr(runmod.notify, "push", lambda *a, **k: True)
    monkeypatch.setattr(runmod.sources, "snapshot", lambda a: (_ for _ in ()).throw(RuntimeError("caído")))
    monkeypatch.setattr(runmod.sources, "fetch_news", lambda: ([], 1, 1))
    monkeypatch.setattr(runmod.sources, "fetch_calendar", lambda: [])
    monkeypatch.setattr(runmod.sources, "fetch_fear_greed", lambda: {"value": 1, "label": "x"})
    runmod.main()
    h = json.load(open(os.path.join(config.DATA_DIR, "health.json")))
    assert h["fails"]["precios"] >= 1 and "precios" in h["errors"]
