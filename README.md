# Mercado Agent (100 % gratis)

Agente que vigila **BTC, XAUUSD, EURUSD, NASDAQ y SP500** y te avisa en el celular cuando detecta riesgo de caída o posible subida. Sin servidor, sin tarjeta, sin pagar.

| Pieza | Servicio gratuito |
|---|---|
| Ejecución (cada ~5-10 min) | GitHub Actions (ilimitado en repos públicos) |
| IA que clasifica noticias y escribe el briefing | Cadena de IAs gratuitas con respaldo automático: Gemini → Groq → Cerebras → Mistral → NVIDIA → OpenRouter |
| Notificaciones en Android | ntfy.sh |
| App instalable | GitHub Pages (PWA) |
| Datos | Yahoo Finance, Kraken, Google News/RSS, Forex Factory, alternative.me |

## Cómo funciona
1. El workflow `agente` corre cada 5 min: precios → calendario → noticias (cada ~9 min) → briefing (lun-vie 07:30 hora local).
2. Las noticias nuevas las clasifica la IA (activo, dirección, severidad 1-5, confianza). Ej.: "Riot vende 8,962 BTC" → 🔴 BTC bajista 4/5.
3. Si una IA falla o se le acaba la cuota (429, timeout, 5xx), pasa sola a la siguiente y la deja descansar unos minutos. Solo si **todas** fallan usa reglas de palabras clave (marcadas "sin IA", máx. 3 push por ciclo). El briefing no cuenta como fallo si no hay IA: se reintenta en el próximo ciclo.
3b. **Fin de semana:** desde el cierre del viernes (17:00 Nueva York) hasta la apertura de Asia del domingo (18:00 Nueva York) no se envían notificaciones de oro, EUR/USD, Nasdaq ni S&P 500; las alertas se guardan igual en la app, que muestra un aviso 🌙 con la hora de reapertura. BTC opera 24/7 y sigue avisando; para pausarlo también crea la variable `WEEKEND_BTC_PUSH` = `0`.
4. Señales de precio sin IA: impulsos 1h vs ATR, picos de volatilidad, cercanía a PDH/PDL, movimiento diario fuera de umbral.
5. Avisa 45 min antes de eventos USD/EUR de alto impacto (NFP, CPI, FOMC).
6. Guarda los resultados en la rama `data` (un solo commit que se reemplaza, sin crecer el historial). La app los lee desde ahí.

## Puesta en marcha (≈10 min)
1. **Clave de Gemini (gratis):** https://aistudio.google.com/apikey → *Create API key*.
2. **ntfy:** instala la app *ntfy* en Android y suscríbete a un tema largo e inventado, p. ej. `mercado-x7k2q9-nosfe`. Quien conozca el nombre puede leer tus alertas: no lo compartas.
3. **Secrets del repo:** Settings → Secrets and variables → Actions → *New repository secret*:
   - `GEMINI_API_KEY` = tu clave
   - **IAs de respaldo (gratis, recomendadas):** `GROQ_API_KEY` (console.groq.com/keys), `OPENROUTER_API_KEY` (openrouter.ai/keys, usa modelos `:free`), `CEREBRAS_API_KEY` (cloud.cerebras.ai), `MISTRAL_API_KEY` (console.mistral.ai, plan gratuito Experiment), `NVIDIA_API_KEY` (build.nvidia.com). Solo se usan las que tengan clave. Orden y modelos opcionales en *Variables*: `LLM_ORDER` (por defecto `gemini,groq,cerebras,mistral,nvidia,openrouter`), `GEMINI_MODEL`, `GROQ_MODEL`, `CEREBRAS_MODEL`, `MISTRAL_MODEL`, `NVIDIA_MODEL`, `OPENROUTER_MODEL`.
   - `NTFY_TOPIC` = el tema del paso 2
   - (opcional) `TWELVEDATA_KEY` = clave gratis de twelvedata.com, respaldo de precios
4. **Pages:** Settings → Pages → Source: *Deploy from a branch* → rama `main`, carpeta `/docs`.
5. **Primera ejecución:** pestaña Actions → `diagnostico` → *Run workflow* (muestra qué fuentes responden), luego `agente` → *Run workflow*.
6. **Instalar la app:** abre `https://nosfe999.github.io/agente-claudia/` en Chrome (Android) → ⋮ → *Instalar aplicación*.
7. Android: Ajustes → Batería → ntfy → *Sin restricciones*, para que lleguen las alertas con la pantalla apagada.

## Qué incluye la app
Alertas filtrables · mercado y calendario · sesiones y killzones ICT (con horario de verano) · calculadora de lotes · diario con estadísticas en R (se guarda solo en tu teléfono; exporta/importa copia) · chat con el analista usando tu propia clave de Gemini.

## Límites (léelos)
- **No es tiempo real.** GitHub programa el cron cada 5 min pero suele retrasarlo 5-30 min. Sirve para riesgo, noticias y contexto, no para scalping de segundos.
- GitHub pausa los cron tras 60 días sin actividad del repo; el workflow intenta reactivarse solo, pero si la app muestra "detenido", revisa Actions.
- El repo es público: tu estrategia y las alertas son visibles. Las claves están en Secrets y no se exponen.
- Yahoo/RSS son gratuitos y pueden fallar o limitar; la app muestra qué fuente falló. Oro, Nasdaq y S&P usan futuros, no tu CFD exacto.
- La IA puede equivocarse y las señales técnicas no están backtesteadas. Es información, no asesoría financiera.
- Los límites del plan gratuito de Gemini cambian; el agente usa ~1 llamada cada 9 min y cae a reglas si hay 429.

## Desarrollo
```bash
pip install -r requirements.txt pytest
pytest tests && node tests/test_lib.js
python -m agent.diagnose        # prueba las fuentes
DATA_DIR=out python -m agent.run
```
