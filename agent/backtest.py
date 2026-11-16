"""Backtest histórico de la señal técnica (estructura 4h) con el mismo plan 1:2 y la misma verificación
que el agente en vivo (`scorecard.plan` / `scorecard.evaluate`).

Qué mide: solo la parte técnica (`structure.analyze`). Las noticias no se pueden reconstruir hacia atrás.
Cada día se simulan los 3 avisos (19:00, 01:00, 06:00 NY); entrada = cierre de la última vela 15m previa.
Comparación: siempre largo, siempre corto y dirección aleatoria sobre los mismos avisos. Si la señal no supera
claramente a esas líneas base ni al punto de equilibrio (33.3 %), no hay evidencia de ventaja.
Límites: Yahoo da ~60 días de velas 15m; las muestras de un mismo día/tendencia están correlacionadas, así que
la muestra efectiva es menor que n; PDH/PDL se aproximan con el rango de las 24 h previas.

Uso: python -m agent.backtest [--days 58] [--assets BTC,XAUUSD]
"""
import argparse
import json
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, scorecard, sources, structure

NY = ZoneInfo(config.MARKET_TZ)
MAX_ENTRY_AGE_S = 5400   # la última vela 15m debe ser reciente (cierre de mercado -> se omite)
H1_WINDOW = 480          # velas 1h usadas para la estructura (≈ 20 días -> 120 velas 4h)


def _atr15(bars):
    seg = bars[-15:]
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(seg, seg[1:])]
    return sum(trs) / len(trs)


def _slots(first_ts, last_ts, days):
    """[(ts, hora_aviso, fin_killzone)] de los avisos simulados dentro de [first_ts, last_ts]."""
    end = datetime.fromtimestamp(last_ts, NY).date()
    start = max(datetime.fromtimestamp(first_ts, NY).date(), end - timedelta(days=days))
    out, d = [], start
    while d <= end:
        for hour, (_, _, _, kz_end) in config.DIGEST_SLOTS.items():
            ts = datetime(d.year, d.month, d.day, hour, tzinfo=NY).timestamp()
            if first_ts <= ts <= last_ts:
                out.append((ts, hour, kz_end))
        d += timedelta(days=1)
    return out


def _levels(st, intra):
    lv = list(st["res"] + st["sup"]) if st else []
    lv += [v for k, v in sources.chart_levels(intra).items() if k in
           ("h24", "l24", "asia_h", "asia_l", "lon_h", "lon_l", "ny_h", "ny_l")]
    prev = intra[-192:-96]
    if prev:
        lv += [max(b["h"] for b in prev), min(b["l"] for b in prev)]
    return lv


def collect(intra, hourly, days=58):
    """Un registro por aviso simulado: {'ts', 'long', 'short', 'st'} (resultado largo/corto con el plan 1:2
    y la lectura técnica `st` de ese momento). Base de `simulate` y de las variantes."""
    out = []
    for ts, hour, kz_end in _slots(intra[0]["t"], intra[-1]["t"], days):
        past = [b for b in intra if b["t"] < ts]
        if len(past) < 200 or ts - past[-1]["t"] > MAX_ENTRY_AGE_S:
            continue
        entry, atr = past[-1]["c"], _atr15(past)
        h1 = [b for b in hourly if b["t"] < ts][-H1_WINDOW:]
        st = structure.analyze(structure.resample(h1), entry) if len(h1) >= 160 else None
        lv = _levels(st, past[-500:])
        hz = scorecard.horizon_h(hour, kz_end)

        def run(direction):
            pl = scorecard.plan(direction, entry, atr, lv)
            if not pl:
                return None
            rec = {**pl, "dir": direction, "ts": int(ts), "hz": hz, "bar_ts": past[-1]["t"]}
            return scorecard.evaluate(rec, intra)

        lo, sh = run("alcista"), run("bajista")
        if lo is None or sh is None:
            continue  # sin velas suficientes tras el aviso (fin de semana, hueco de datos)
        out.append({"ts": ts, "long": lo, "short": sh, "st": st})
    return out


def _pick(rec):
    """Resultado de seguir el sesgo técnico del registro (None si no es direccional)."""
    st = rec["st"]
    if not st or st["bias"] not in ("alcista", "bajista"):
        return None
    return rec["long"] if st["bias"] == "alcista" else rec["short"]


# Variantes fijadas ANTES de mirar resultados (pocas, para limitar el sobreajuste por probar muchas).
VARIANTS = {
    "tech": lambda st: True,                                            # la señal actual
    "cuna_rota": lambda st: "rota" in st["pattern"],                    # solo cuñas ya rotas
    "con_tendencia": lambda st: st["bias"] == st["trend"],              # sesgo igual a la tendencia 4h
    "cuna_y_tendencia": lambda st: "rota" in st["pattern"] and st["bias"] == st["trend"],
}


def simulate(intra, hourly, days=58, seed=7):
    """Devuelve {'tech': [res], 'long': [res], 'short': [res], 'random': [res]} con los resultados
    de `scorecard.evaluate`. 'tech' solo cuenta avisos con sesgo técnico direccional; las líneas base
    usan todos los avisos con datos (random: misma cantidad que tech, mismos avisos)."""
    return from_slots(collect(intra, hourly, days), seed)


def from_slots(slots, seed=7):
    rng = random.Random(seed)
    out = {"tech": [], "long": [], "short": [], "random": []}
    for r in slots:
        out["long"].append(r["long"])
        out["short"].append(r["short"])
        if _pick(r):
            out["tech"].append(_pick(r))
            out["random"].append(rng.choice((r["long"], r["short"])))
    return out


def variant_report(slots):
    """{variante: {'all': stat, 'h1': stat, 'h2': stat}}. h1/h2 = primera/segunda mitad en el tiempo:
    una variante solo es creíble si acierta por encima del equilibrio en las dos y no solo en el total."""
    slots = sorted(slots, key=lambda r: r["ts"])
    mid = len(slots) // 2
    out = {}
    for name, ok in VARIANTS.items():
        def rows(sub):
            return [_pick(r) for r in sub if _pick(r) and ok(r["st"])]
        out[name] = {k: summarize({"x": rows(sub)})["x"]
                     for k, sub in (("all", slots), ("h1", slots[:mid]), ("h2", slots[mid:]))}
    return out


def fmt_variants(rep):
    rows = ["variante            n   acierto%  IC95%         R medio | 1ª mitad  2ª mitad"]
    for name, d in rep.items():
        a, h1, h2 = d["all"], d["h1"], d["h2"]
        if a["hit"] is None:
            rows.append(f"{name:<16} {0:>4}   -")
            continue
        f = lambda s: "  n/d " if s["hit"] is None else f"{s['hit']:>5}%"
        rows.append(f"{name:<16} {a['a'] + a['f']:>4}   {a['hit']:>6}   {a['lo']:>5}-{a['hi']:<5}   "
                    f"{a['avg_r']:>+6.2f} | {f(h1)}   {f(h2)}")
    return "\n".join(rows)


def variant_verdict(d):
    be = scorecard.BREAK_EVEN
    a, h1, h2 = d["all"], d["h1"], d["h2"]
    if a["hit"] is None or a["small"]:
        return "muestra pequeña"
    both = all(x["hit"] is not None and x["hit"] > be for x in (h1, h2))
    if a["lo"] > be and both:
        return "posible ventaja (confirmar con datos nuevos)"
    if both:
        return "sobre equilibrio en ambas mitades pero IC incluye equilibrio: no concluyente"
    return "sin evidencia"


def summarize(results):
    """{serie: stat} con la misma estadística que la app (aciertos/(aciertos+fallos), IC 95 %, R medio)."""
    out = {}
    for name, rows in results.items():
        t = {"n": 0, "a": 0, "f": 0, "s": 0, "rsum": 0.0}
        for r in rows:
            scorecard._bump({"x": t}, "x", r)
        out[name] = scorecard.stat(t)
    return out


def verdict(stats):
    """Texto honesto: solo hay evidencia si el IC 95 % de la técnica queda sobre el equilibrio y supera al azar."""
    t, rnd = stats.get("tech"), stats.get("random")
    if not t or t["hit"] is None:
        return "Sin señales técnicas en el período."
    if t["small"]:
        return f"Muestra pequeña ({t['a'] + t['f']} resueltas < {scorecard.MIN_N}): sin conclusión."
    if t["lo"] > scorecard.BREAK_EVEN and (rnd is None or rnd["hit"] is None or t["hit"] > rnd["hit"]):
        return "Posible ventaja: el límite inferior del IC 95 % supera el equilibrio. Confírmalo en datos nuevos."
    if t["hi"] < scorecard.BREAK_EVEN:
        return "Pierde dinero: ni el límite superior del IC 95 % llega al equilibrio."
    return "Sin evidencia de ventaja: el IC 95 % incluye el equilibrio (33.3 %)."


def fmt(stats):
    rows = ["serie      n   acierto%  IC95%         R medio"]
    for k in ("tech", "long", "short", "random"):
        s = stats.get(k)
        if not s or s["hit"] is None:
            rows.append(f"{k:<8} {0:>4}   -")
            continue
        rows.append(f"{k:<8} {s['a'] + s['f']:>4}   {s['hit']:>6}   {s['lo']:>5}-{s['hi']:<5}   {s['avg_r']:>+6.2f}")
    return "\n".join(rows)


def fetch(asset):
    c = config.ASSETS[asset]
    intra = sources.yahoo_candles(c["yf"], "60d", "15m")
    hourly = sources.yahoo_candles(c["yf"], "2y", "1h")
    return intra, hourly


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=58)
    ap.add_argument("--assets", default=",".join(config.ASSETS))
    args = ap.parse_args(argv)
    report, total, pooled = {}, {"tech": [], "long": [], "short": [], "random": []}, []
    for a in args.assets.split(","):
        try:
            slots = collect(*fetch(a), days=args.days)
        except Exception as e:
            print(f"{a}: sin datos ({str(e)[:100]})")
            continue
        pooled += slots
        res = from_slots(slots)
        report[a] = summarize(res)
        for k in total:
            total[k] += res[k]
        print(f"\n== {a} ==\n{fmt(report[a])}\n{verdict(report[a])}")
    if total["tech"] or total["long"]:
        report["TOTAL"] = summarize(total)
        print(f"\n== TOTAL ==\n{fmt(report['TOTAL'])}\n{verdict(report['TOTAL'])}")
    if pooled:
        vrep = variant_report(pooled)
        report["VARIANTES"] = vrep
        print("\n== VARIANTES (todos los activos) ==\n" + fmt_variants(vrep))
        for k, d in vrep.items():
            print(f"  {k}: {variant_verdict(d)}")
        print(f"  Se probaron {len(VARIANTS)} variantes: con tantas pruebas algún acierto alto puede ser azar.")
    print(f"\nEquilibrio con 1:{scorecard.RR:g} = {scorecard.BREAK_EVEN}%. Solo mide la parte técnica.")
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, "backtest.json"), "w") as f:
        json.dump({"ts": int(datetime.now().timestamp()), "days": args.days, "report": report}, f)


if __name__ == "__main__":
    main()
