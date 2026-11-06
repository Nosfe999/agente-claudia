import functools
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import analyzer
import config
import notify
import signals
import sources
import storage

log = logging.getLogger("agent")
STATE = {"snapshots": {}, "calendar": [], "last": {}, "fails": {}, "err": {}}

EMOJI = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}
TAG = {"alcista": "chart_with_upwards_trend", "bajista": "chart_with_downwards_trend", "neutral": "warning"}


def _job(name):
    """Registra ejecución y avisa por push si un trabajo falla 3 veces seguidas."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                fn(*a, **k)
                STATE["last"][name] = time.time()
                STATE["fails"][name] = 0
            except Exception as e:
                log.exception("job %s", name)
                n = STATE["fails"].get(name, 0) + 1
                STATE["fails"][name] = n
                STATE["err"][name] = str(e)[:200]
                if n >= 3 and storage.cooldown_ok(f"fail:{name}", 360):
                    notify.push("⚠️ Agente con fallos", f"'{name}' falla {n} veces seguidas: {str(e)[:150]}",
                                priority=3, tags=["warning"])
        return wrapper
    return deco


def emit(asset, kind, direction, severity, title, body="", link="", source="", push=True):
    pushed = push and severity >= config.MIN_SEVERITY
    storage.add_alert(asset, kind, direction, severity, title, body, link, source, int(pushed))
    if pushed:
        notify.push(f"{EMOJI.get(direction, '⚪')} {title}"[:120] if kind != "noticia" else
                    f"{EMOJI.get(direction, '⚪')} {asset} {direction} · {severity}/5",
                    body, priority=severity, tags=[TAG.get(direction, "warning")], click=link)


# ---------------- ciclos ----------------
@_job("noticias")
def news_cycle():
    items = sources.fetch_news()
    cutoff = time.time() - config.NEWS_MAX_AGE_H * 3600
    fresh = [i for i in items if i["ts"] >= cutoff]
    if storage.seen_count() == 0:  # primer arranque: no inundar con noticias viejas
        storage.mark_seen([i["id"] for i in items])
        log.info("bootstrap: %d titulares marcados como vistos", len(items))
        return
    new = sorted(storage.unseen(fresh), key=lambda i: -i["ts"])[:config.MAX_NEWS_PER_CYCLE]
    if not new:
        return
    results = analyzer.classify(new)
    storage.mark_seen([i["id"] for i in new])  # solo tras clasificar OK (si falla, reintenta)
    for r in results:
        sev = r["severity"] - (1 if r["confidence"] < 0.5 else 0)
        sev = max(1, sev)
        it = r["item"]
        body = f"{r['reason']}\nConfianza {int(r['confidence'] * 100)}% · {it['title']} ({it['source']})"
        emit(",".join(r["assets"]), "noticia", r["direction"], sev, it["title"], body, it["link"], it["source"])


@_job("precios")
def price_cycle():
    ok = 0
    for asset in config.ASSETS:
        try:
            s = sources.snapshot(asset)
            if not s:
                continue
            STATE["snapshots"][asset] = s
            ok += 1
            for sig in signals.check_signals(asset, s):
                if storage.cooldown_ok(f"{asset}:{sig['kind']}:{sig['direction']}", config.SIGNAL_COOLDOWN_MIN):
                    emit(asset, sig["kind"], sig["direction"], sig["severity"], sig["title"], sig["body"])
        except Exception:
            log.exception("snapshot %s", asset)
    if ok == 0:
        raise RuntimeError("no se pudo obtener precios de ningún activo")


@_job("calendario")
def calendar_cycle():
    cal = sources.fetch_calendar()
    STATE["calendar"] = cal
    now = time.time()
    for ev in cal:
        mins = (ev["ts"] - now) / 60
        if 0 < mins <= config.CALENDAR_WARN_MIN and storage.cooldown_ok(f"cal:{ev['ts']}:{ev['title']}", 1440):
            emit("MACRO", "calendario", "neutral", 3,
                 f"En {int(mins)} min: {ev['country']} {ev['title']}",
                 f"Previo: {ev['previous'] or '–'} · Pronóstico: {ev['forecast'] or '–'}. Se espera volatilidad.")


def context_text():
    loc = ZoneInfo(config.LOCAL_TZ)
    L = [f"Hora local: {datetime.now(loc).strftime('%a %d/%m/%Y %H:%M')}"]
    for a, s in STATE["snapshots"].items():
        flag = " [DATOS DESACTUALIZADOS]" if s["stale"] else ""
        L.append(f"{a}: {s['price']} | 1h {s['chg_1h']:+.2f}% | 4h {s['chg_4h']:+.2f}% | día {s['chg_day']:+.2f}%"
                 f" | ATR15 {s['atr15']:.4g} | PDH {s['pdh']:g} PDL {s['pdl']:g}{flag}")
    fg = sources.fetch_fear_greed()
    if fg:
        L.append(f"Miedo y codicia cripto: {fg['value']} ({fg['label']})")
    now = time.time()
    ev = [e for e in STATE["calendar"] if now - 1800 < e["ts"] < now + 86400]
    if ev:
        L.append("Eventos de alto impacto (24h):")
        for e in ev:
            t = datetime.fromtimestamp(e["ts"], loc).strftime("%a %H:%M")
            L.append(f"- {t} {e['country']} {e['title']} (prev {e['previous'] or '–'}, pron {e['forecast'] or '–'})")
    recent = storage.list_alerts(12, min_sev=3)
    if recent:
        L.append("Alertas recientes:")
        for a in recent:
            L.append(f"- {a['asset']} {a['direction']} {a['severity']}/5: {a['title'][:120]}")
    return "\n".join(L)


@_job("briefing")
def briefing_job():
    text = analyzer.briefing(context_text())
    storage.add_alert("MACRO", "briefing", "neutral", 1, "Briefing de la mañana", text)
    notify.push("🗞️ Briefing de mercado", text, priority=3, tags=["newspaper"])


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    tz = ZoneInfo(config.LOCAL_TZ)
    sch = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                         "misfire_grace_time": 120})
    now = datetime.now(tz)
    sch.add_job(price_cycle, "interval", minutes=config.PRICE_EVERY_MIN, next_run_time=now)
    sch.add_job(calendar_cycle, "interval", minutes=config.CALENDAR_EVERY_MIN, next_run_time=now)
    sch.add_job(news_cycle, "interval", minutes=config.NEWS_EVERY_MIN, next_run_time=now)
    sch.add_job(briefing_job, "cron", day_of_week="mon-fri", hour=config.BRIEFING_HOUR, minute=30)
    sch.add_job(lambda: storage.prune(30), "cron", hour=3, minute=0)
    sch.start()
    return sch
