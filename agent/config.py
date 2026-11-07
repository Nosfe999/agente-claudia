import os


def _s(name, default=""):
    return os.getenv(name) or default


def _i(name, default):
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


DATA_DIR = _s("DATA_DIR", "data-out")
GEMINI_API_KEY = _s("GEMINI_API_KEY")
GEMINI_MODEL = _s("GEMINI_MODEL")          # vacío = autodescubre un modelo Flash gratuito
# IAs de respaldo (todas con plan gratuito). Solo se usan las que tengan clave; modelo vacío = autodescubre.
GROQ_API_KEY = _s("GROQ_API_KEY")
GROQ_MODEL = _s("GROQ_MODEL")
OPENROUTER_API_KEY = _s("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _s("OPENROUTER_MODEL")  # vacío = un modelo ":free"
CEREBRAS_API_KEY = _s("CEREBRAS_API_KEY")
CEREBRAS_MODEL = _s("CEREBRAS_MODEL")
MISTRAL_API_KEY = _s("MISTRAL_API_KEY")
MISTRAL_MODEL = _s("MISTRAL_MODEL")
NVIDIA_API_KEY = _s("NVIDIA_API_KEY")
NVIDIA_MODEL = _s("NVIDIA_MODEL")
LLM_ORDER = _s("LLM_ORDER", "gemini,groq,cerebras,mistral,nvidia,openrouter")  # orden de prueba
LLM_TIMEOUT_S = _i("LLM_TIMEOUT_S", 30)       # por proveedor; así la cadena completa cabe en el ciclo
TWELVEDATA_KEY = _s("TWELVEDATA_KEY")      # opcional: respaldo de precios
NTFY_SERVER = _s("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = _s("NTFY_TOPIC")
NTFY_TOKEN = _s("NTFY_TOKEN")

LOCAL_TZ = _s("LOCAL_TZ", "America/Asuncion")
MIN_SEVERITY = _i("MIN_SEVERITY", 3)            # mínimo para enviar push (1-5)
NEWS_EVERY_MIN = _i("NEWS_EVERY_MIN", 9)        # el cron corre cada ~5-10 min
BRIEFING_HOUR = _i("BRIEFING_HOUR", 7)          # hora local, lun-vie, desde :30
SIGNAL_COOLDOWN_MIN = _i("SIGNAL_COOLDOWN_MIN", 90)
MAX_NEWS_PER_CYCLE = _i("MAX_NEWS_PER_CYCLE", 25)
NEWS_MAX_AGE_H = _i("NEWS_MAX_AGE_H", 8)
CALENDAR_WARN_MIN = _i("CALENDAR_WARN_MIN", 45)
# Cierre semanal de futuros/forex: viernes 17:00 Nueva York -> domingo 18:00 Nueva York (apertura de Asia).
# En ese lapso no se envían push de oro/EURUSD/Nasdaq/S&P (solo se guardan en la app). BTC opera 24/7.
MARKET_TZ = "America/New_York"
CLOSE_WEEKDAY, CLOSE_HOUR = 4, 17               # viernes 17:00
OPEN_WEEKDAY, OPEN_HOUR = 6, 18                 # domingo 18:00
WEEKEND_BTC_PUSH = _i("WEEKEND_BTC_PUSH", 1)    # 0 = pausar también BTC el fin de semana
MAX_FALLBACK_PUSH = 3                            # tope de push por ciclo sin IA
LLM_BACKOFF_MIN = 30

# yf = Yahoo Finance (futuros para oro/índices: casi 24h, cercano a un CFD)
# kraken = par público de Kraken · td = símbolo de Twelve Data (respaldo opcional)
ASSETS = {
    "BTC": {
        "name": "Bitcoin", "yf": "BTC-USD", "kraken": "XBTUSD", "td": "BTC/USD",
        "daily_pct": 4.0, "h24": True,
        "queries": ["bitcoin", "bitcoin miners sell BTC", "bitcoin ETF flows",
                    "bitcoin treasury company", "crypto exchange hack OR liquidations"],
    },
    "XAUUSD": {
        "name": "Oro", "yf": "GC=F", "td": "XAU/USD", "daily_pct": 1.5, "h24": False,
        "queries": ["gold price", "central banks gold buying", "gold safe haven"],
    },
    "EURUSD": {
        "name": "EUR/USD", "yf": "EURUSD=X", "td": "EUR/USD", "daily_pct": 0.7, "h24": False,
        "queries": ["EUR/USD", "ECB interest rates euro", "US dollar index DXY"],
    },
    "NASDAQ": {
        "name": "Nasdaq 100", "yf": "NQ=F", "td": "NDX", "daily_pct": 2.0, "h24": False,
        "queries": ["Nasdaq 100", "big tech earnings", "Nvidia OR Microsoft OR Apple stock"],
    },
    "SP500": {
        "name": "S&P 500", "yf": "ES=F", "td": "SPX", "daily_pct": 1.5, "h24": False,
        "queries": ["S&P 500", "Wall Street stocks Fed", "US stocks selloff OR rally"],
    },
}

MACRO_QUERIES = ["Federal Reserve", "US CPI inflation", "nonfarm payrolls", "Trump tariffs",
                 "oil prices OPEC", "geopolitical risk markets"]

EXTRA_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Fed": "https://www.federalreserve.gov/feeds/press_all.xml",
}
