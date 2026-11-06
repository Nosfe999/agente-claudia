import json
import re

import config

_client = None

SYSTEM_CLASSIFY = """Eres analista de mercados para un trader intradía. Activos: BTC, XAUUSD, EURUSD, NASDAQ, SP500.
Recibes titulares numerados. Decide cuáles tienen impacto material en algún activo (horas a días).

Reglas:
- Cuenta: hechos concretos (ventas/compras grandes de BTC por empresas o ballenas, flujos de ETF, datos macro, decisiones de bancos centrales, aranceles, geopolítica, hacks/quiebras, resultados de big tech, regulación).
- Ignora: opinión, predicciones de precio de analistas, clickbait, publicidad, noticias viejas o repetidas.
- Usa SOLO lo que dicen titular y resumen. No inventes cifras ni contexto.
- severity: 1 irrelevante, 2 menor, 3 relevante, 4 fuerte, 5 crítico/sistémico.
- direction por activo: alcista, bajista o neutral. Si un titular afecta distinto a varios activos, emite entradas separadas con el mismo "i".
- confidence 0-1: certeza de que impacto y dirección son correctos. Baja si es ambiguo.
- reason: máximo 160 caracteres, en español, con el dato clave.

Devuelve SOLO un array JSON, sin texto extra:
[{"i":0,"assets":["BTC"],"direction":"bajista","severity":4,"confidence":0.7,"reason":"..."}]
Incluye solo titulares con severity>=2. Si no hay ninguno, devuelve []."""

SYSTEM_BRIEF = """Eres analista de mercados de un trader intradía (BTC, XAUUSD, EURUSD, NASDAQ, SP500).
Con el contexto dado escribe, en español y máximo 250 palabras:
1) Sesgo por activo (alcista/bajista/neutral) y por qué, en una línea cada uno.
2) Eventos clave de hoy con hora local.
3) Riesgos principales.
4) Niveles a vigilar (PDH/PDL).
Sin promesas ni órdenes de compra/venta. Señala incertidumbre y datos desactualizados."""

SYSTEM_ASK = """Eres el analista de mercados del trader. Responde en español, conciso y con datos del contexto.
Si buscas en la web, cita fuente y fecha. No prometas resultados ni des órdenes de compra/venta:
da escenarios con niveles e invalidación. Si no sabes o los datos están desactualizados, dilo."""


def client():
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("Falta ANTHROPIC_API_KEY")
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=90, max_retries=2)
    return _client


def _text(resp):
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def parse_classification(text, items):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for r in arr:
        try:
            item = items[int(r["i"])]
            assets = [a for a in r.get("assets", []) if a in config.ASSETS]
            if not assets:
                continue
            d = r.get("direction", "neutral")
            d = d if d in ("alcista", "bajista", "neutral") else "neutral"
            out.append({
                "item": item, "assets": assets, "direction": d,
                "severity": max(1, min(5, int(r.get("severity", 1)))),
                "confidence": max(0.0, min(1.0, float(r.get("confidence", 0.5)))),
                "reason": str(r.get("reason", ""))[:240],
            })
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return out


def classify(items):
    if not items:
        return []
    lines = [f"[{i}] {it['title']} — {it['summary']} ({it['source']}; tema: {it['hint']})"
             for i, it in enumerate(items)]
    resp = client().messages.create(
        model=config.MODEL_FAST, max_tokens=2500, system=SYSTEM_CLASSIFY,
        messages=[{"role": "user", "content": "\n".join(lines)}])
    return parse_classification(_text(resp), items)


def briefing(context):
    resp = client().messages.create(
        model=config.MODEL_SMART, max_tokens=900, system=SYSTEM_BRIEF,
        messages=[{"role": "user", "content": context}])
    return _text(resp)


def ask(question, context, use_web=True):
    msg = [{"role": "user", "content": f"CONTEXTO ACTUAL:\n{context}\n\nPREGUNTA: {question}"}]
    kw = dict(model=config.MODEL_SMART, max_tokens=1500, system=SYSTEM_ASK, messages=msg)
    if use_web:
        try:
            resp = client().messages.create(
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}], **kw)
            return _text(resp)
        except Exception:
            pass  # herramienta no disponible → responder sin web
    return _text(client().messages.create(**kw))
