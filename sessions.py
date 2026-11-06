"""Sesiones de mercado y killzones ICT, con DST correcto, mostradas en hora local."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import config

SESSIONS = {  # (zona, apertura, cierre) en hora local de cada plaza
    "Tokio": ("Asia/Tokyo", time(9, 0), time(18, 0)),
    "Londres": ("Europe/London", time(8, 0), time(17, 0)),
    "Nueva York": ("America/New_York", time(8, 0), time(17, 0)),
}
KILLZONES = {  # hora de Nueva York (referencia ICT, ajustable)
    "Asia": (time(20, 0), time(0, 0)),
    "Londres": (time(2, 0), time(5, 0)),
    "NY AM": (time(7, 0), time(10, 0)),
    "London Close": (time(10, 0), time(12, 0)),
}


def window(now, tzname, start, end):
    """(abierta, inicio, fin) de la ventana actual o de la próxima."""
    tz = ZoneInfo(tzname)
    n = now.astimezone(tz)
    for d in (-1, 0, 1):
        day = (n + timedelta(days=d)).date()
        s, e = datetime.combine(day, start, tz), datetime.combine(day, end, tz)
        if e <= s:
            e += timedelta(days=1)
        if s <= n < e:
            return True, s, e
    nxt = min(datetime.combine((n + timedelta(days=d)).date(), start, tz) for d in (0, 1, 2)
              if datetime.combine((n + timedelta(days=d)).date(), start, tz) > n)
    return False, nxt, None


def forex_open(now):
    ny = now.astimezone(ZoneInfo("America/New_York"))
    wd, hm = ny.weekday(), ny.hour * 60 + ny.minute
    if wd == 5:
        return False
    if wd == 4 and hm >= 17 * 60:
        return False
    if wd == 6 and hm < 17 * 60:
        return False
    return True


def status(now=None):
    now = now or datetime.now(timezone.utc)
    loc = ZoneInfo(config.LOCAL_TZ)

    def row(name, tzname, s, e):
        is_open, a, b = window(now, tzname, s, e)
        out = {"name": name, "open": is_open}
        if is_open:
            out["closes_local"] = b.astimezone(loc).strftime("%a %H:%M")
            out["mins_left"] = int((b - now.astimezone(b.tzinfo)).total_seconds() // 60)
        else:
            out["opens_local"] = a.astimezone(loc).strftime("%a %H:%M")
            out["mins_to_open"] = int((a - now.astimezone(a.tzinfo)).total_seconds() // 60)
        return out

    return {
        "now_local": now.astimezone(loc).strftime("%a %d/%m %H:%M"),
        "forex_open": forex_open(now),
        "sessions": [row(n, *v) for n, v in SESSIONS.items()],
        "killzones": [row(n, "America/New_York", *v) for n, v in KILLZONES.items()],
    }
