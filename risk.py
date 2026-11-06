"""Calculadora de tamaño de posición. Tamaño de contrato por defecto; VERIFICA el de tu broker."""

CONTRACT = {          # unidades por 1.0 lote
    "BTC": 1.0,
    "XAUUSD": 100.0,    # 100 oz → $100 por cada $1 de movimiento
    "EURUSD": 100000.0,  # $10 por pip (0.0001)
    "NASDAQ": 1.0,      # CFD típico: $1 por punto por lote (varía por broker)
    "SP500": 1.0,
}


def position_size(balance, risk_pct, entry, stop, asset="XAUUSD", contract=None):
    if balance <= 0 or not (0 < risk_pct <= 10):
        raise ValueError("balance > 0 y riesgo entre 0 y 10 %")
    dist = abs(entry - stop)
    if dist <= 0:
        raise ValueError("entrada y stop no pueden ser iguales")
    c = contract or CONTRACT[asset]
    risk_usd = balance * risk_pct / 100
    lots = risk_usd / (dist * c)
    sign = 1 if entry > stop else -1  # largo si stop < entrada
    return {
        "side": "largo" if sign > 0 else "corto",
        "risk_usd": round(risk_usd, 2), "stop_distance": round(dist, 5),
        "lots": round(lots, 3), "contract_size": c,
        "targets": {f"{r}R": round(entry + sign * dist * r, 5) for r in (1, 2, 3)},
        "note": "Redondea hacia abajo al lote mínimo de tu broker. Verifica el valor por punto/pip.",
    }
