# Mercado Agent

Agente 24/7 que vigila BTC, XAUUSD, EURUSD, NASDAQ y SP500, y te avisa en Android cuando detecta riesgo de caída o posible subida.

## Qué hace
- **Noticias** (cada 10 min): Google News + CoinDesk, Cointelegraph, Fed y FXStreet. Claude Haiku filtra y clasifica (activo, dirección, severidad 1-5, confianza). Ej.: "Riot vende 8,962 BTC" → 🔴 BTC bajista 4/5.
- **Precio** (cada 5 min, sin IA): impulsos 1h vs ATR, picos de volatilidad, cercanía a PDH/PDL, movimiento diario fuera de umbral.
- **Calendario**: aviso 30 min antes de eventos de alto impacto USD/EUR (NFP, CPI, FOMC…).
- **Briefing** lun-vie 07:30 hora local (Claude Sonnet).
- **App PWA**: alertas, mercado, sesiones/killzones, calculadora de riesgo, diario con estadísticas en R, chat con el analista (con búsqueda web).

## Confiabilidad
- Deduplicación de titulares y cooldown por señal (sin spam).
- Si la IA falla, el titular se reintenta en el siguiente ciclo.
- Datos con >90-180 min de antigüedad se marcan "desactualizados" y no generan señales.
- Si un trabajo falla 3 veces seguidas, recibes un push de aviso.
- Confianza <50 % baja un punto la severidad.
- Es información, no asesoría financiera: la IA puede equivocarse. Verifica antes de operar.

## Puesta en marcha (local)
```bash
pip install -r requirements.txt
cp .env.example .env     # edita ANTHROPIC_API_KEY, NTFY_TOPIC y API_TOKEN
python server.py         # http://localhost:8000
```
Pruebas: `pip install pytest httpx && pytest tests`

## Notificaciones en Android
1. Instala **ntfy** (Play Store o F-Droid).
2. Suscríbete al mismo tema que pusiste en `NTFY_TOPIC` (largo y secreto: quien lo conozca puede leer tus alertas).
3. En la app web → pestaña **Analista** → "Enviar notificación de prueba".
4. Ajustes de Android → batería → ntfy → "Sin restricciones".

## Instalar como app
Abre la URL de tu servidor en Chrome (Android) → menú ⋮ → **Instalar aplicación**. Al abrir, pega tu `API_TOKEN`.

## Dejarlo corriendo 24/7
Necesita un servidor siempre encendido (tu PC no basta si se apaga). Opciones:
- **VPS barato** (~$5/mes): `docker build -t mercado . && docker run -d --restart=always -p 8000:8000 --env-file .env -v mercado-data:/app/data mercado`
- **Render / Railway / Fly.io**: usa el `Dockerfile`, define las variables de `.env` y monta un volumen en `/app/data`.
- HTTPS es obligatorio para instalar la PWA fuera de localhost (Caddy o el proxy de la plataforma).
- **Siempre define `API_TOKEN`** si el servidor es público.

## Ajustes (`.env` / `config.py`)
`MIN_SEVERITY` (3 = recomendado; 4 = solo lo fuerte), `NEWS_EVERY_MIN`, `PRICE_EVERY_MIN`, umbrales diarios por activo y fuentes en `config.py`.

## Límites conocidos
- Yahoo Finance/RSS son gratuitos y pueden retrasarse o cambiar; NASDAQ/SP500/oro usan futuros (NQ=F, ES=F, GC=F), no tu CFD exacto.
- El tamaño de contrato en la calculadora es estándar; verifica el de tu broker.
- Siguiente paso recomendado: backtest de las señales técnicas antes de confiar en ellas, y añadir datos de ETF flows / on-chain (APIs de pago).
