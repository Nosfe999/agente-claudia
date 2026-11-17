import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import pytest  # noqa: E402

from agent import config, positioning, sources  # noqa: E402
from agent import run as runmod  # noqa: E402


def test_rsi_valores_conocidos():
    # ejemplo clásico de Wilder (StockCharts): RSI14 ≈ 70.5 en el cierre 15
    c = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert positioning.rsi(c) == pytest.approx(70.5, abs=0.3)
    assert positioning.rsi(list(range(1, 40))) == 100.0
    assert positioning.rsi(list(range(40, 1, -1))) == 0.0
    assert positioning.rsi([1.0] * 10) is None


def test_zona_con_histeresis():
    z = runmod.dxy_zone
    assert z(75, "", 75, 25) == "ob" and z(74.9, "", 75, 25) == ""
    assert z(25, "", 75, 25) == "os" and z(25.1, "", 75, 25) == ""
    assert z(72, "ob", 75, 25) == "ob"      # aún dentro del margen
    assert z(70, "ob", 75, 25) == ""        # volvió 5 puntos: se rearma
    assert z(28, "os", 75, 25) == "os" and z(30, "os", 75, 25) == ""


def series(kind, now, n=80, sec=3600):
    """Velas 1h cerradas: 'up' sube siempre (RSI 100), 'down' baja siempre (RSI 0), 'flat' alterna (RSI ≈ 50)."""
    out, c = [], 100.0
    for i in range(n):
        c += {"up": 0.1, "down": -0.1, "flat": 0.1 if i % 2 else -0.1}[kind]
        out.append({"t": now - (n - i) * sec - 1, "o": c, "h": c + .05, "l": c - .05, "c": c})
    return out


@pytest.fixture
def ctx(monkeypatch):
    c = runmod.Ctx()
    c.closed = False
    c.market, c.alerts, c.st["dxy_zone"] = {}, [], ""
    sent = []
    monkeypatch.setattr(runmod.notify, "push", lambda *a, **k: sent.append(a) or True)
    c.sent = sent
    return c


def feed(monkeypatch, ctx, kind):
    monkeypatch.setattr(sources, "yahoo_candles", lambda *a, **k: series(kind, ctx.now))


def test_avisa_una_vez_y_se_rearma(monkeypatch, ctx):
    feed(monkeypatch, ctx, "up")
    runmod.dxy_rsi_job(ctx)
    assert len(ctx.sent) == 1 and "SOBRECOMPRA" in ctx.sent[0][0]
    assert ctx.alerts[0]["asset"] == "DXY" and ctx.market["dxy_rsi"]["zone"] == "ob"
    runmod.dxy_rsi_job(ctx)                      # sigue en la zona: no repite
    assert len(ctx.sent) == 1
    feed(monkeypatch, ctx, "flat")
    runmod.dxy_rsi_job(ctx)                      # sale de la zona: sin aviso
    assert len(ctx.sent) == 1 and ctx.st["dxy_zone"] == ""
    feed(monkeypatch, ctx, "down")
    runmod.dxy_rsi_job(ctx)
    assert len(ctx.sent) == 2 and "SOBREVENTA" in ctx.sent[1][0]


def test_mercado_cerrado_apagada_y_fallo(monkeypatch, ctx):
    feed(monkeypatch, ctx, "up")
    ctx.closed = True
    runmod.dxy_rsi_job(ctx)
    assert not ctx.sent and ctx.st["dxy_zone"] == "ob"   # registra la zona pero no avisa
    ctx.st["dxy_zone"], ctx.closed = "", False
    monkeypatch.setattr(config, "DXY_RSI_ALERT", 0)
    runmod.dxy_rsi_job(ctx)
    assert not ctx.sent
    monkeypatch.setattr(config, "DXY_RSI_ALERT", 1)

    def boom(*a, **k):
        raise RuntimeError("yahoo caído")
    monkeypatch.setattr(sources, "yahoo_candles", boom)
    runmod.dxy_rsi_job(ctx)                      # no lanza; deja el error visible
    assert "yahoo caído" in ctx.market["pos_err"]["dxy_rsi"] and not ctx.sent
