import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
import numpy as np, pandas as pd
import config, storage, signals, sources, analyzer, risk, sessions
from datetime import datetime, timezone

storage.init()

def test_risk():
    r = risk.position_size(1000, 1, 2000, 1990, "XAUUSD")
    assert r["side"] == "largo" and r["lots"] == 0.01 and r["risk_usd"] == 10
    assert r["targets"]["2R"] == 2020
    s = risk.position_size(1000, 1, 1.1000, 1.1010, "EURUSD")
    assert s["side"] == "corto" and s["targets"]["1R"] == 1.099
    for bad in [(0,1,1,2),(1000,0,1,2),(1000,1,1,1)]:
        try: risk.position_size(*bad); assert False
        except ValueError: pass

def test_journal_r():
    storage.journal_add({"asset":"XAUUSD","side":"largo","entry":100,"stop":99,"exit":102})
    storage.journal_add({"asset":"XAUUSD","side":"corto","entry":100,"stop":101,"exit":101})
    st = storage.journal_stats()
    assert st["trades"] == 2 and st["total_r"] == 1.0 and st["win_rate"] == 50.0

def test_dedupe_cooldown():
    items = [{"id":"a"},{"id":"b"}]
    storage.mark_seen(["a"])
    assert [i["id"] for i in storage.unseen(items)] == ["b"]
    assert storage.cooldown_ok("k", 10) is True
    assert storage.cooldown_ok("k", 10) is False

def test_parse_classification():
    items = [{"title":"x"},{"title":"y"}]
    txt = 'Aquí:\n[{"i":0,"assets":["BTC","FOO"],"direction":"bajista","severity":9,"confidence":2,"reason":"r"},{"i":7,"assets":["BTC"]},{"i":1,"assets":[]}]'
    out = analyzer.parse_classification(txt, items)
    assert len(out) == 1 and out[0]["assets"] == ["BTC"] and out[0]["severity"] == 5 and out[0]["confidence"] == 1.0
    assert analyzer.parse_classification("basura", items) == []
    assert analyzer.parse_classification("[{rota", items) == []

def _frames(last_jump=0.0, stale=False):
    n = 200
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("15min") - (pd.Timedelta(hours=10) if stale else pd.Timedelta(0)), periods=n, freq="15min")
    c = 2000 + np.cumsum(np.random.default_rng(1).normal(0, 0.3, n))
    c[-4:] += np.linspace(0, last_jump, 4)
    intra = pd.DataFrame({"Close": c, "High": c + 0.3, "Low": c - 0.3}, index=idx)
    daily = pd.DataFrame({"Close":[1990,2000,2001],"High":[2005,2010,2012],"Low":[1980,1990,1995]})
    return intra, daily

def test_signals():
    s = sources.build_snapshot("XAUUSD", *_frames(last_jump=-15))
    assert s and not s["stale"]
    kinds = {x["kind"]: x for x in signals.check_signals("XAUUSD", s)}
    assert "impulso_1h" in kinds and kinds["impulso_1h"]["direction"] == "bajista"
    calm = sources.build_snapshot("XAUUSD", *_frames(last_jump=0.0))
    assert not any(x["kind"] == "impulso_1h" for x in signals.check_signals("XAUUSD", calm))
    st = sources.build_snapshot("XAUUSD", *_frames(last_jump=-15, stale=True))
    assert st["stale"] and signals.check_signals("XAUUSD", st) == []
    assert sources.build_snapshot("XAUUSD", None, None) is None

def test_parse_feed():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><title>Riot Platforms sells 8,962 BTC - Reuters</title><link>https://e.com/1</link>
    <pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;x&lt;/b&gt; hola</description></item>
    <item><title>Riot Platforms sells 8,962 BTC - CoinDesk</title><link>https://e.com/2</link></item></channel></rss>"""
    it = sources.parse_feed(rss, "BTC")
    assert len(it) == 2 and it[0]["id"] == it[1]["id"] and it[0]["summary"] == "x hola"

def test_sessions():
    s = sessions.status(datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc))  # jueves 10:00 NY
    ny = [x for x in s["sessions"] if x["name"] == "Nueva York"][0]
    assert ny["open"] and s["forex_open"]
    sat = sessions.status(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc))
    assert not sat["forex_open"]

def test_api():
    os.environ["DISABLE_SCHEDULER"] = "1"
    config.API_TOKEN = "secreto"
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/alerts").status_code == 401
        h = {"X-Token": "secreto"}
        assert c.get("/api/alerts", headers=h).status_code == 200
        assert c.get("/api/sessions", headers=h).status_code == 200
        r = c.post("/api/risk", headers=h, json={"balance":1000,"risk_pct":1,"asset":"XAUUSD","entry":2000,"stop":1990})
        assert r.json()["lots"] == 0.01
        assert c.post("/api/risk", headers=h, json={"balance":1000,"risk_pct":1,"asset":"XAUUSD","entry":2000,"stop":2000}).status_code == 400
        assert c.post("/api/journal", headers=h, json={"asset":"ZZZ","side":"largo","entry":1}).status_code == 400
        assert c.get("/").status_code == 200 and c.get("/manifest.webmanifest").status_code == 200
        assert c.get("/sw.js").status_code == 200
