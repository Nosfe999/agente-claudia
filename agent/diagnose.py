"""Prueba qué fuentes gratuitas responden desde la red de GitHub Actions (no necesita claves)."""
import time

from . import config, sources


def t(label, fn):
    t0 = time.time()
    try:
        res = fn()
        print(f"OK    {label:<28} {res}  ({time.time() - t0:.1f}s)")
    except Exception as e:
        print(f"FALLA {label:<28} {str(e)[:140]}")


def main():
    for asset in config.ASSETS:
        c = config.ASSETS[asset]
        t(f"yahoo {asset}", lambda c=c: f"{len(sources.yahoo_candles(c['yf'], '5d', '15m'))} velas 15m")
        if c.get("kraken"):
            t(f"kraken {asset}", lambda c=c: f"{len(sources.kraken_candles(c['kraken'], 15))} velas 15m")
        t(f"snapshot {asset}", lambda a=asset: (lambda s: f"precio {s['price']} edad {s['age_min']}min "
                                                f"{'DESACTUALIZADO' if s['stale'] else 'ok'} fuente {s['source']}")(sources.snapshot(a)))
    t("noticias", lambda: (lambda r: f"{len(r[0])} titulares, {r[1]}/{r[2]} feeds")(sources.fetch_news()))
    t("calendario", lambda: f"{len(sources.fetch_calendar())} eventos alto impacto")
    t("fear&greed", lambda: sources.fetch_fear_greed())
    from . import positioning
    t("macro DXY/10Y/VIX", lambda: positioning.macro_line(positioning.macro_snapshot()) or "sin datos")
    t("COT CFTC", lambda: (lambda r: f"{len(r['assets'])} activos"
                           f"{' · SIN DATOS: ' + ', '.join(r['missing']) if r['missing'] else ''}")(positioning.fetch_cot()))
    t("BTC funding/OI", lambda: (lambda d: f"{d['source']} funding {d['funding_8h'] * 100:+.4f}%/8h OI {d['oi_usd']}")(positioning.fetch_deriv()))
    from . import llm
    for name in llm.order():
        if llm._key(name):
            t(f"IA {name}", lambda n=name: llm.probe(n))
            if name == "nvidia" or name == "mistral":
                t(f"  modelos {name}", lambda n=name: ", ".join(llm._discover_compat(n)[:10]))
        else:
            print(f"-     IA {name:<25} sin clave (omitida)")


if __name__ == "__main__":
    main()
