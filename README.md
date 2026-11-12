# Mercado Agent (100 % gratis)

Agente que vigila **BTC, XAUUSD, EURUSD, NASDAQ y SP500** y te avisa en el celular cuando detecta riesgo de caída o posible subida. Sin servidor, sin tarjeta, sin pagar.

| Pieza | Servicio gratuito |
|---|---|
| Ejecución (cada ~5-10 min) | GitHub Actions (ilimitado en repos públicos) |
| IA que clasifica noticias y escribe el briefing | Cadena de IAs gratuitas con respaldo automático: Gemini → Groq → Cerebras → Mistral → NVIDIA → OpenRouter |
| Notificaciones en Android | ntfy.sh |
| App instalable | GitHub Pages (PWA) |
| Datos | Yahoo Finance, Kraken, Google News/RSS, Forex Factory, alternative.me, CFTC (COT), OKX/Deribit (funding, OI) |

## Resumen por killzone (modo por defecto)
Solo **3 notificaciones al día**, 1 h antes de cada killzone (hora Nueva York, con horario de verano):

| Aviso | Hora NY | Killzone |
|---|---|---|
| 🌏 Asia | 19:00 | 20:00–00:00 |
| 🇬🇧 Londres | 01:00 | 02:00–05:00 |
| 🇺🇸 Nueva York | 06:00 | 07:00–10:00 |

Cada aviso trae noticias clave, sesgo por activo con nivel clave/objetivo/invalidación y la agenda macro. Los niveles salen de datos reales: PDH/PDL, rango 24h, rango de Asia/Londres/NY y FVG sin rellenar.

**Noticias + gráfico (confluencia).** Antes de cada aviso se analiza la estructura en 4h de cada activo (`agent/structure.py`, sin IA): tendencia por swings, **cuñas** descendentes/ascendentes con ruptura y reteste, zonas de resistencia/soporte y objetivo de la cuña (altura de la cuña proyectada). Se cruza con el sesgo neto de las noticias de las últimas 10 h:
- ✅ **a favor**: técnica y noticias coinciden → sesgo con más convicción.
- ⚠️ **en conflicto**: se oponen → sesgo neutral y "esperar confirmación".
- Solo una de las dos señales → se marca "técnica" o "noticias".

La IA solo redacta con esos datos; no inventa niveles. La detección de patrones es automática y aproximada: no sustituye mirar el gráfico. El resto del día el agente sigue guardando alertas en la app, pero **sin push**.
- Si ninguna IA responde antes del minuto 30 de la hora, se envía un resumen sin IA (marcado).
- Con el mercado cerrado (vie 17:00 → dom 18:00 NY) no se envía.
- Los fallos del propio agente (3 seguidos) sí avisan, máx. 1 vez cada 6 h.
- Para volver al modo anterior (push por noticia/señal + briefing), crea la variable `DIGEST_ONLY` = `0`.

## Plan 1:2 y aciertos (backtest en vivo)
Cada sesgo alcista/bajista del resumen trae un **plan**: entrada (precio al enviar el aviso), **SL** detrás del nivel estructural más cercano contrario al sesgo (+0.25 ATR; debe quedar entre 1.5 y 8 ATR15, si no 4 ATR15) y **TP = entrada ± 2× el riesgo (1:2)**. Los neutrales no tienen plan. Las cifras las calcula el código; la IA solo las copia (si las omite, se añaden solas).

Al terminar la killzone se verifica con velas de 15 min (`agent/scorecard.py`): **acierto** = toca el TP antes que el SL (+2R) · **fallo** = toca el SL antes (−1R), o ambos en la misma vela · **sin movimiento** = ninguno, cuenta su retorno al cierre en R. El siguiente resumen incluye el resultado de la última sesión y la línea `🎯 Aciertos: hoy · sem · mes · año`.

La app (pestaña 🎯 Aciertos) muestra % de aciertos y R medio por **día, semana, mes y año** (acciones: aciertos/(aciertos+fallos)), el acumulado con intervalo de confianza 95 %, el desglose por activo, tipo de señal y sesión, los planes en curso y los últimos resultados con entrada/SL/TP. Con 1:2 el punto de equilibrio es **33,3 %** y el azar también da ≈ 33 %; con menos de 30 resueltos no hay conclusión. El día es la fecha local en que se emitió el sesgo. Datos en `scorecard.json` (rama `data`).

## Posicionamiento y régimen (solo contexto)
`agent/positioning.py`, todo gratis y con cada fuente aislada (si una falla, la app muestra "no disponible" y el agente sigue):
- **Régimen y macro:** VIX, DXY y rendimiento 10Y (Yahoo, cada 30 min). Régimen `risk-off` (VIX ≥ 22 o +10 % en el día), `risk-on` (VIX < 16 o −8 %), si no `mixto`. Viento macro por activo (dólar, rendimientos, VIX).
- **COT (CFTC, semanal):** posición neta de especuladores (informe Legacy) en oro, EUR, Nasdaq, S&P y bitcoin CME, como % del open interest, cambio semanal y percentil de 52 semanas; extremos (p ≥ 90 o ≤ 10) se marcan como riesgo de liquidación o de squeeze. Se publica los viernes con datos del martes. Dataset configurable con la variable `COT_DATASET` (por defecto `6dca-aqww`).
- **BTC:** funding de perpetuos y open interest (OKX; Deribit de respaldo) con cambio de OI en 24 h. No hay datos de liquidaciones gratuitos y fiables.

Aparece en el resumen (sección CONTEXTO) y en la pestaña Mercado. **No cambia el sesgo ni el plan 1:2**: así las estadísticas de aciertos siguen siendo comparables. Cada plan guarda el régimen y si el viento macro iba a favor o en contra; la pestaña Aciertos los desglosa para que veas con tu propio historial si aportan algo. Si alguna fuente no responde desde GitHub, ejecuta `diagnostico` para ver cuál.

## Cómo funciona
1. El workflow `agente` corre cada 5 min: precios → calendario → noticias (cada ~9 min) → resumen de killzone (o, con `DIGEST_ONLY=0`, briefing lun-vie 07:30 hora local).
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
