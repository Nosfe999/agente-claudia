"""Un ciclo del agente (lo ejecuta GitHub Actions cada ~5 min). Estado y resultados en DATA_DIR (JSON)."""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, llm, notify, signals, sources

log = logging.getLogger("run")
EMOJI = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}
TAG = {"alcista": "chart_with_upwards_trend", "bajista": "chart_with_downwards_trend", "neutral": "warning"}
MAX_ALERTS = 300


# ---------------- cierre semanal del mercado ----------------
def market_window(now):
    """(cerrado, ts_reapertura). Cerrado de viernes 17:00 a domingo 18:00 (hora de Nueva York, con horario de verano)."""
    tz = ZoneInfo(config.MARKET_TZ)
    t = datetime.fromtimestamp(now, tz)
    mins = (t.weekday() * 24 + t.hour) * 60 + t.minute
    close = (config.CLOSE_WEEKDAY * 24 + config.CLOSE_HOUR) * 60
    open_ = (config.OPEN_WEEKDAY * 24 + config.OPEN_HOUR) * 60
    if not close <= mins < open_:
        return False, None
    reopen = (t + timedelta(minutes=open_ - mins)).replace(second=0, microsecond=0)
    return True, int(reopen.timestamp())


# ---------------- persistencia ----------------
def _path(name):
    return os.path.join(config.DATA_DIR, name)


def load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, data):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, _path(name))


def cooldown_ok(st, key, minutes, now):
    last = st["cooldown"].get(key)
    if last and now - last < minutes * 60:
        return False
    st["cooldown"][key] = now
    return True


# ---------------- alertas ----------------
class Ctx:
    def __init__(self):
        self.now = time.time()
        self.st = load("state.json", {})
        for k, d in (("seen", {}), ("cooldown", {}), ("fails", {}), ("last", {}), ("err", {})):
            self.st.setdefault(k, d)
        self.alerts = load("alerts.json", [])
        self.market = load("market.json", {})
        self.pushes = 0
        self.fallback_pushes = 0
        self.llm_state = "ok"
        self.closed, self.reopen_ts = market_window(self.now)
        self.muted = 0  # push omitidos por mercado cerrado

    def _muted(self, asset):
        """Mercado cerrado: se silencian los activos que no operan 24/7 (BTC sigue, salvo WEEKEND_BTC_PUSH=0)."""
        if not self.closed:
            return False
        return any(a != "BTC" or not config.WEEKEND_BTC_PUSH for a in asset.split(","))

    def emit(self, asset, kind, direction, severity, title, body="", link="", source="", by="reglas"):
        pushed = False
        if severity >= config.MIN_SEVERITY and self._muted(asset):
            self.muted += 1
        elif severity >= config.MIN_SEVERITY:
            if by == "reglas-ia" and self.fallback_pushes >= config.MAX_FALLBACK_PUSH:
                pass
            else:
                head = (f"{EMOJI.get(direction, '⚪')} {asset} {direction} · {severity}/5" if kind == "noticia"
                        else f"{EMOJI.get(direction, '⚪')} {title}")
                pushed = notify.push(head[:120], body or title, priority=severity,
                                     tags=[TAG.get(direction, "warning")], click=link)
                self.pushes += pushed
                if by == "reglas-ia":
                    self.fallback_pushes += 1
        self.alerts.insert(0, {"ts": int(self.now), "asset": asset, "kind": kind, "direction": direction,
                               "severity": severity, "title": title, "body": body, "link": link,
                               "source": source, "pushed": int(pushed), "by": by})


def job(ctx, name, fn):
    try:
        fn()
        ctx.st["last"][name] = ctx.now
        ctx.st["fails"][name] = 0
        ctx.st["err"].pop(name, None)
    except Exception as e:
        log.exception("job %s", name)
        n = ctx.st["fails"].get(name, 0) + 1
        ctx.st["fails"][name] = n
        ctx.st["err"][name] = str(e)[:300]
        if n >= 3 and cooldown_ok(ctx.st, f"fail:{name}", 360, ctx.now):
            notify.push("⚠️ Agente con fallos", f"'{name}' falla {n} veces seguidas: {str(e)[:150]}",
                        priority=3, tags=["warning"])


# ---------------- trabajos ----------------
def price_job(ctx):
    snaps, status, ok = ctx.market.get("snapshots", {}), {}, 0
    for asset in config.ASSETS:
        try:
            s = sources.snapshot(asset)
            if not s:
                raise RuntimeError("datos insuficientes")
            snaps[asset] = s
            status[asset] = {"ok": True, "source": s["source"], "age_min": s["age_min"]}
            ok += 1
            for sig in signals.check_signals(asset, s):
                if cooldown_ok(ctx.st, f"{asset}:{sig['kind']}:{sig['direction']}", config.SIGNAL_COOLDOWN_MIN, ctx.now):
                    ctx.emit(asset, sig["kind"], sig["direction"], sig["severity"], sig["title"], sig["body"])
        except Exception as e:
            status[asset] = {"ok": False, "error": str(e)[:160]}
            log.warning("precio %s: %s", asset, e)
    ctx.market["snapshots"] = snaps
    ctx.market["price_status"] = status
    if ok == 0:
        raise RuntimeError("sin precios de ningún activo: " + "; ".join(
            f"{a}: {v.get('error', '')[:60]}" for a, v in status.items()))


def news_job(ctx):
    if ctx.now - ctx.st["last"].get("noticias", 0) < config.NEWS_EVERY_MIN * 60:
        return
    items, ok, total = sources.fetch_news()
    ctx.market["news_sources"] = {"ok": ok, "total": total}
    cutoff = ctx.now - config.NEWS_MAX_AGE_H * 3600
    if not ctx.st["seen"]:  # primer arranque: no inundar con noticias viejas
        for i in items:
            ctx.st["seen"][i["id"]] = ctx.now
        log.info("bootstrap: %d titulares marcados como vistos", len(items))
        return
    new = [i for i in items if i["ts"] >= cutoff and i["id"] not in ctx.st["seen"]]
    new.sort(key=lambda i: -i["ts"])
    new = new[:config.MAX_NEWS_PER_CYCLE]
    if not new:
        return
    results, by = None, "ia"
    try:  # la cadena de IAs ya salta sola de un proveedor a otro y respeta sus tiempos de espera
        results = llm.classify(ctx.st, new)
        ctx.llm_state = f"ok ({ctx.st.get('llm_last', '?')})"
    except llm.LLMUnavailable as e:
        log.warning("Ninguna IA disponible: %s", e)
        ctx.llm_state = f"respaldo ({str(e)[:80]})"
    if results is None:
        results, by = llm.fallback_classify(new), "reglas-ia"
    for i in new:  # solo marcamos tras clasificar (si falla todo, reintenta)
        ctx.st["seen"][i["id"]] = ctx.now
    for r in results:
        sev = max(1, r["severity"] - (1 if r["confidence"] < 0.5 else 0))
        it = r["item"]
        body = f"{r['reason']}\nConfianza {int(r['confidence'] * 100)}% · {it['title']} ({it['source']})"
        ctx.emit(",".join(r["assets"]), "noticia", r["direction"], sev, it["title"], body, it["link"],
                 it["source"], by=by)


def calendar_job(ctx):
    if ctx.now - ctx.st["last"].get("calendario_fetch", 0) > 3600 or not ctx.market.get("calendar"):
        ctx.market["calendar"] = sources.fetch_calendar()
        ctx.st["last"]["calendario_fetch"] = ctx.now
    for ev in ctx.market.get("calendar", []):
        mins = (ev["ts"] - ctx.now) / 60
        if 0 < mins <= config.CALENDAR_WARN_MIN and cooldown_ok(ctx.st, f"cal:{ev['ts']}:{ev['title']}", 1440, ctx.now):
            ctx.emit("MACRO", "calendario", "neutral", 3, f"En {int(mins)} min: {ev['country']} {ev['title']}",
                     f"Previo: {ev['previous'] or '–'} · Pronóstico: {ev['forecast'] or '–'}. Se espera volatilidad.")


def fear_greed_job(ctx):
    if ctx.now - ctx.st["last"].get("fg_fetch", 0) < 1800 and ctx.market.get("fear_greed"):
        return
    ctx.market["fear_greed"] = sources.fetch_fear_greed()
    ctx.st["last"]["fg_fetch"] = ctx.now


def context_text(ctx):
    loc = ZoneInfo(config.LOCAL_TZ)
    L = [f"Hora local: {datetime.fromtimestamp(ctx.now, loc).strftime('%a %d/%m/%Y %H:%M')}"]
    for a, s in ctx.market.get("snapshots", {}).items():
        flag = " [DATOS DESACTUALIZADOS]" if s["stale"] else ""
        L.append(f"{a}: {s['price']} | 1h {s['chg_1h']:+.2f}% | 4h {s['chg_4h']:+.2f}% | día {s['chg_day']:+.2f}%"
                 f" | ATR15 {s['atr15']:.4g} | PDH {s['pdh']:g} PDL {s['pdl']:g}{flag}")
    fg = ctx.market.get("fear_greed")
    if fg:
        L.append(f"Miedo y codicia cripto: {fg['value']} ({fg['label']})")
    ev = [e for e in ctx.market.get("calendar", []) if ctx.now - 1800 < e["ts"] < ctx.now + 86400]
    if ev:
        L.append("Eventos de alto impacto (24h):")
        for e in ev:
            t = datetime.fromtimestamp(e["ts"], loc).strftime("%a %H:%M")
            L.append(f"- {t} {e['country']} {e['title']} (prev {e['previous'] or '–'}, pron {e['forecast'] or '–'})")
    recent = [a for a in ctx.alerts if a["severity"] >= 3 and a["kind"] != "briefing"][:12]
    if recent:
        L.append("Alertas recientes:")
        for a in recent:
            L.append(f"- {a['asset']} {a['direction']} {a['severity']}/5: {a['title'][:120]}")
    return "\n".join(L)


def briefing_job(ctx):
    loc = datetime.fromtimestamp(ctx.now, ZoneInfo(config.LOCAL_TZ))
    today = loc.strftime("%Y-%m-%d")
    due = loc.weekday() < 5 and (loc.hour, loc.minute) >= (config.BRIEFING_HOUR, 30) and loc.hour < config.BRIEFING_HOUR + 3
    if not due or ctx.st.get("last_briefing") == today:
        return
    try:
        text = llm.briefing(ctx.st, context_text(ctx))
    except llm.LLMUnavailable as e:  # sin IA no es un fallo del agente: se reintenta en el próximo ciclo
        log.warning("Briefing pendiente, ninguna IA disponible: %s", e)
        return
    ctx.st["last_briefing"] = today
    ctx.alerts.insert(0, {"ts": int(ctx.now), "asset": "MACRO", "kind": "briefing", "direction": "neutral",
                          "severity": 1, "title": "Briefing de la mañana", "body": text, "link": "",
                          "source": "IA", "pushed": 1, "by": "ia"})
    notify.push("🗞️ Briefing de mercado", text, priority=3, tags=["newspaper"])


# ---------------- principal ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ctx = Ctx()
    job(ctx, "precios", lambda: price_job(ctx))
    job(ctx, "calendario", lambda: calendar_job(ctx))
    job(ctx, "noticias", lambda: news_job(ctx))
    job(ctx, "sentimiento", lambda: fear_greed_job(ctx))
    job(ctx, "briefing", lambda: briefing_job(ctx))

    ctx.alerts = ctx.alerts[:MAX_ALERTS]
    cut = ctx.now - 3 * 86400
    ctx.st["seen"] = {k: v for k, v in ctx.st["seen"].items() if v >= cut}
    ctx.st["cooldown"] = {k: v for k, v in ctx.st["cooldown"].items() if v >= ctx.now - 2 * 86400}
    ctx.market["updated"] = int(ctx.now)
    ctx.market["calendar"] = [e for e in ctx.market.get("calendar", []) if ctx.now - 7200 < e["ts"] < ctx.now + 172800]

    save("alerts.json", ctx.alerts)
    save("market.json", ctx.market)
    save("state.json", ctx.st)
    save("health.json", {
        "updated": int(ctx.now), "last": ctx.st["last"], "fails": ctx.st["fails"], "errors": ctx.st["err"],
        "llm": ctx.llm_state, "llm_last": ctx.st.get("llm_last", ""), "llm_providers": llm.status(ctx.st),
        "model": config.GEMINI_MODEL or ctx.st.get("model", ""),
        "ia_configured": llm.configured(), "gemini_configured": llm.configured(),  # gemini_configured: compat. app
        "ntfy_configured": bool(config.NTFY_TOPIC),
        "pushes": ctx.pushes,
        "market_closed": ctx.closed, "reopen_ts": ctx.reopen_ts, "muted": ctx.muted,
        "btc_push": bool(config.WEEKEND_BTC_PUSH),
    })
    log.info("ciclo OK · alertas=%d push=%d ia=%s", len(ctx.alerts), ctx.pushes, ctx.llm_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
