import logging

import requests

from . import config

log = logging.getLogger("notify")


def push(title, message, priority=3, tags=None, click=None):
    """Push gratuito vía ntfy.sh. Devuelve True si el servidor lo aceptó."""
    if not config.NTFY_TOPIC:
        log.warning("NTFY_TOPIC vacío; no se envía: %s", title)
        return False
    payload = {"topic": config.NTFY_TOPIC, "title": title[:200], "message": message[:3000],
               "priority": max(1, min(5, int(priority))), "tags": tags or []}
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
