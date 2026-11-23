"""¿En qué momento del fin de semana hay más volumen de BTC?
Velas 1h de BTC-USD (Yahoo), por defecto ~120 días (≈17 fines de semana). Fin de semana = viernes 17:00 NY ->
domingo 18:00 NY (mismo cierre que el resto del agente). Se promedia el volumen por hora y se compara con el
promedio de un día hábil. Sin red no hace nada.

Uso: python -m agent.volumen_finde [--days 120]
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, sources

NY = ZoneInfo(config.MARKET_TZ)
LOCAL = ZoneInfo(config.LOCAL_TZ)
DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _in_weekend(t):
    mins = (t.weekday() * 24 + t.hour) * 60 + t.minute
    return (config.CLOSE_WEEKDAY * 24 + config.CLOSE_HOUR) * 60 <= mins < (config.OPEN_WEEKDAY * 24 + config.OPEN_HOUR) * 60


def weekend_profile(rows, top=5):
    """rows: [(ts, volumen)]. Función pura. Devuelve volumen medio por (día, hora NY) del fin de semana,
    las `top` mejores horas (con la hora equivalente en Asunción) y la razón vs. la hora media de un día hábil."""
    slot, weekday_vols = defaultdict(list), []
    for ts, v in rows:
        t = datetime.fromtimestamp(ts, NY)
        if _in_weekend(t):
            slot[(t.weekday(), t.hour)].append(v)
        elif t.weekday() < 4 or (t.weekday() == 4 and t.hour < config.CLOSE_HOUR):
            weekday_vols.append(v)
    if not slot or not weekday_vols:
        return None
    avg = {k: sum(v) / len(v) for k, v in slot.items()}
    base = sum(weekday_vols) / len(weekday_vols)
    total = sum(avg.values())
    ranked = sorted(avg.items(), key=lambda kv: -kv[1])
    out = []
    for (wd, h), v in ranked[:top]:
        ny = datetime(2026, 1, 1, h, tzinfo=NY)  # solo para convertir la hora del día
        loc = ny.astimezone(LOCAL)
        out.append({"dia": DAYS[wd], "hora_ny": h, "hora_local": loc.hour, "vs_dia_habil": round(v / base, 2),
                    "pct_del_finde": round(v / total * 100, 1), "muestras": len(slot[(wd, h)])})
    by_day = defaultdict(float)
    for (wd, _), v in avg.items():
        by_day[wd] += v
    return {"top_horas": out, "reparto_por_dia_pct": {DAYS[d]: round(v / total * 100, 1) for d, v in sorted(by_day.items())},
            "volumen_medio_hora_finde_vs_habil": round(total / len(avg) / base, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    args = ap.parse_args()
    res = weekend_profile(sources.yahoo_volume_1h("BTC-USD", f"{args.days}d"))
    if not res:
        raise SystemExit("sin datos suficientes")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, "volumen_finde.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
