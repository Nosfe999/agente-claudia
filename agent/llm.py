"""IA gratuita con varios proveedores en cadena (si uno agota la cuota o falla, pasa al siguiente)
y, si todos fallan, respaldo por palabras clave.

Orden por defecto: Gemini -> Groq -> Cerebras -> Mistral -> NVIDIA -> OpenRouter (solo 50/día gratis, va al final).
Cambia el orden con LLM_ORDER.
Solo se usan los proveedores que tienen clave configurada."""
import json
import logging
import re
import time

import requests

from . import config

log = logging.getLogger("llm")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
PROVIDERS = ("gemini", "groq", "cerebras", "mistral", "nvidia", "openrouter")


class LLMUnavailable(Exception):
    """Sin clave, cuota agotada o servicio caído. backoff_min = minutos que se deja descansar al proveedor."""

    def __init__(self, msg, backoff_min=None):
        super().__init__(msg)
        self.backoff_min = config.LLM_BACKOFF_MIN if backoff_min is None else backoff_min


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


# ---------------- utilidades comunes ----------------
def _check(r):
    """Traduce códigos HTTP a errores del agente. 404 = modelo inexistente (se redescubre)."""
    c = r.status_code
    if c < 400:
        return
    if c in (401, 403):
        raise LLMUnavailable(f"HTTP {c} (clave inválida o sin permiso)", 360)
    if c == 404:
        raise FileNotFoundError(f"{r.url} {r.text[:120]}")
    if c == 429:
        raise LLMUnavailable(f"HTTP 429 (cuota o límite): {r.text[:100]}", config.LLM_BACKOFF_MIN)
    if c == 408 or c >= 500:
        raise LLMUnavailable(f"HTTP {c} (servicio saturado)", 10)
    raise LLMUnavailable(f"HTTP {c}: {r.text[:80]}", 15)


def _clean(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)  # modelos que razonan en voz alta
    text = text.strip()
    if not text:
        raise LLMUnavailable("respuesta vacía", 10)
    return text


def _key(name):
    return {"gemini": config.GEMINI_API_KEY, "groq": config.GROQ_API_KEY, "cerebras": config.CEREBRAS_API_KEY,
            "mistral": config.MISTRAL_API_KEY, "nvidia": config.NVIDIA_API_KEY,
            "openrouter": config.OPENROUTER_API_KEY}[name]


def order():
    """Proveedores en el orden de LLM_ORDER; los que falten van al final."""
    wanted = [p.strip().lower() for p in (config.LLM_ORDER or "").split(",")]
    out = [p for p in wanted if p in PROVIDERS]
    return out + [p for p in PROVIDERS if p not in out]


def configured():
    return any(_key(p) for p in PROVIDERS)


# ---------------- Gemini ----------------
def _headers():
    return {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}


def discover_models():
    """Modelos Flash/Flash-Lite que soportan generateContent, del más reciente/estable al menos."""
    r = requests.get(f"{GEMINI_BASE}/models?pageSize=200", headers=_headers(), timeout=20)
    _check(r)
    found = []
    for m in r.json().get("models", []):
        name = m["name"].split("/")[-1]
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if "flash" not in name or re.search(r"image|tts|live|audio|embed|thinking|robot|computer|native", name):
            continue
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        found.append(((0 if "preview" in name or "exp" in name else 1,
                       float(v.group(1)) if v else 0,
                       1 if "lite" in name else 0), name))
    if not found:
        raise LLMUnavailable("no hay modelo Flash disponible")
    return [n for _, n in sorted(found, reverse=True)]


def discover_model():
    return discover_models()[0]


def _generate(model, system, user, json_mode, max_tokens):
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent", headers=_headers(), json=body,
                      timeout=config.LLM_TIMEOUT_S)
    _check(r)
    cands = r.json().get("candidates") or []
    parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
    return _clean("".join(p.get("text", "") for p in parts))


def _call_gemini(state, system, user, json_mode, max_tokens):
    fixed = config.GEMINI_MODEL
    if fixed:
        try:
            return _generate(fixed, system, user, json_mode, max_tokens)
        except FileNotFoundError:
            raise LLMUnavailable(f"modelo '{fixed}' no existe", 360)
    cands = [state["model"]] if state.get("model") else []
    errs = []
    for attempt in range(2):
        if not cands:
            cands = discover_models()[:3]
        errs = []
        for model in cands:
            try:
                text = _generate(model, system, user, json_mode, max_tokens)
            except FileNotFoundError:
                errs.append(f"{model}: 404")
                continue
            except (LLMUnavailable, requests.RequestException) as e:
                errs.append(f"{model}: {str(e)[:60]}")
                if isinstance(e, LLMUnavailable) and e.backoff_min >= 60:
                    raise  # clave inválida u otro error que otro modelo no arregla
                continue
            state["model"] = model
            return text
        state.pop("model", None)
        cands = []
        if attempt == 0 and not errs:
            continue
        break
    raise LLMUnavailable("ningún modelo Gemini respondió: " + "; ".join(errs)[:200], 10)


# ---------------- Groq / OpenRouter / Cerebras (API compatible con OpenAI) ----------------
COMPAT = {
    "groq": {"base": "https://api.groq.com/openai/v1", "model": lambda: config.GROQ_MODEL,
             "prefer": [r"llama-3\.3-70b", r"gpt-oss-120b", r"llama-3\.1-70b", r"qwen.*32b", r"llama-3\.1-8b"]},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "model": lambda: config.OPENROUTER_MODEL,
                   "prefer": [r"llama-3\.3-70b", r"gpt-oss-120b", r"deepseek", r"qwen", r"gemma", r"mistral"]},
    "cerebras": {"base": "https://api.cerebras.ai/v1", "model": lambda: config.CEREBRAS_MODEL,
                 "prefer": [r"gpt-oss-120b", r"llama-?3\.3-70b", r"qwen-3-235b", r"qwen-3-32b", r"llama-?3\.1-8b"]},
    "mistral": {"base": "https://api.mistral.ai/v1", "model": lambda: config.MISTRAL_MODEL,
                "prefer": [r"^mistral-small-latest$", r"^mistral-medium-latest$", r"^open-mistral-nemo$",
                           r"mistral-small", r"mistral-medium", r"mistral-large"]},
    "nvidia": {"base": "https://integrate.api.nvidia.com/v1", "model": lambda: config.NVIDIA_MODEL,
               "prefer": [r"llama-3\.3-70b", r"gpt-oss-120b", r"llama-3\.1-70b", r"nemotron.*(super|70b)", r"mistral-small",
                          r"gemma-3-27b", r"qwen.*(72b|32b)", r"llama-3\.1-8b", r"gemma", r"qwen", r"llama"]},
}
EXCLUDE = re.compile(r"whisper|guard|tts|embed|vision|image|audio|moderation|orpheus|compound|safeguard|"
                     r"transcrib|speech|rerank|-vl|ocr|pixtral|voxtral|codestral|reward|parse|clip|safety|retriev|nemoretriever|embedqa", re.I)


def _compat_headers(name):
    h = {"Authorization": f"Bearer {_key(name)}", "Content-Type": "application/json"}
    if name == "openrouter":
        h.update({"HTTP-Referer": "https://github.com/Nosfe999/agente-claudia", "X-Title": "Mercado Agent"})
    return h


def _discover_compat(name):
    """Modelos candidatos, del más preferido al menos (algunos listados no sirven para chat: se prueba el siguiente)."""
    cfg = COMPAT[name]
    r = requests.get(f"{cfg['base']}/models", headers=_compat_headers(name), timeout=20)
    _check(r)
    ids = []
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        if not mid or m.get("active") is False or EXCLUDE.search(mid):
            continue
        if name == "openrouter":  # solo modelos gratuitos
            p = m.get("pricing") or {}
            if not (mid.endswith(":free") or (str(p.get("prompt")) == "0" and str(p.get("completion")) == "0")):
                continue
        ids.append(mid)
    out = []
    for rx in cfg["prefer"]:
        out += [mid for mid in ids if re.search(rx, mid, re.I) and mid not in out]
    out += [mid for mid in ids if mid not in out]
    if not out:
        raise LLMUnavailable("sin modelos gratuitos disponibles", 60)
    return out[:10]


def _chat(name, model, system, user, max_tokens):
    body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in range(2):
        r = requests.post(f"{COMPAT[name]['base']}/chat/completions", headers=_compat_headers(name), json=body,
                          timeout=config.LLM_TIMEOUT_S)
        if r.status_code == 429 and attempt == 0:  # límite por segundo (p. ej. Mistral gratis): un reintento corto
            try:
                wait = min(float(r.headers.get("retry-after", 2)), 5)
            except ValueError:
                wait = 2
            time.sleep(wait)
            continue
        break
    _check(r)
    j = r.json()
    if j.get("error"):  # OpenRouter a veces responde 200 con un error dentro
        code = (j["error"].get("code") if isinstance(j["error"], dict) else None) or 0
        raise LLMUnavailable(f"{name}: {str(j['error'])[:80]}", config.LLM_BACKOFF_MIN if code == 429 else 15)
    ch = j.get("choices") or []
    msg = (ch[0].get("message") or {}) if ch else {}
    if not (msg.get("content") or "").strip():
        raise LLMUnavailable(f"respuesta vacía (finish={ch[0].get('finish_reason') if ch else '-'}, "
                             f"modelo {model}; ¿razonamiento sin tokens?)", 10)
    return _clean(msg["content"])


def _call_compat(name, state, system, user, json_mode, max_tokens):
    # json_mode se ignora: el prompt ya pide JSON y parse_classification extrae el array aunque venga con texto.
    models = state.setdefault("models", {})
    fixed = COMPAT[name]["model"]()
    if fixed:
        try:
            return _chat(name, fixed, system, user, max_tokens)
        except FileNotFoundError:
            raise LLMUnavailable(f"modelo '{fixed}' no existe", 360)
    cands = [models[name]] if models.get(name) else []
    for attempt in range(2):
        if not cands:
            cands = _discover_compat(name)
            if attempt == 0:
                time.sleep(1)  # algunos planes gratis limitan a 1 solicitud/seg
        notfound = []
        for model in cands:
            try:
                text = _chat(name, model, system, user, max_tokens)
            except FileNotFoundError as e:
                notfound.append(f"{model}: {str(e)[-60:]}")
                continue
            models[name] = model
            return text
        models.pop(name, None)
        cands = []
    raise LLMUnavailable("ningún modelo respondió: " + "; ".join(notfound)[:200], 60)


_FN = {
    "gemini": _call_gemini,
    "groq": lambda *a: _call_compat("groq", *a),
    "openrouter": lambda *a: _call_compat("openrouter", *a),
    "cerebras": lambda *a: _call_compat("cerebras", *a),
    "mistral": lambda *a: _call_compat("mistral", *a),
    "nvidia": lambda *a: _call_compat("nvidia", *a),
}


# ---------------- cadena con respaldo automático ----------------
def call(state, system, user, json_mode=False, max_tokens=4000):
    """Prueba cada proveedor con clave, en orden. Uno que falla descansa unos minutos (state['llm_backoff'])
    y se pasa al siguiente. Si ninguno responde, LLMUnavailable (el agente usa reglas de palabras clave)."""
    now = time.time()
    backoff = state.setdefault("llm_backoff", {})
    errs = state.setdefault("llm_err", {})
    active = [p for p in order() if _key(p)]
    if not active:
        raise LLMUnavailable("sin claves de IA (GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, CEREBRAS_API_KEY)")
    skipped = []
    for name in active:
        if now < backoff.get(name, 0):
            skipped.append(f"{name}: en espera")
            continue
        try:
            text = _FN[name](state, system, user, json_mode, max_tokens)
        except requests.RequestException as e:
            err = LLMUnavailable(f"{type(e).__name__}: {str(e)[:100]}", 10)
        except LLMUnavailable as e:
            err = e
        else:
            if state.get("llm_last") != name:
                log.info("IA activa: %s", name)
            state["llm_last"] = name
            backoff.pop(name, None)
            errs.pop(name, None)
            return text
        backoff[name] = time.time() + err.backoff_min * 60
        errs[name] = str(err)[:120]
        log.warning("IA %s falló (%s); descansa %d min", name, err, err.backoff_min)
        skipped.append(f"{name}: {str(err)[:50]}")
    raise LLMUnavailable("; ".join(skipped) or "ningún proveedor respondió")


def status(state):
    """Estado de cada proveedor para health.json (nunca incluye las claves)."""
    now = time.time()
    out = []
    for name in order():
        wait = state.get("llm_backoff", {}).get(name, 0) - now
        out.append({"name": name, "configured": bool(_key(name)),
                    "waiting_min": max(0, int(wait / 60)) if _key(name) else 0,
                    "error": state.get("llm_err", {}).get(name, "") if _key(name) else "",
                    "model": (config.GEMINI_MODEL or state.get("model", "")) if name == "gemini"
                    else (COMPAT[name]["model"]() or state.get("models", {}).get(name, ""))})
    return out


def probe(name):
    """Prueba directa de un proveedor (ignora el descanso). Para el workflow de diagnóstico."""
    st = {}
    text = _FN[name](st, "Responde solo con la palabra: ok", "ping", False, 300)
    model = (config.GEMINI_MODEL or st.get("model")) if name == "gemini" \
        else (COMPAT[name]["model"]() or st.get("models", {}).get(name))
    return f"modelo {model} -> {text[:30]!r}"


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
