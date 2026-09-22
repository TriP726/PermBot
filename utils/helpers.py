import os
import sys
import re
import json
import asyncio
import logging
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta, tzinfo
from typing import Any, Union, Optional

# --- Zero-Dependency Windows-Safe Eastern Time Engine ---
class USEastern(tzinfo):
    """Pure-Python US Eastern Timezone with automated Daylight Saving Time (DST)."""
    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=-4) if self._is_dst(dt) else timedelta(hours=-5)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "EDT" if self._is_dst(dt) else "EST"

    def dst(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=1) if self._is_dst(dt) else timedelta(0)

    def _is_dst(self, dt: Optional[datetime]) -> bool:
        if dt is None:
            return False
        # Calculate 2nd Sunday in March and 1st Sunday in November
        year = dt.year
        # 2nd Sunday in March (range: March 8 to 14)
        march_8 = datetime(year, 3, 8)
        dst_start_day = 8 + (6 - march_8.weekday()) % 7
        dst_start = datetime(year, 3, dst_start_day, 2, 0)

        # 1st Sunday in November (range: Nov 1 to 7)
        nov_1 = datetime(year, 11, 1)
        dst_end_day = 1 + (6 - nov_1.weekday()) % 7
        dst_end = datetime(year, 11, dst_end_day, 2, 0)

        dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
        return dst_start <= dt_naive < dst_end

# Try zoneinfo first, fallback gracefully to USEastern on Windows
try:
    from zoneinfo import ZoneInfo
    _tz = ZoneInfo("America/New_York")
    datetime.now(_tz)  # Test if tzdata actually exists
    EASTERN_TZ = _tz
except Exception:
    EASTERN_TZ = USEastern()

# --- Standard Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
_raw_logger = logging.getLogger("PermBot")

class PermLogger:
    """Hybrid Logger supporting both log.info(...) methods and log('msg') calls."""
    def __init__(self, target_logger: logging.Logger):
        self._logger = target_logger

    def __call__(self, message: str, level: str = "INFO") -> None:
        lvl = getattr(logging, str(level).upper(), logging.INFO)
        self._logger.log(lvl, message)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def warn(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self._logger.critical(msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

log = PermLogger(_raw_logger)
logger = _raw_logger

# --- Multi-Key Token Loader ---
TOKEN_KEYS = ["DISCORD_TOKEN", "BOT_TOKEN", "TOKEN", "DISCORD_BOT_TOKEN", "APP_TOKEN"]

def load_token(key: Optional[str] = None, filepath: str = ".env") -> str:
    candidate_keys = [key] if key else TOKEN_KEYS
    for k in candidate_keys:
        if k:
            val = os.getenv(k)
            if val and val.strip():
                return val.strip().strip("'").strip('"')

    env_file = Path(filepath)
    if env_file.is_file():
        try:
            lines = env_file.read_text(encoding="utf-8-sig").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k_clean = k.strip()
                    v_clean = v.strip().strip("'").strip('"')
                    if (key and k_clean == key) or (not key and k_clean in candidate_keys):
                        if v_clean:
                            return v_clean
        except Exception:
            pass

    for fallback in ["token.txt", ".token", "token"]:
        token_txt = Path(fallback)
        if token_txt.is_file():
            try:
                content = token_txt.read_text(encoding="utf-8-sig").strip()
                if content and not content.startswith("#"):
                    return content.splitlines()[0].strip().strip("'").strip('"')
            except Exception:
                pass
    return ""

def get_env(key: str, default: Any = None, filepath: str = ".env") -> Any:
    val = os.getenv(key)
    if val is not None:
        return val
    env_file = Path(filepath)
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return v.strip().strip("'").strip('"')
        except Exception:
            pass
    return default

# --- Async UI Helpers ---
async def delete_after_delay(message: Any, delay: float = 5.0) -> None:
    try:
        await asyncio.sleep(delay)
        if hasattr(message, "delete") and callable(message.delete):
            await message.delete()
    except Exception:
        pass

# --- Synchronous Atomic JSON Persistence ---
def load_json(filepath: str, default: Any = None) -> Any:
    path = Path(filepath)
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}

def save_json(filepath: str, data: Any) -> bool:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".tmp_{os.getpid()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        return True
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False

# --- Duration Parsing ---
class ParsedDuration(timedelta):
    @property
    def seconds_total(self) -> int:
        return int(self.total_seconds())
    def __int__(self) -> int:
        return int(self.total_seconds())
    def __float__(self) -> float:
        return float(self.total_seconds())

def parse_duration(time_str: str) -> Optional[ParsedDuration]:
    if not time_str:
        return None
    clean = str(time_str).strip().lower()
    regex = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw]|hours?|mins?|minutes?|days?|secs?|seconds?|weeks?)?")
    matches = regex.findall(clean)
    if not matches:
        return None
    total_seconds = 0.0
    for val, unit in matches:
        if not val:
            continue
        v = float(val)
        u = (unit or "").lower()
        if u in ['s', 'sec', 'secs', 'second', 'seconds']:
            total_seconds += v
        elif u in ['m', 'min', 'mins', 'minute', 'minutes']:
            total_seconds += v * 60
        elif u in ['h', 'hr', 'hrs', 'hour', 'hours'] or u == '':
            total_seconds += v * 3600
        elif u in ['d', 'day', 'days']:
            total_seconds += v * 86400
        elif u in ['w', 'week', 'weeks']:
            total_seconds += v * 604800
    return ParsedDuration(seconds=int(total_seconds))

# --- UI Progress Bars & Formatting ---
def create_progress_bar(current: float, total: float, length: int = 10, filled_char: str = "█", empty_char: str = "░") -> str:
    if total <= 0:
        return empty_char * length
    progress = max(0.0, min(1.0, current / total))
    filled = int(round(progress * length))
    return filled_char * filled + empty_char * (length - filled)

def format_seconds(seconds: float) -> str:
    secs = int(max(0, seconds))
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def format_duration(seconds: float) -> str:
    return format_seconds(seconds)

def humanize_time(seconds: float) -> str:
    return format_seconds(seconds)

def format_bytes(size: Union[int, float]) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

# --- EST-First Date & Time Parsing ---
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

def parse_eastern_datetime(date_str: str, time_str: str) -> datetime:
    """Parses separate user date and time inputs in America/New_York (EST/EDT)."""
    now_et = datetime.now(EASTERN_TZ)
    d_clean = date_str.strip().lower()
    t_clean = time_str.strip().lower()

    # 1. Determine Target Date
    target_date = now_et.date()
    if d_clean in ["today", "tdy", "t"]:
        target_date = now_et.date()
    elif d_clean in ["tomorrow", "tmrw", "tmw"]:
        target_date = now_et.date() + timedelta(days=1)
    elif d_clean in WEEKDAYS:
        target_wd = WEEKDAYS[d_clean]
        days_ahead = (target_wd - now_et.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = now_et.date() + timedelta(days=days_ahead)
    else:
        parsed_d = None
        for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d", "%m-%d", "%d/%m/%Y"]:
            try:
                dt_temp = datetime.strptime(d_clean, fmt)
                year = dt_temp.year if "%Y" in fmt else now_et.year
                parsed_d = dt_temp.replace(year=year).date()
                break
            except ValueError:
                continue
        if parsed_d:
            target_date = parsed_d
        else:
            raise ValueError(f"Could not understand date: '{date_str}'")

    # 2. Determine Target Time
    target_hour, target_minute = 20, 0
    time_match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t_clean)
    if time_match:
        h = int(time_match.group(1))
        m = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and h < 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        target_hour, target_minute = h, m
    else:
        for fmt in ["%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M", "%H"]:
            try:
                t_dt = datetime.strptime(t_clean, fmt)
                target_hour, target_minute = t_dt.hour, t_dt.minute
                break
            except ValueError:
                continue

    result_et = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=target_hour,
        minute=target_minute,
        tzinfo=EASTERN_TZ
    )
    return result_et

def parse_dt_flexible(dt_val: Union[datetime, str, int, float, None]) -> datetime:
    if dt_val is None:
        return datetime.now(EASTERN_TZ)
    if isinstance(dt_val, datetime):
        return dt_val if dt_val.tzinfo is not None else dt_val.replace(tzinfo=EASTERN_TZ)
    if isinstance(dt_val, (int, float)):
        return datetime.fromtimestamp(dt_val, tz=timezone.utc).astimezone(EASTERN_TZ)
    if isinstance(dt_val, str):
        clean_str = dt_val.strip()
        try:
            dt = datetime.fromisoformat(clean_str)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=EASTERN_TZ)
        except ValueError:
            pass
    return datetime.now(EASTERN_TZ)

def generate_gcal_link(title: str, description: str, start_dt: Union[datetime, str, int, float], end_dt: Optional[Union[datetime, str, int, float]] = None) -> str:
    s_utc = parse_dt_flexible(start_dt).astimezone(timezone.utc)
    if end_dt:
        e_utc = parse_dt_flexible(end_dt).astimezone(timezone.utc)
    else:
        e_utc = s_utc + timedelta(hours=1)

    fmt = "%Y%m%dT%H%M%SZ"
    dates_param = f"{s_utc.strftime(fmt)}/{e_utc.strftime(fmt)}"

    base_url = "https://calendar.google.com/calendar/render?action=TEMPLATE"
    params = {
        "text": title or "Discord Event",
        "details": description or "",
        "dates": dates_param
    }
    return f"{base_url}&{urllib.parse.urlencode(params)}"
