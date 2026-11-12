"""Plan de operación (entrada/SL/TP 1:2) de cada sesgo del resumen y verificación de su cumplimiento.

Plan (determinístico, sin IA), solo para sesgos alcista/bajista:
  entrada = precio al enviar el resumen.
  SL = detrás del nivel estructural más cercano contrario al sesgo (+ colchón de 0.25 ATR15), si queda entre
       1.5 y 8 ATR15 de la entrada; si no, 4 ATR15.
  TP = entrada ± 2 × riesgo (ratio 1:2).
Verificación al terminar la killzone, con velas de 15m:
  acierto = toca el TP antes que el SL (+2R) · fallo = toca el SL antes (-1R), o ambos en la misma vela
  sin_movimiento = no toca ninguno; cuenta su retorno al cierre en R.
Con 1:2 el punto de equilibrio es 33.3 % de aciertos (sobre acierto+fallo); un paseo aleatorio también da ≈ 33 %.
"""
import math
from datetime import date

VERSION = 2
RR = 2.0
SL_BUFFER = 0.25
ATR_MIN, ATR_MAX, ATR_FALLBACK = 1.5, 8.0, 4.0
MIN_BARS_FRAC = 0.5      # fracción mínima de velas esperadas para dar la medición por válida
GRACE_S = 900            # espera tras el horizonte para que cierre la última vela
EXPIRE_H = 36            # sin velas suficientes tras este plazo -> "sin_datos"
MAX_OPEN, MAX_DONE = 60, 400
MIN_N = 30               # por debajo, la muestra es pequeña
LIMITS = {"day": 14, "week": 12, "month": 12, "year": 5}
BREAK_EVEN = round(100 / (1 + RR), 1)


def new():
    return {"v": VERSION, "open": [], "done": [], "totals": {}, "days": {}, "legacy": []}


def migrate(sc):
    """v1 (1:1 con 1R = 4×ATR15) -> v2. Los abiertos pasan a plan 1:2 con SL = 4×ATR15; lo ya medido
    con la regla anterior no es comparable: se archiva en 'legacy' y no cuenta."""
    if not sc or sc.get("v") == VERSION:
        out = sc or new()
        for k, d in new().items():
            out.setdefault(k, d)
        return out
    out = new()
    for r in sc.get("open", []):
        sgn = 1 if r["dir"] == "alcista" else -1
        risk = r["r"]
        out["open"].append({
            "id": r["id"], "ts": r["ts"], "asset": r["asset"], "dir": r["dir"], "label": r["label"],
            "slot": r["slot"], "entry": r["entry"], "risk": risk, "sl": round(r["entry"] - sgn * risk, 5),
            "tp": round(r["entry"] + sgn * RR * risk, 5), "basis": "ATR", "rr": RR,
            "bar_ts": r["bar_ts"], "hz": r["hz"]})
    out["legacy"] = sc.get("done", [])[:50]
    return out


def horizon_h(slot_hour, kz_end):
    """Horas desde el aviso hasta el fin de la killzone (horas NY)."""
    return ((kz_end - slot_hour) % 24) or 24


def plan(direction, entry, atr15, levels=()):
    """{entry, sl, tp, risk, basis, rr} o None si no hay sesgo direccional o faltan datos."""
    if direction not in ("alcista", "bajista") or not entry or not atr15 or atr15 <= 0:
        return None
    sgn = 1 if direction == "alcista" else -1
    sl, basis = None, "ATR"
    near = sorted((l for l in levels if (l < entry if sgn > 0 else l > entry)), key=lambda l: abs(entry - l))
    for lvl in near:
        cand = lvl - sgn * SL_BUFFER * atr15
        if ATR_MIN * atr15 <= abs(entry - cand) <= ATR_MAX * atr15:
            sl, basis = cand, "nivel"
            break
    if sl is None:
        sl = entry - sgn * ATR_FALLBACK * atr15
    risk = abs(entry - sl)
    return {"entry": round(entry, 5), "sl": round(sl, 5), "tp": round(entry + sgn * RR * risk, 5),
            "risk": round(risk, 5), "basis": basis, "rr": RR}


def add(sc, ts, asset, direction, label, slot, pl, bar_ts, hz):
    """Registra un plan. Ignora planes vacíos y duplicados."""
    if not pl:
        return False
    rid = f"{asset}-{int(ts)}"
    if any(r["id"] == rid for r in sc["open"]):
        return False
    sc["open"].append({"id": rid, "ts": int(ts), "asset": asset, "dir": direction, "label": label,
                       "slot": slot, **pl, "bar_ts": bar_ts, "hz": hz})
    sc["open"] = sc["open"][-MAX_OPEN:]
    return True


def due(rec, now):
    return now >= rec["ts"] + rec["hz"] * 3600 + GRACE_S


def evaluate(rec, bars):
    """Resultado de un plan con las velas 15m disponibles, o None si faltan velas."""
    end = rec["ts"] + rec["hz"] * 3600
    fwd = [b for b in bars if rec["bar_ts"] < b["t"] < end]
    if len(fwd) < max(2, int(rec["hz"] * 4 * MIN_BARS_FRAC)):
        return None
    sgn = 1 if rec["dir"] == "alcista" else -1
    e, sl, tp, R = rec["entry"], rec["sl"], rec["tp"], rec["risk"]
    outcome, amb, exit_ts, mfe, mae = None, False, None, 0.0, 0.0
    for b in fwd:
        hit_tp = b["h"] >= tp if sgn > 0 else b["l"] <= tp
        hit_sl = b["l"] <= sl if sgn > 0 else b["h"] >= sl
        mfe = max(mfe, ((b["h"] - e) if sgn > 0 else (e - b["l"])) / R)
        mae = max(mae, ((e - b["l"]) if sgn > 0 else (b["h"] - e)) / R)
        if hit_tp or hit_sl:
            outcome, amb, exit_ts = ("acierto" if hit_tp and not hit_sl else "fallo"), hit_tp and hit_sl, b["t"]
            break
    close_r = sgn * (fwd[-1]["c"] - e) / R
    if outcome is None:
        outcome, r = "sin_movimiento", max(-1.0, min(rec.get("rr", RR), close_r))
    else:
        r = rec.get("rr", RR) if outcome == "acierto" else -1.0
    return {"outcome": outcome, "r": round(r, 3), "mfe": round(mfe, 2), "mae": round(mae, 2),
            "close_r": round(close_r, 2), "amb": amb, "exit_ts": exit_ts}


def _bump(bucket, key, res):
    t = bucket.setdefault(key, {"n": 0, "a": 0, "f": 0, "s": 0, "rsum": 0.0})
    t["n"] += 1
    t[{"acierto": "a", "fallo": "f"}.get(res["outcome"], "s")] += 1
    t["rsum"] = round(t["rsum"] + res["r"], 3)


def close(sc, rec, res, day):
    """Mueve un plan de abiertos a cerrados y actualiza acumulados y día (salvo sin_datos). day = 'AAAA-MM-DD'."""
    sc["open"] = [r for r in sc["open"] if r["id"] != rec["id"]]
    sc["done"].insert(0, {**rec, **res})
    sc["done"] = sc["done"][:MAX_DONE]
    if res["outcome"] != "sin_datos":
        keys = ["ALL", f"asset:{rec['asset']}", f"label:{rec['label']}", f"slot:{rec['slot']}"]
        keys += [f"{k}:{rec[k]}" for k in ("regime", "macro") if rec.get(k)]
        for key in keys:
            _bump(sc["totals"], key, res)
        _bump(sc["days"].setdefault(day, {}), "ALL", res)


def wilson(a, n, z=1.96):
    """Intervalo de confianza 95 % de una proporción (aciertos a de n)."""
    if n <= 0:
        return None, None
    p = a / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1)


def stat(t):
    dec = t["a"] + t["f"]
    lo, hi = wilson(t["a"], dec)
    return {"n": t["n"], "a": t["a"], "f": t["f"], "s": t["s"],
            "hit": round(t["a"] / dec * 100, 1) if dec else None, "lo": lo, "hi": hi,
            "avg_r": round(t["rsum"] / t["n"], 3) if t["n"] else None, "small": dec < MIN_N}


def summary(totals):
    return {k: stat(t) for k, t in totals.items()}


def _keys(day):
    y, w, _ = date.fromisoformat(day).isocalendar()
    return {"day": day, "week": f"{y}-W{w:02d}", "month": day[:7], "year": day[:4]}


def periods(days, today):
    """% de aciertos por día, semana (ISO), mes y año, el período en curso primero.
    Un día es la fecha local en que se emitió el sesgo."""
    agg = {p: {} for p in LIMITS}
    for day, d in days.items():
        t = d.get("ALL")
        if not t:
            continue
        for p, k in _keys(day).items():
            a = agg[p].setdefault(k, {"n": 0, "a": 0, "f": 0, "s": 0, "rsum": 0.0})
            for f in ("n", "a", "f", "s"):
                a[f] += t[f]
            a["rsum"] = round(a["rsum"] + t["rsum"], 3)
    out, cur = {}, _keys(today)
    for p, m in agg.items():
        keys = sorted(m, reverse=True)
        if cur[p] not in m:
            keys = [cur[p]] + keys
        zero = {"n": 0, "a": 0, "f": 0, "s": 0, "rsum": 0.0}
        out[p] = [{"k": k, **stat(m.get(k, zero))} for k in keys[:LIMITS[p]]]
    return out
