"""Señales técnicas determinísticas (sin IA): impulsos, volatilidad, niveles y movimiento diario."""
import config


def _dir(x):
    return "alcista" if x > 0 else "bajista"


def check_signals(asset, s):
    if not s or s.get("stale"):
        return []
    out, p, atr = [], s["price"], s["atr15"] or 0
    if atr > 0:
        move = s["d_1h"]
        k = abs(move) / atr
        if k >= 3.0:
            out.append({
                "asset": asset, "kind": "impulso_1h", "direction": _dir(move),
                "severity": 4 if k >= 4.5 else 3,
                "title": f"{asset}: impulso {_dir(move)} en 1h ({s['chg_1h']:+.2f}%)",
                "body": f"Movimiento = {k:.1f}× ATR(15m). Precio {p}."})
        if s["avg_range"] > 0 and s["last_range"] >= 3 * s["avg_range"]:
            out.append({
                "asset": asset, "kind": "volatilidad", "direction": "neutral", "severity": 3,
                "title": f"{asset}: pico de volatilidad",
                "body": f"Vela 15m {s['last_range'] / s['avg_range']:.1f}× el rango medio. Precio {p}."})
        for name, lvl in (("PDH", s["pdh"]), ("PDL", s["pdl"])):
            if abs(p - lvl) <= 0.25 * atr:
                out.append({
                    "asset": asset, "kind": f"nivel_{name.lower()}", "direction": "neutral", "severity": 2,
                    "title": f"{asset}: cerca de {name} ({lvl:g})", "body": f"Precio {p}."})
    th = config.ASSETS[asset]["daily_pct"]
    d = s["chg_day"]
    if abs(d) >= th:
        out.append({
            "asset": asset, "kind": "mov_diario", "direction": _dir(d),
            "severity": 4 if abs(d) >= 1.5 * th else 3,
            "title": f"{asset}: {d:+.2f}% vs cierre previo",
            "body": f"Supera el umbral diario de {th}%. Precio {p}."})
    return out
