from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def parse_time_to_ms(time_str: str, tz: ZoneInfo = HK_TZ) -> int:
    time_str = time_str.strip()
    base = time_str
    frac = ""
    if "." in time_str:
        base, frac = time_str.split(".", 1)
    else:
        parts = time_str.rsplit(":", 1)
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].count(":") == 2:
            base, frac = parts
    dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    if frac:
        ms = int(frac[:3].ljust(3, "0"))
        dt = dt.replace(microsecond=ms * 1000)
    return int(dt.replace(tzinfo=tz).timestamp() * 1000)


def trading_day_from_ts(ts_ms: int, tz: ZoneInfo = HK_TZ) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    return dt.strftime("%Y%m%d")


def ensure_symbol_prefix(code: str, market: str) -> str:
    code = code.strip()
    if "." in code:
        return code
    return f"{market}.{code}"
