import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from agent import backtest, scorecard  # noqa: E402


def walk(n, step, t0, c0=2000.0, sigma=1.0, seed=3):
    rnd, c, out = random.Random(seed), c0, []
    for i in range(n):
        o = c
        c += rnd.gauss(0, sigma)
        out.append({"t": t0 + i * step, "o": o, "h": max(o, c) + 0.3, "l": min(o, c) - 0.3, "c": c})
    return out


def data():
    t15 = 1_760_000_000 - 60 * 96 * 900          # ~60 días de velas 15m contiguas (sin fines de semana)
    intra = walk(60 * 96, 900, t15)
    hourly = walk(24 * 400, 3600, t15 - 340 * 24 * 3600, sigma=2.0, seed=5)
    hourly = [b for b in hourly if b["t"] < intra[-1]["t"]]
    return intra, hourly


def test_simulate_estructura_y_consistencia():
    intra, hourly = data()
    res = backtest.simulate(intra, hourly, days=58)
    assert len(res["long"]) == len(res["short"]) > 50
    assert len(res["random"]) == len(res["tech"])
    for r in res["long"] + res["short"]:
        assert r["outcome"] in ("acierto", "fallo", "sin_movimiento")
    # largo y corto con el mismo plan simétrico no pueden acertar ambos en el mismo aviso salvo ambigüedad
    both = sum(1 for a, b in zip(res["long"], res["short"]) if a["outcome"] == b["outcome"] == "acierto")
    assert both == 0


def test_summarize_y_verdict():
    rows = [{"outcome": "acierto", "r": 2.0}] * 20 + [{"outcome": "fallo", "r": -1.0}] * 10
    st = backtest.summarize({"tech": rows, "random": rows[:15], "long": [], "short": []})
    assert st["tech"]["hit"] == 66.7 and st["tech"]["avg_r"] == 1.0
    small = [{"outcome": "acierto", "r": 2.0}] * 10 + [{"outcome": "fallo", "r": -1.0}] * 5
    assert "pequeña" in backtest.verdict(backtest.summarize({"tech": small, "random": [], "long": [], "short": []}))
    big = [{"outcome": "acierto", "r": 2.0}] * 200 + [{"outcome": "fallo", "r": -1.0}] * 100
    assert "ventaja" in backtest.verdict(backtest.summarize({"tech": big, "random": [], "long": [], "short": []}))
    flat = [{"outcome": "acierto", "r": 2.0}] * 100 + [{"outcome": "fallo", "r": -1.0}] * 200
    assert "Sin evidencia" in backtest.verdict(backtest.summarize({"tech": flat, "random": [], "long": [], "short": []}))
    assert scorecard.BREAK_EVEN == 33.3
