# Mercado Agent (100 % gratis)

Agente que vigila **BTC, XAUUSD, EURUSD, NASDAQ y SP500** y te avisa en el celular cuando detecta riesgo de caída o posible subida. Sin servidor, sin tarjeta, sin pagar.

| Pieza | Servicio gratuito |
|---|---|
| Ejecución (cada ~5-10 min) | GitHub Actions (ilimitado en repos públicos) |
| IA que clasifica noticias y escribe el briefing | Google Gemini (plan gratuito de AI Studio) |
| Notificaciones en Android | ntfy.sh |
| App instalable | GitHub Pages (PWA) |
| Datos | Yahoo Finance, Kraken, Google News/RSS, Forex Factory, alternative.me |

## Cómo funciona
1. El workflow `agente` corre cada 5 min: precios → calendario → noticias (cada ~9 min) → briefing (lun-vie 07:30 hora local).
2. Las noticias nuevas las clasifica Gemini (activo, dirección, severidad 1-5, confianza). Ej.: "Riot vende 8,962 BTC" → 🔴 BTC bajista 4/5.
3. Si Gemini falla o se acaba la cuota, usa reglas de palabras clave (marcadas "sin IA", máx. 3 push por ciclo).
4. Señales de precio sin IA: impulsos 1h vs ATR, picos de volatilidad, cercanía a PDH/PDL, movimiento diario fuera de umbral.
5. Avisa 45 min antes de eventos USD/EUR de alto impacto (NFP, CPI, FOMC).
6. Guarda los resultados en la rama `data` (un solo commit que se reemplaza, sin crecer el historial). La app los lee desde ahí.

## Puesta en marcha (≈10 min)
1. **Clave de Gemini (gratis):** https://aistudio.google.com/apikey → *Create API key*.
2. **ntfy:** instala la app *ntfy* en Android y suscríbete a un tema largo e inventado, p. ej. `mercado-x7k2q9-nosfe`. Quien conozca el nombre puede leer tus alertas: no lo compartas.
3. **Secrets del repo:** Settings → Secrets and variables → Actions → *New repository secret*:
   - `GEMINI_API_KEY` = tu clave
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
