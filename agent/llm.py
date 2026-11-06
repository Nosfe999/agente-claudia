"""IA gratuita (Gemini, plan gratuito de Google AI Studio) con respaldo por palabras clave."""
import json
import logging
import re

import requests

from . import config

log = logging.getLogger("llm")
BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMUnavailable(Exception):
    """Sin clave, cuota agotada o servicio caído: el agente usa el clasificador de respaldo."""


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


# ---------------- Gemini ----------------
def _headers():
    return {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}


def discover_model():
    """Elige el Flash/Flash-Lite estable más reciente que soporte generateContent."""
    r = requests.get(f"{BASE}/models?pageSize=200", headers=_headers(), timeout=20)
    r.raise_for_status()
    best = None
    for m in r.json().get("models", []):
        name = m["name"].split("/")[-1]
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if "flash" not in name or re.search(r"image|tts|live|audio|embed|thinking|robot|computer|native", name):
            continue
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        key = (0 if "preview" in name or "exp" in name else 1,
               float(v.group(1)) if v else 0,
               1 if "lite" in name else 0)
        if best is None or key > best[0]:
            best = (key, name)
    if not best:
        raise LLMUnavailable("no hay modelo Flash disponible")
    return best[1]


def _generate(model, system, user, json_mode, max_tokens):
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(f"{BASE}/models/{model}:generateContent", headers=_headers(), json=body, timeout=60)
    if r.status_code in (429, 503):
        raise LLMUnavailable(f"HTTP {r.status_code} (cuota o saturación)")
    if r.status_code == 404:
        raise FileNotFoundError(model)
    r.raise_for_status()
    cands = r.json().get("candidates") or []
    parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise LLMUnavailable("respuesta vacía")
    return text


def call(state, system, user, json_mode=False, max_tokens=4000):
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("falta GEMINI_API_KEY")
    model = config.GEMINI_MODEL or state.get("model") or ""
    for attempt in range(2):
        try:
            if not model:
                model = discover_model()
                state["model"] = model
            return _generate(model, system, user, json_mode, max_tokens)
        except FileNotFoundError:
            state.pop("model", None)
            model = ""
        except requests.RequestException as e:
            raise LLMUnavailable(str(e)[:120])
    raise LLMUnavailable("modelo no encontrado")


# ---------------- clasificación ----------------
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
            out.append({"item": item, "assets": assets, "direction": d,
                        "severity": max(1, min(5, int(r.get("severity", 1)))),
                        "confidence": max(0.0, min(1.0, float(r.get("confidence", 0.5)))),
                        "reason": str(r.get("reason", ""))[:240], "by": "ia"})
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return out


def classify(state, items):
    lines = [f"[{i}] {it['title']} — {it['summary']} ({it['source']}; tema: {it['hint']})"
             for i, it in enumerate(items)]
    text = call(state, SYSTEM_CLASSIFY, "\n".join(lines), json_mode=True)
    return parse_classification(text, items)


def briefing(state, context):
    return call(state, SYSTEM_BRIEF, context, max_tokens=1500)


# ---------------- respaldo sin IA ----------------
RULES = [  # (activos, regex, dirección, motivo)
    (["BTC"], r"\b(sells?|sold|selling|dump(s|ed)?|liquidat\w+|outflows?|hack(ed)?|exploit|ban(s|ned)?|crackdown|insolven\w+|bankrupt\w*)\b", "bajista", "Posible presión vendedora/riesgo en BTC"),
    (["BTC"], r"\b(buys?|bought|accumulat\w+|inflows?|etf approv\w+|record high|adopt\w+|reserve)\b", "alcista", "Posible demanda/impulso en BTC"),
    (["XAUUSD"], r"\b(rate cuts?|dovish|safe[- ]haven|central banks? (buy|buying)|geopolitic\w+|war)\b", "alcista", "Factor que suele favorecer al oro"),
    (["XAUUSD"], r"\b(rate hikes?|hawkish|strong dollar|yields? (jump|surge|rise))\b", "bajista", "Factor que suele presionar al oro"),
    (["NASDAQ", "SP500"], r"\b(selloff|sell-off|plunge\w*|tumble\w*|crash\w*|recession|tariffs?|downgrade\w*)\b", "bajista", "Riesgo de caída en acciones"),
    (["NASDAQ", "SP500"], r"\b(rally|surge\w*|record high|beats? (estimates|expectations)|rate cuts?)\b", "alcista", "Impulso positivo en acciones"),
    (["EURUSD"], r"\b(ecb (hike|cut|raises|lowers)|euro (falls|slumps|jumps|rises)|dollar (surges|slumps))\b", "neutral", "Movimiento relevante en EUR/USD"),
]


def fallback_classify(items):
    out = []
    for it in items:
        text = f"{it['title']} {it['summary']}".lower()
        for assets, rx, direction, why in RULES:
            tgt = [a for a in assets if it["hint"] in (a, "MACRO")]
            if tgt and re.search(rx, text):
                out.append({"item": it, "assets": tgt, "direction": direction, "severity": 4,
                            "confidence": 0.4, "reason": f"[sin IA] {why}", "by": "reglas"})
                break
    return out
