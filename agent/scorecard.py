"""Registro y medición de aciertos de los sesgos del resumen (determinístico, sin IA).

Cada sesgo direccional (alcista/bajista) emitido en un resumen se guarda con precio y ATR de entrada.
Al terminar la killzone se mide con velas de 15m:
  1R = R_ATR × ATR(15m) (≈ ATR de 1h).
  acierto = el precio toca +1R a favor antes que -1R en contra · fallo = al revés (o ambos en la misma vela).
  sin_movimiento = no toca ninguno; cuenta con su retorno al cierre del horizonte, recortado a ±1R.
Los neutrales no se miden. Referencia del azar: ≈ 50 % de aciertos.
"""
import math

R_ATR = 4
MIN_BARS_FRAC = 0.5      # fracción mínima de velas esperadas para dar la medición por válida
GRACE_S = 900            # espera tras el horizonte para que cierre la última vela
EXPIRE_H = 36            # sin velas suficientes tras este plazo -> "sin_datos"
MAX_OPEN, MAX_DONE = 60, 400
MIN_N = 30               # por debajo, la muestra es pequeña


def new():
    return {"open": [], "done": [], "totals": {}}


def horizon_h(slot_hour, kz_end):
    """Horas desde el aviso hasta el fin de la killzone (horas NY)."""
    return ((kz_end - slot_hour) % 24) or 24


def add(sc, ts, asset, direction, label, slot, entry, atr15, bar_ts, hz):
    """Registra un sesgo. Ignora neutrales, ATR inválido y duplicados."""
    if direction not in ("alcista", "bajista") or not atr15 or atr15 <= 0:
        return False
    rid = f"{asset}-{int(ts)}"
    if any(r["id"] == rid for r in sc["open"]):
        return False
    sc["open"].append({"id": rid, "ts": int(ts), "asset": asset, "dir": direction, "label": label,
                       "slot": slot, "entry": entry, "r": round(R_ATR * atr15, 6), "bar_ts": bar_ts, "hz": hz})
    sc["open"] = sc["open"][-MAX_OPEN:]
    return True


def due(rec, now):
    return now >= rec["ts"] + rec["hz"] * 3600 + GRACE_S


def evaluate(rec, bars):
    """Resultado de un registro con las velas 15m disponibles, o None si faltan velas."""
    end = rec["ts"] + rec["hz"] * 3600
    fwd = [b for b in bars if rec["bar_ts"] < b["t"] < end]
    if len(fwd) < max(2, int(rec["hz"] * 4 * MIN_BARS_FRAC)):
        return None
    sgn = 1 if rec["dir"] == "alcista" else -1
    e, R = rec["entry"], rec["r"]
    outcome, amb, mfe, mae = None, False, 0.0, 0.0
    for b in fwd:
        fav = ((b["h"] - e) if sgn > 0 else (e - b["l"])) / R
        adv = ((e - b["l"]) if sgn > 0 else (b["h"] - e)) / R
        mfe, mae = max(mfe, fav), max(mae, adv)
        if outcome is None:
            if fav >= 1 and adv >= 1:
                outcome, amb = "fallo", True
            elif fav >= 1:
                outcome = "acierto"
            elif adv >= 1:
                outcome = "fallo"
    close_r = sgn * (fwd[-1]["c"] - e) / R
    if outcome is None:
        outcome, r = "sin_movimiento", max(-1.0, min(1.0, close_r))
    else:
        r = 1.0 if outcome == "acierto" else -1.0
    return {"outcome": outcome, "r": round(r, 3), "mfe": round(mfe, 2), "mae": round(mae, 2),
            "close_r": round(close_r, 2), "amb": amb}


def _bump(totals, key, res):
    t = totals.setdefault(key, {"n": 0, "a": 0, "f": 0, "s": 0, "rsum": 0.0})
    t["n"] += 1
    t[{"acierto": "a", "fallo": "f"}.get(res["outcome"], "s")] += 1
    t["rsum"] = round(t["rsum"] + res["r"], 3)


def close(sc, rec, res, now=None):
    """Mueve un registro de abiertos a cerrados y actualiza los acumulados (salvo sin_datos)."""
    sc["open"] = [r for r in sc["open"] if r["id"] != rec["id"]]
    sc["done"].insert(0, {**rec, **res})
    sc["done"] = sc["done"][:MAX_DONE]
    if res["outcome"] != "sin_datos":
        for key in ("ALL", f"asset:{rec['asset']}", f"label:{rec['label']}", f"slot:{rec['slot']}"):
            _bump(sc["totals"], key, res)


def wilson(a, n, z=1.96):
    """Intervalo de confianza 95 % de una proporción (aciertos a de n)."""
    if n <= 0:
        return None, None
    p = a / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1)


def summary(totals):
    out = {}
    for k, t in totals.items():
        dec = t["a"] + t["f"]
        lo, hi = wilson(t["a"], dec)
        out[k] = {"n": t["n"], "a": t["a"], "f": t["f"], "s": t["s"],
                  "hit": round(t["a"] / dec * 100, 1) if dec else None, "lo": lo, "hi": hi,
                  "avg_r": round(t["rsum"] / t["n"], 3) if t["n"] else None, "small": dec < MIN_N}
    return out
