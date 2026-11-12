"""Posicionamiento y régimen de mercado, 100 % gratis. Solo contexto: NO modifica el sesgo ni el plan.

· DXY, rendimiento 10Y y VIX (Yahoo Finance) -> régimen risk-on/off y viento macro por activo.
· COT de la CFTC (informe Legacy, especuladores no comerciales) -> posicionamiento neto semanal y percentil 52 sem.
· BTC: funding y open interest de perpetuos (OKX; Deribit de respaldo).
Cada fuente falla por separado y la app/resumen muestran "no disponible"; nada de esto detiene al agente.
"""
from datetime import date, timedelta

from . import config, sources

MACRO = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "VIX": "^VIX"}

# código CFTC (Legacy, solo futuros) -> (activo, texto que debe contener el nombre del mercado; control de sanidad)
COT_CONTRACTS = {
    "088691": ("XAUUSD", "GOLD"),
    "099741": ("EURUSD", "EURO FX"),
    "209742": ("NASDAQ", "NASDAQ"),
    "13874A": ("SP500", "S&P"),
    "133741": ("BTC", "BITCOIN"),
}
COT_EXTREME = 90          # percentil >= 90 (o <= 10) = posicionamiento extremo
COT_MIN_WEEKS = 20        # menos semanas de historia: no se calcula percentil
FUND_HIGH, FUND_LOW = 0.0003, -0.0001   # funding por 8 h (0.03 % / -0.01 %)


# ---------------- DXY / 10Y / VIX ----------------
def macro_snapshot():
    """{DXY|US10Y|VIX: {price, chg_day, chg_5d}}. El 10Y va en % con cambios en puntos básicos (pb); el resto en %."""
    out = {}
    for k, sym in MACRO.items():
        try:
            bars = sources.yahoo_candles(sym, "1mo", "1d")
            if len(bars) < 7:
                continue
            last, prev, p5 = bars[-1]["c"], bars[-2]["c"], bars[-6]["c"]
            if k == "US10Y":
                if last > 20:  # algunas versiones de Yahoo devuelven el rendimiento x10
                    last, prev, p5 = last / 10, prev / 10, p5 / 10
                out[k] = {"price": round(last, 3), "chg_day": round((last - prev) * 100, 1),
                          "chg_5d": round((last - p5) * 100, 1), "unit": "pb", "ts": bars[-1]["t"]}
            else:
                out[k] = {"price": round(last, 3), "chg_day": round((last / prev - 1) * 100, 2),
                          "chg_5d": round((last / p5 - 1) * 100, 2), "unit": "%", "ts": bars[-1]["t"]}
        except Exception as e:  # una serie caída no invalida las demás
            sources.log.warning("macro %s: %s", k, e)
    return out


def regime(m):
    """'risk-off' | 'risk-on' | 'mixto' según VIX (nivel y cambio diario). None si no hay VIX."""
    v = (m or {}).get("VIX")
    if not v:
        return None
    pts = 0
    if v["price"] >= 22 or v["chg_day"] >= 10:
        pts -= 2
    elif v["price"] < 16:
        pts += 1
    if v["chg_day"] <= -8:
        pts += 1
    return "risk-off" if pts <= -2 else "risk-on" if pts >= 1 else "mixto"


def macro_hints(m):
    """{activo: (sesgo, motivo)}: viento macro por activo (dólar, rendimientos, VIX). Orientativo."""
    m = m or {}
    d, y, reg = m.get("DXY"), m.get("US10Y"), regime(m)
    dxy = 0 if not d else (-1 if d["chg_day"] >= 0.3 else 1 if d["chg_day"] <= -0.3 else 0)   # dólar fuerte = -1
    yld = 0 if not y else (-1 if y["chg_day"] >= 5 else 1 if y["chg_day"] <= -5 else 0)       # rendimientos subiendo = -1
    risk = {"risk-on": 1, "risk-off": -1}.get(reg, 0)
    why = []
    if dxy:
        why.append(f"DXY {d['chg_day']:+.2f}%")
    if yld:
        why.append(f"10Y {y['chg_day']:+.0f}pb")
    if risk:
        why.append(reg)
    txt = ", ".join(why) or "sin movimiento relevante"

    def sign(x):
        return "alcista" if x >= 1 else "bajista" if x <= -1 else "neutral"
    return {"XAUUSD": (sign(dxy + yld), txt), "EURUSD": (sign(dxy), txt),
            "NASDAQ": (sign(risk + yld), txt), "SP500": (sign(risk), txt), "BTC": (sign(risk + dxy), txt)}


def macro_line(m):
    parts = []
    for k, n in (("VIX", "VIX"), ("DXY", "DXY"), ("US10Y", "10Y")):
        x = (m or {}).get(k)
        if x:
            ch = f"{x['chg_day']:+.1f}pb" if x["unit"] == "pb" else f"{x['chg_day']:+.2f}%"
            parts.append(f"{n} {x['price']:g} ({ch})")
    return " · ".join(parts)


# ---------------- COT (CFTC) ----------------
def cot_metrics(rows):
    """Filas del informe Legacy (Socrata) -> {activo: métricas}, más los activos sin datos válidos."""
    by = {}
    for r in rows:
        code = str(r.get("cftc_contract_market_code", "")).strip()
        if code not in COT_CONTRACTS:
            continue
        asset, kw = COT_CONTRACTS[code]
        if kw.lower() not in str(r.get("market_and_exchange_names", "")).lower():
            continue  # el código no corresponde al mercado esperado: se descarta antes que mostrar datos ajenos
        try:
            lg, sh, oi = (float(r[k]) for k in ("noncomm_positions_long_all", "noncomm_positions_short_all",
                                                "open_interest_all"))
            d = str(r.get("report_date_as_yyyy_mm_dd") or r.get("report_date"))[:10]
            date.fromisoformat(d)
        except (KeyError, ValueError, TypeError):
            continue
        if oi > 0:
            by.setdefault(asset, {})[d] = {"d": d, "net": lg - sh, "pct": (lg - sh) / oi * 100}
    out = {}
    for a, byd in by.items():
        rows_ = sorted(byd.values(), key=lambda x: x["d"], reverse=True)
        cur, prev = rows_[0], rows_[1] if len(rows_) > 1 else None
        hist = [x["pct"] for x in rows_[:52]]
        pc = round(100 * sum(1 for v in hist if v <= cur["pct"]) / len(hist)) if len(hist) >= COT_MIN_WEEKS else None
        read = ("largos extremos: riesgo de liquidación" if pc is not None and pc >= COT_EXTREME
                else "cortos extremos: riesgo de squeeze" if pc is not None and pc <= 100 - COT_EXTREME else "")
        out[a] = {"date": cur["d"], "net": int(cur["net"]), "net_pct_oi": round(cur["pct"], 1),
                  "d_net": int(cur["net"] - prev["net"]) if prev else None, "pctile": pc, "weeks": len(hist),
                  "read": read}
    return {"assets": out, "missing": [a for a, _ in COT_CONTRACTS.values() if a not in out]}


def fetch_cot():
    codes = ",".join(f"'{c}'" for c in COT_CONTRACTS)
    since = (date.today() - timedelta(days=400)).isoformat()
    rows = sources.get(f"https://publicreporting.cftc.gov/resource/{config.COT_DATASET}.json", timeout=30, params={
        "$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd > '{since}'",
        "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 600}).json()
    if not isinstance(rows, list):
        raise RuntimeError(f"respuesta inesperada de la CFTC: {str(rows)[:80]}")
    res = cot_metrics(rows)
    if not res["assets"]:
        raise RuntimeError("la CFTC no devolvió filas válidas (¿cambió el dataset o los campos?)")
    return res


# ---------------- BTC: funding y open interest ----------------
def _okx():
    f = sources.get("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP").json()
    o = sources.get("https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP").json()
    if f.get("code") != "0" or o.get("code") != "0":
        raise RuntimeError(f.get("msg") or o.get("msg") or "error OKX")
    fund, oi = float(f["data"][0]["fundingRate"]), o["data"][0]
    return {"funding_8h": fund, "oi_usd": float(oi["oiUsd"]) if oi.get("oiUsd") else None}


def _deribit():
    r = sources.get("https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL").json()["result"]
    return {"funding_8h": float(r["funding_8h"]), "oi_usd": float(r["open_interest"])}


def fetch_deriv():
    errs = []
    for name, fn in (("okx", _okx), ("deribit", _deribit)):
        try:
            d = fn()
            d["source"] = name
            return d
        except Exception as e:
            errs.append(f"{name}: {str(e)[:70]}")
    raise RuntimeError("; ".join(errs))


def deriv_read(d):
    f = d["funding_8h"]
    return ("funding muy alto: longs apalancados, riesgo de flush" if f >= FUND_HIGH
            else "funding negativo: shorts apalancados, riesgo de squeeze" if f <= FUND_LOW else "funding normal")


def oi_change_24h(hist, now, oi):
    """% de cambio del OI respecto a la muestra más cercana a 24 h atrás (entre 20 y 28 h). None si no hay."""
    c = [(abs(now - t - 86400), v) for t, v in hist if 20 * 3600 <= now - t <= 28 * 3600 and v]
    if not c or not oi:
        return None
    return round((oi / min(c)[1] - 1) * 100, 1)


# ---------------- texto ----------------
def cot_line(a, c):
    pc = f" · p{c['pctile']} 52sem" if c["pctile"] is not None else ""
    dn = f" · Δsem {c['d_net']:+,}" if c["d_net"] is not None else ""
    return f"{a}: neto {c['net_pct_oi']:+.1f}% del OI{pc}{dn}" + (f" · {c['read']}" if c["read"] else "")


def lines(market, now, full=True):
    """Líneas de contexto para el resumen. full=False: solo lo notable (sin IA). Ignora datos muy viejos."""
    L = []
    mac = market.get("macro")
    if mac and now - mac["ts"] < 6 * 3600:
        if full or mac.get("regime") in ("risk-on", "risk-off"):
            L.append(f"RÉGIMEN {mac.get('regime') or 'n/d'}: {macro_line(mac['data'])}")
        if full:
            L.append("MACRO por activo: " + " | ".join(f"{a} {s} ({w})" for a, (s, w) in mac["hints"].items()))
    cot = market.get("cot")
    if cot and now - cot["ts"] < 12 * 86400:
        items = [(a, c) for a, c in cot["assets"].items() if full or c["read"]]
        if items:
            L.append(f"COT (informe al {max(c['date'] for _, c in items)}, especuladores):")
            L += [f"- {cot_line(a, c)}" for a, c in items]
    dv = market.get("deriv")
    if dv and now - dv["ts"] < 6 * 3600 and (full or "normal" not in dv["read"]):
        oi = f" · OI ${dv['oi_usd'] / 1e9:.1f}B" if dv.get("oi_usd") else ""
        ch = f" ({dv['oi_chg_24h']:+.1f}% 24h)" if dv.get("oi_chg_24h") is not None else ""
        L.append(f"BTC derivados: funding {dv['funding_8h'] * 100:+.3f}%/8h{oi}{ch} · {dv['read']}")
    return L
