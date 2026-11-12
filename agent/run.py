"""Un ciclo del agente (lo ejecuta GitHub Actions cada ~5 min). Estado y resultados en DATA_DIR (JSON)."""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, llm, notify, scorecard, signals, sources, structure

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
        self.score = scorecard.migrate(load("scorecard.json", {}))
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
        if config.DIGEST_ONLY:
            pass  # modo resumen: la alerta solo se guarda; el aviso va en el resumen de la killzone
        elif severity >= config.MIN_SEVERITY and self._muted(asset):
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
    if plans:
        L.append("PLAN 1:2 calculado (copia estas cifras exactas):")
        L += [f"- {a} {p['dir']}: {plan_line(p)}" for a, p in plans.items()]
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


def _levels(s):
    parts = [f"PDH {s['pdh']:g} PDL {s['pdl']:g}"]
    if "h24" in s:
        parts.append(f"24h {s['h24']:g}-{s['l24']:g}")
    for k, n in (("asia", "Asia"), ("lon", "Londres"), ("ny", "NY")):
        if f"{k}_h" in s:
            parts.append(f"{n} {s[k + '_h']:g}-{s[k + '_l']:g}")
    if s.get("fvg"):
        parts.append(f"FVG {s['fvg']} {s['fvg_lo']:g}-{s['fvg_hi']:g}")
    return " | ".join(parts)


def _news_window(ctx):
    """Noticias clasificadas de las últimas DIGEST_NEWS_H horas, las más fuertes primero."""
    cut = ctx.now - config.DIGEST_NEWS_H * 3600
    news = [a for a in ctx.alerts if a["kind"] == "noticia" and a["ts"] >= cut]
    news.sort(key=lambda a: (-a["severity"], -a["ts"]))
    return news


def digest_news(ctx):
    return _news_window(ctx)[:12]


def structure_job(ctx):
    """Estructura 4h (swings, cuñas, zonas) de cada activo. Un activo sin datos se omite."""
    out = {}
    for a in config.ASSETS:
        try:
            snap = ctx.market.get("snapshots", {}).get(a)
            price = snap["price"] if snap and not snap["stale"] else None
            res = structure.analyze(structure.resample(sources.structure_candles(a)), price)
            if res:
                out[a] = res
        except Exception as e:
            log.warning("estructura %s: %s", a, e)
    ctx.market["structure"] = out


def structure_text(st):
    parts = [f"tendencia {st['trend']}"]
    if st["pattern"]:
        parts.append(f"{st['pattern']} (línea {st['line']:g})")
    if st["res"]:
        parts.append("resistencias " + ", ".join(f"{x:g}" for x in st["res"]))
    if st["sup"]:
        parts.append("soportes " + ", ".join(f"{x:g}" for x in st["sup"]))
    if st["wedge_target"] is not None:
        parts.append(f"objetivo de la cuña {st['wedge_target']:g}")
    return " | ".join(parts)


def confluence_of(ctx, asset):
    """(sesgo técnico, sesgo noticias, neto, etiqueta, sesgo final) cruzando estructura 4h y noticias."""
    st = ctx.market.get("structure", {}).get(asset)
    tech = st["bias"] if st else "neutral"
    nb, net = structure.news_bias(_news_window(ctx), asset)
    label, final = structure.confluence(tech, nb)
    return tech, nb, net, label, final


def digest_bias(ctx, asset):
    """(etiqueta, sesgo final) que usa el resumen: confluencia técnica+noticias, o solo precio sin estructura."""
    if ctx.market.get("structure", {}).get(asset):
        _, _, _, label, final = confluence_of(ctx, asset)
        return label, final
    s = ctx.market["snapshots"][asset]
    thr = config.ASSETS[asset]["daily_pct"] / 10
    return "solo precio", "alcista" if s["chg_4h"] > thr else "bajista" if s["chg_4h"] < -thr else "neutral"


def kz_header(t, slot):
    """Encabezado del aviso con la ventana de la killzone en hora local. t = ahora en hora NY."""
    name, emoji, s, e = slot
    loc = ZoneInfo(config.LOCAL_TZ)
    start = t.replace(hour=s, minute=0, second=0, microsecond=0)
    end = t.replace(hour=e, minute=0, second=0, microsecond=0) + (timedelta(days=1) if e <= s else timedelta())
    return f"{emoji} {name} en 1h ({start.astimezone(loc):%H:%M}–{end.astimezone(loc):%H:%M})"


def digest_context(ctx, header, plans=None):
    loc = ZoneInfo(config.LOCAL_TZ)
    L = [header + " (horas locales)", f"Hora local: {datetime.fromtimestamp(ctx.now, loc):%a %d/%m/%Y %H:%M}"]
    for a, s in ctx.market.get("snapshots", {}).items():
        flag = " [DATOS DESACTUALIZADOS]" if s["stale"] else ""
        L.append(f"{a}: {s['price']} | 1h {s['chg_1h']:+.2f}% | 4h {s['chg_4h']:+.2f}% | día {s['chg_day']:+.2f}%"
                 f" | ATR15 {s['atr15']:.4g} | {_levels(s)}{flag}")
        st = ctx.market.get("structure", {}).get(a)
        L.append("  ESTRUCTURA 4h: " + (structure_text(st) if st else "no disponible"))
        tech, nb, net, label, final = confluence_of(ctx, a)
        L.append(f"  CONFLUENCIA: técnica {tech} + noticias {nb} (neto {net:+d}) => {label}; sesgo final {final}")
    if plans:
        L.append("PLAN 1:2 calculado (copia estas cifras exactas):")
        L += [f"- {a} {p['dir']}: {plan_line(p)}" for a, p in plans.items()]
    fg = ctx.market.get("fear_greed")
    if fg:
        L.append(f"Miedo y codicia cripto: {fg['value']} ({fg['label']})")
    ev = [e for e in ctx.market.get("calendar", []) if ctx.now - 1800 < e["ts"] < ctx.now + 12 * 3600]
    if ev:
        L.append("Eventos de alto impacto (próximas 12h):")
        for e in ev:
            t = datetime.fromtimestamp(e["ts"], loc).strftime("%a %H:%M")
            L.append(f"- {t} {e['country']} {e['title']} (prev {e['previous'] or '–'}, pron {e['forecast'] or '–'})")
    news = digest_news(ctx)
    L.append(f"Noticias clasificadas ({len(news)}):" if news else "Noticias clasificadas: ninguna relevante.")
    for a in news:
        L.append(f"- {a['asset']} {a['direction']} {a['severity']}/5: {a['title'][:110]} | {a['body'].split(chr(10))[0][:120]}")
    return "\n".join(L)


def fallback_digest(ctx):
    """Resumen sin IA: estructura 4h + noticias (o cambio de 4h si no hay estructura). Solo si ninguna IA responde."""
    L = ["NOTICIAS"]
    news = digest_news(ctx)[:4]
    L += [f"• {EMOJI.get(a['direction'], '⚪')} {a['asset']}: {a['title'][:100]}" for a in news] or ["• Sin noticias relevantes"]
    L.append("ANÁLISIS (sin IA)")
    plans = digest_plans(ctx)
    for a, s in ctx.market.get("snapshots", {}).items():
        st = ctx.market.get("structure", {}).get(a)
        px = s["price"]
        label, d = digest_bias(ctx, a)
        if st:
            lv_all = st["res"] + st["sup"] + ([st["wedge_target"]] if st["wedge_target"] is not None else [])
            extra = f" · {st['pattern']}" if st["pattern"] else ""
        else:
            extra = ""
            lv_all = [s[k] for k in ("pdh", "pdl", "h24", "l24") if k in s]
        up, down = [x for x in lv_all if x > px], [x for x in lv_all if x < px]
        above = f"{min(up):g}" if up else "sin nivel"
        below = f"{max(down):g}" if down else "sin nivel"
        pl = plans.get(a)
        if pl:
            lv = plan_line(pl)
        else:
            lv = f"rango {below}–{above}" if label != "en conflicto" else "esperar confirmación"
        L.append(f"{EMOJI[d]} {a} {d} · {label}{' (datos viejos)' if s['stale'] else ''}\n   {px:g}{extra} · {lv}")
    return "\n".join(L)


def px(x):
    return f"{x:.5f}" if abs(x) < 10 else f"{x:.2f}"


def plan_line(pl):
    return f"entrada {px(pl['entry'])} · SL {px(pl['sl'])} · TP {px(pl['tp'])} (1:{pl['rr']:g})"


def levels_for(ctx, asset):
    """Niveles estructurales reales (zonas 4h, PDH/PDL, rango 24h, rangos de sesión) para ubicar el SL."""
    s = ctx.market["snapshots"][asset]
    st = ctx.market.get("structure", {}).get(asset)
    lv = list(st["res"] + st["sup"]) if st else []
    for k in ("pdh", "pdl", "h24", "l24", "asia_h", "asia_l", "lon_h", "lon_l", "ny_h", "ny_l"):
        if k in s:
            lv.append(s[k])
    return lv


def digest_plans(ctx):
    """{activo: plan + sesgo} para los sesgos direccionales con datos al día. Es lo que se envía y se mide."""
    out = {}
    for a, s in ctx.market.get("snapshots", {}).items():
        if s["stale"]:
            continue
        label, d = digest_bias(ctx, a)
        pl = scorecard.plan(d, s["price"], s["atr15"], levels_for(ctx, a))
        if pl:
            out[a] = {**pl, "dir": d, "label": label, "bar_ts": s["ts"]}
    return out


def ensure_plans(text, plans):
    """Garantiza que cada plan aparezca con sus cifras exactas aunque la IA lo omita o lo altere."""
    miss = [a for a, p in plans.items() if px(p["sl"]) not in text or px(p["tp"]) not in text or px(p["entry"]) not in text]
    if not miss:
        return text
    return text + "\nPLAN 1:2\n" + "\n".join(f"{EMOJI[plans[a]['dir']]} {a} {plans[a]['dir']}: {plan_line(plans[a])}" for a in miss)


def _day(ts):
    return datetime.fromtimestamp(ts, ZoneInfo(config.LOCAL_TZ)).strftime("%Y-%m-%d")


def score_lines(ctx):
    """Resultado de la última sesión medida y aciertos de hoy/semana/mes/año (a/(a+f)). Vacío si no hay nada."""
    L = []
    done = [d for d in ctx.score["done"] if d["outcome"] != "sin_datos" and ctx.now - d["ts"] < 48 * 3600]
    if done:
        last = max(d["ts"] for d in done)
        grp = [d for d in done if d["ts"] == last]
        ic = {"acierto": "✅TP", "fallo": "❌SL", "sin_movimiento": "➖"}
        L.append(f"RESULTADO {grp[0]['slot']}: " + " · ".join(f"{d['asset']} {ic[d['outcome']]}" for d in grp))
    per = scorecard.periods(ctx.score["days"], _day(ctx.now))
    parts = []
    for p, n in (("day", "hoy"), ("week", "sem"), ("month", "mes"), ("year", "año")):
        x = per[p][0]
        if x["a"] + x["f"]:
            parts.append(f"{n} {x['a']}/{x['a'] + x['f']} ({x['hit']:.0f}%)")
    if parts:
        L.append("🎯 Aciertos: " + " · ".join(parts))
    return "\n".join(L)


def score_record(ctx, slot, hour, plans):
    """Guarda los planes del resumen recién enviado para verificarlos al terminar la killzone."""
    name, _, _, kz_end = slot
    hz = scorecard.horizon_h(hour, kz_end)
    for a, p in plans.items():
        core = {k: p[k] for k in ("entry", "sl", "tp", "risk", "basis", "rr")}
        scorecard.add(ctx.score, ctx.now, a, p["dir"], p["label"], name, core, p["bar_ts"], hz)


def score_job(ctx):
    """Mide los sesgos cuya killzone ya terminó, con velas de 15m. Sin velas suficientes se reintenta."""
    pend = [r for r in ctx.score["open"] if scorecard.due(r, ctx.now)]
    bars = {}
    for r in pend:
        try:
            if r["asset"] not in bars:
                bars[r["asset"]] = sources.candles(r["asset"])[0]
            res = scorecard.evaluate(r, bars[r["asset"]])
        except Exception as e:
            log.warning("aciertos %s: %s", r["asset"], e)
            res = None
        if res is None and ctx.now - (r["ts"] + r["hz"] * 3600) > scorecard.EXPIRE_H * 3600:
            res = {"outcome": "sin_datos", "r": 0.0}
        if res:
            scorecard.close(ctx.score, r, res, _day(r["ts"]))


def digest_job(ctx):
    """Resumen de noticias + análisis, 1 h antes de cada killzone (hora NY). Es el único push regular."""
    if not config.DIGEST_ONLY:
        return
    t = datetime.fromtimestamp(ctx.now, ZoneInfo(config.MARKET_TZ))
    slot, key = config.DIGEST_SLOTS.get(t.hour), t.strftime("%Y-%m-%d-%H")
    if not slot or ctx.st.get("last_digest") == key or ctx.closed:
        return
    header = kz_header(t, slot)
    structure_job(ctx)
    plans = digest_plans(ctx)
    try:
        text, by = ensure_plans(llm.digest(ctx.st, digest_context(ctx, header, plans)), plans), "ia"
    except llm.LLMUnavailable as e:
        if t.minute < config.DIGEST_FALLBACK_MIN:  # se reintenta en el próximo ciclo
            log.warning("Resumen pendiente, ninguna IA disponible: %s", e)
            return
        text, by = fallback_digest(ctx), "reglas"
    extra = score_lines(ctx)
    text = text + ("\n" + extra if extra else "")
    pushed = notify.push(header, text, priority=4, tags=["newspaper"])
    if not pushed and config.NTFY_TOPIC:
        raise RuntimeError("ntfy no aceptó el resumen")  # sin marcar: reintenta en el próximo ciclo
    ctx.st["last_digest"] = key
    ctx.pushes += pushed
    ctx.alerts.insert(0, {"ts": int(ctx.now), "asset": "MACRO", "kind": "briefing", "direction": "neutral",
                          "severity": 1, "title": header, "body": text, "link": "",
                          "source": "IA" if by == "ia" else "reglas", "pushed": int(pushed), "by": by})
    try:
        score_record(ctx, slot, t.hour, plans)
    except Exception:  # el registro de aciertos nunca debe afectar al resumen ya enviado
        log.exception("registro de aciertos")


def briefing_job(ctx):
    if config.DIGEST_ONLY:  # el resumen por killzone reemplaza al briefing de la mañana
        return
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
    job(ctx, "aciertos", lambda: score_job(ctx))
    job(ctx, "resumen", lambda: digest_job(ctx))

    ctx.alerts = ctx.alerts[:MAX_ALERTS]
    cut = ctx.now - 3 * 86400
    ctx.st["seen"] = {k: v for k, v in ctx.st["seen"].items() if v >= cut}
    ctx.st["cooldown"] = {k: v for k, v in ctx.st["cooldown"].items() if v >= ctx.now - 2 * 86400}
    ctx.market["updated"] = int(ctx.now)
    ctx.market["calendar"] = [e for e in ctx.market.get("calendar", []) if ctx.now - 7200 < e["ts"] < ctx.now + 172800]

    ctx.score["stats"] = scorecard.summary(ctx.score["totals"])
    ctx.score["periods"] = scorecard.periods(ctx.score["days"], _day(ctx.now))
    ctx.score["rr"], ctx.score["break_even"] = scorecard.RR, scorecard.BREAK_EVEN
    ctx.score["updated"] = int(ctx.now)
    save("scorecard.json", ctx.score)
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
        "btc_push": bool(config.WEEKEND_BTC_PUSH), "digest_only": bool(config.DIGEST_ONLY),
    })
    log.info("ciclo OK · alertas=%d push=%d ia=%s", len(ctx.alerts), ctx.pushes, ctx.llm_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
