"""Estructura de mercado en velas 4h, sin IA: swings, cuñas (ruptura y reteste), zonas de
soporte/resistencia, objetivos y confluencia con las noticias. Funciones puras."""

K = 3  # velas a cada lado para confirmar un swing


def resample(bars, sec=14400):
    """Velas ordenadas -> velas de `sec` segundos."""
    out, key = [], None
    for b in bars:
        k = int(b["t"] // sec)
        if k != key:
            key = k
            out.append({"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]})
        else:
            cur = out[-1]
            cur["h"], cur["l"], cur["c"] = max(cur["h"], b["h"]), min(cur["l"], b["l"]), b["c"]
    return out


def pivots(bars, k=K):
    """Swings confirmados: ([(i, alto)], [(i, bajo)])."""
    hi, lo = [], []
    for i in range(k, len(bars) - k):
        left, right = bars[i - k:i], bars[i + 1:i + k + 1]
        if bars[i]["h"] > max(b["h"] for b in left) and bars[i]["h"] >= max(b["h"] for b in right):
            hi.append((i, bars[i]["h"]))
        if bars[i]["l"] < min(b["l"] for b in left) and bars[i]["l"] <= min(b["l"] for b in right):
            lo.append((i, bars[i]["l"]))
    return hi, lo


def _fit(pts):
    """Recta y = a*x + b por mínimos cuadrados."""
    n = len(pts)
    sx, sy = sum(x for x, _ in pts), sum(y for _, y in pts)
    sxx, sxy = sum(x * x for x, _ in pts), sum(x * y for x, y in pts)
    den = n * sxx - sx * sx
    a = (n * sxy - sx * sy) / den if den else 0.0
    return a, (sy - a * sx) / n


def _atr(bars, n=14):
    seg = bars[-n - 1:]
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(seg, seg[1:])]
    return sum(trs) / len(trs)


def _zones(levels, tol):
    """Une niveles a menos de `tol` entre sí (promedio)."""
    out = []
    for x in sorted(levels):
        if out and x - out[-1][-1] <= tol:
            out[-1].append(x)
        else:
            out.append([x])
    return [sum(g) / len(g) for g in out]


def _wedge(bars, hi, lo, price, atr, strict=False):
    """Cuña descendente/ascendente con los 3 últimos swings dados. (sesgo, texto, línea, objetivo) o None.
    strict: solo acepta cuñas ya rotas (para ajustes con swings anteriores, evita cuñas viejas)."""
    if len(hi) < 3 or len(lo) < 3:
        return None
    n, last = len(bars), len(bars) - 1
    (ah, bh), (al, bl) = _fit(hi[-3:]), _fit(lo[-3:])
    up, dn = (lambda x: ah * x + bh), (lambda x: al * x + bl)
    first = min(hi[-3][0], lo[-3][0])
    h0 = up(first) - dn(first)  # altura de la cuña al inicio
    if h0 <= 0:
        return None
    recent = range(max(first, n - 24), n)  # ruptura en los últimos ~4 días
    if ah < 0 and al < 0 and ah < al:  # cuña descendente (patrón de giro alcista)
        line = up(last)
        if price > line - 0.5 * atr and any(bars[i]["c"] > up(i) for i in recent):
            return ("alcista", "cuña descendente rota al alza" + (", en reteste" if abs(price - line) <= atr else ""),
                    line, line + h0)
        if strict:
            return None
        if price < dn(last):
            return "bajista", "cuña descendente fallida (rota a la baja)", line, None
        return "neutral", "cuña descendente sin romper (sesgo alcista si rompe arriba)", line, None
    if ah > 0 and al > 0 and al > ah:  # cuña ascendente (patrón de giro bajista)
        line = dn(last)
        if price < line + 0.5 * atr and any(bars[i]["c"] < dn(i) for i in recent):
            return ("bajista", "cuña ascendente rota a la baja" + (", en reteste" if abs(price - line) <= atr else ""),
                    line, line - h0)
        if strict:
            return None
        if price > up(last):
            return "alcista", "cuña ascendente fallida (rota al alza)", line, None
        return "neutral", "cuña ascendente sin romper (sesgo bajista si rompe abajo)", line, None
    return None


def analyze(bars, price=None):
    """bars: velas 4h. Devuelve dict con tendencia, patrón, sesgo técnico, zonas y objetivos; None si faltan datos."""
    if len(bars) < 40:
        return None
    n, price = len(bars), price or bars[-1]["c"]
    atr = _atr(bars)
    hi, lo = pivots(bars)
    if len(hi) < 3 or len(lo) < 3:
        return None
    if hi[-1][1] > hi[-2][1] and lo[-1][1] > lo[-2][1]:
        trend = "alcista"
    elif hi[-1][1] < hi[-2][1] and lo[-1][1] < lo[-2][1]:
        trend = "bajista"
    else:
        trend = "rango"
    bias, pattern, line, wedge_target = ("neutral" if trend == "rango" else trend), "", None, None

    # la cuña se ajusta con los swings previos a la ruptura: se prueba descartando los últimos 0-4 swings
    wedge = None
    for drop in range(5):
        for d1 in range(drop + 1):
            wedge = _wedge(bars, hi[:len(hi) - d1], lo[:len(lo) - (drop - d1)], price, atr, strict=drop > 0)
            if wedge:
                break
        if wedge:
            break
    if wedge:
        bias, pattern, line, wedge_target = wedge

    tol = 0.5 * atr
    res = [z for z in _zones([p for _, p in hi], tol) if z > price][:3]
    sup = [z for z in _zones([p for _, p in lo], tol) if z < price][::-1][:2]
    if wedge_target is not None and ((bias == "alcista" and wedge_target <= price)
                                     or (bias == "bajista" and wedge_target >= price)):
        wedge_target = None  # el objetivo ya se cumplió
    return {"trend": trend, "bias": bias, "pattern": pattern,
            "line": round(line, 5) if line is not None else None,
            "res": [round(x, 5) for x in res], "sup": [round(x, 5) for x in sup],
            "wedge_target": round(wedge_target, 5) if wedge_target is not None else None,
            "atr": round(atr, 5)}


# ---------------- noticias + técnica ----------------
def news_bias(alerts, asset):
    """Sesgo neto de las noticias clasificadas (severidad con signo). Umbral ±3."""
    net = sum(a["severity"] * {"alcista": 1, "bajista": -1}.get(a["direction"], 0)
              for a in alerts if asset in a["asset"].split(","))
    return ("alcista" if net >= 3 else "bajista" if net <= -3 else "neutral"), net


def confluence(tech, news):
    """(etiqueta, sesgo final). En conflicto el sesgo final es neutral."""
    if tech == news:
        return ("a favor", tech) if tech != "neutral" else ("sin señal", "neutral")
    if news == "neutral":
        return "solo técnica", tech
    if tech == "neutral":
        return "solo noticias", news
    return "en conflicto", "neutral"
