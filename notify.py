import logging

import requests

import config

log = logging.getLogger("notify")


def push(title, message, priority=3, tags=None, click=None):
    """Envía push vía ntfy (JSON, soporta tildes/emojis). Devuelve True si se entregó al servidor."""
    if not config.NTFY_TOPIC:
        log.warning("NTFY_TOPIC vacío; no se envía: %s", title)
        return False
    payload = {"topic": config.NTFY_TOPIC, "title": title[:200], "message": message[:1000],
               "priority": max(1, min(5, priority)), "tags": tags or []}
    if click and click.startswith("http"):
        payload["click"] = click
    headers = {"Authorization": f"Bearer {config.NTFY_TOKEN}"} if config.NTFY_TOKEN else {}
    try:
        r = requests.post(config.NTFY_SERVER, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("ntfy falló: %s", e)
        return False
