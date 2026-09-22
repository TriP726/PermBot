import os
from pathlib import Path

HELPERS_CODE = '''import os
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
    regex = re.compile(r"(\\d+(?:\\.\\d+)?)\\s*([smhdw]|hours?|mins?|minutes?|days?|secs?|seconds?|weeks?)?")
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
    time_match = re.match(r"^(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?$", t_clean)
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
'''

EVENTS_CODE = '''import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import uuid

from utils.helpers import load_json, save_json, generate_gcal_link, parse_eastern_datetime, parse_duration, parse_dt_flexible, EASTERN_TZ

EVENTS_FILE = "data/events.json"

class EventRSVPView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)
        self.event_id = event_id

    async def handle_rsvp(self, interaction: discord.Interaction, status: str):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        if self.event_id not in events:
            return await interaction.response.send_message("❌ This event no longer exists.", ephemeral=True)

        event = events[self.event_id]
        user_id = str(interaction.user.id)

        for st in ["going", "maybe", "declined"]:
            if user_id in event.get(st, []):
                event[st].remove(user_id)

        event.setdefault(status, []).append(user_id)
        save_json(EVENTS_FILE, data)

        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name.startswith("✅ Going"):
                embed.set_field_at(i, name=f"✅ Going ({len(event.get('going', []))})", value=", ".join(f"<@{u}>" for u in event.get('going', [])) or "None", inline=True)
            elif field.name.startswith("❔ Maybe"):
                embed.set_field_at(i, name=f"❔ Maybe ({len(event.get('maybe', []))})", value=", ".join(f"<@{u}>" for u in event.get('maybe', [])) or "None", inline=True)
            elif field.name.startswith("❌ Declined"):
                embed.set_field_at(i, name=f"❌ Declined ({len(event.get('declined', []))})", value=", ".join(f"<@{u}>" for u in event.get('declined', [])) or "None", inline=True)

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Going", style=discord.ButtonStyle.success, custom_id="rsvp_going", emoji="✅")
    async def going_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "going")

    @discord.ui.button(label="Maybe", style=discord.ButtonStyle.secondary, custom_id="rsvp_maybe", emoji="❔")
    async def maybe_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "maybe")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="rsvp_decline", emoji="❌")
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "declined")

class EventDetailsModal(discord.ui.Modal, title="Create Server Event (USA EST)"):
    title_input = discord.ui.TextInput(
        label="Event Title",
        placeholder="Clan Raid / Oil Rig / Game Night",
        max_length=100,
        required=True
    )
    desc_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Brief details, kits needed, voice channel...",
        max_length=1000,
        required=False
    )
    date_input = discord.ui.TextInput(
        label="Date",
        placeholder="Today, Tomorrow, Friday, or MM/DD/YYYY",
        default="Today",
        max_length=20,
        required=True
    )
    time_input = discord.ui.TextInput(
        label="Time (Eastern Standard Time / EST)",
        placeholder="8:00 PM, 7:30pm, 19:00",
        default="8:00 PM",
        max_length=15,
        required=True
    )
    duration_input = discord.ui.TextInput(
        label="Duration",
        placeholder="2 hours, 90 mins, 1.5h",
        default="2 hours",
        max_length=15,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_et = parse_eastern_datetime(self.date_input.value, self.time_input.value)
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ **Invalid Date/Time:** {e}\\n\\n"
                "**Examples:**\\n"
                "• Date: `Today`, `Tomorrow`, `Friday`, `10/25/2026`\\n"
                "• Time: `8:00 PM`, `7:30pm`, `19:00` (EST/EDT)",
                ephemeral=True
            )

        dur_parsed = parse_duration(self.duration_input.value)
        duration_td = dur_parsed if dur_parsed else timedelta(hours=2)
        end_et = start_et + duration_td

        event_id = str(uuid.uuid4())[:8]
        title = self.title_input.value.strip()
        desc = self.desc_input.value.strip() or "No description provided."

        gcal_url = generate_gcal_link(title, desc, start_et, end_et)

        data = load_json(EVENTS_FILE, {"events": {}})
        data.setdefault("events", {})[event_id] = {
            "id": event_id,
            "guild_id": interaction.guild_id,
            "creator_id": interaction.user.id,
            "title": title,
            "description": desc,
            "start_iso": start_et.isoformat(),
            "end_iso": end_et.isoformat(),
            "going": [str(interaction.user.id)],
            "maybe": [],
            "declined": []
        }
        save_json(EVENTS_FILE, data)

        ts = int(start_et.timestamp())
        est_formatted = start_et.strftime("%a, %b %d @ %I:%M %p %Z")
        dur_str = f"{int(duration_td.total_seconds() // 3600)}h {(int(duration_td.total_seconds() % 3600) // 60)}m".replace(" 0m", "").strip()

        embed = discord.Embed(title=f"📅 {title}", description=desc, color=0x3498db)
        embed.add_field(name="⏰ Start Time (Local)", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
        embed.add_field(name="🇺🇸 Eastern Time", value=f"`{est_formatted}`", inline=True)
        embed.add_field(name="⏳ Duration", value=f"`{dur_str or '2h'}`", inline=True)
        embed.add_field(name="✅ Going (1)", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="❔ Maybe (0)", value="None", inline=True)
        embed.add_field(name="❌ Declined (0)", value="None", inline=True)
        embed.add_field(name="🔗 Add to Calendar", value=f"[Add to Google Calendar]({gcal_url})", inline=False)
        embed.set_footer(text=f"Event ID: {event_id} • Created by {interaction.user.display_name}")

        view = EventRSVPView(event_id=event_id)
        await interaction.response.send_message(embed=embed, view=view)

class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        data = load_json(EVENTS_FILE, {"events": {}})
        for event_id in data.get("events", {}):
            self.bot.add_view(EventRSVPView(event_id))

    @commands.hybrid_group(name="event", description="Manage server events in US Eastern Time.", fallback="help")
    @commands.guild_only()
    async def event_group(self, ctx: commands.Context):
        embed = discord.Embed(title="📅 US Eastern Event Manager", color=0x3498db)
        embed.add_field(name="`!event create` / `/event create`", value="Opens the EST event setup form.", inline=False)
        embed.add_field(name="`!event list` / `/event list`", value="Lists all scheduled events.", inline=False)
        embed.add_field(name="`!event delete <id>` / `/event delete <id>`", value="Deletes a scheduled event.", inline=False)
        await ctx.reply(embed=embed, ephemeral=True)

    @event_group.command(name="create", description="Create a new server event (EST timezone).")
    @commands.guild_only()
    async def event_create(self, ctx: commands.Context):
        if ctx.interaction:
            await ctx.interaction.response.send_modal(EventDetailsModal())
        else:
            view = discord.ui.View()
            btn = discord.ui.Button(label="Open Event Form (EST)", style=discord.ButtonStyle.primary, emoji="📝")

            async def btn_callback(interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
                await interaction.response.send_modal(EventDetailsModal())

            btn.callback = btn_callback
            view.add_item(btn)
            await ctx.reply("Click below to schedule your event in **US Eastern Time (EST)**:", view=view)

    @event_group.command(name="list", description="List all scheduled server events.")
    @commands.guild_only()
    async def event_list(self, ctx: commands.Context):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        guild_events = [e for e in events.values() if e.get("guild_id") == ctx.guild.id]

        if not guild_events:
            return await ctx.reply("📅 No events are currently scheduled.", ephemeral=True)

        embed = discord.Embed(title=f"📅 Scheduled Events ({len(guild_events)})", color=0x3498db)
        for ev in guild_events[:10]:
            st = parse_dt_flexible(ev.get("start_iso"))
            ts = int(st.timestamp())
            est_str = st.strftime("%b %d @ %I:%M %p %Z")
            going_cnt = len(ev.get("going", []))
            embed.add_field(
                name=f"{ev['title']} (ID: `{ev['id']}`)",
                value=f"⏰ <t:{ts}:F> (<t:{ts}:R>)\\n🇺🇸 **EST:** `{est_str}`\\n👥 **Going:** {going_cnt}\\n📝 {ev['description'][:100]}",
                inline=False
            )
        await ctx.reply(embed=embed)

    @event_group.command(name="delete", description="Delete an event by ID.")
    @commands.guild_only()
    async def event_delete(self, ctx: commands.Context, event_id: str):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        if event_id not in events:
            return await ctx.reply(f"❌ No event found with ID `{event_id}`.", ephemeral=True)

        ev = events[event_id]
        is_creator = ev.get("creator_id") == ctx.author.id
        is_admin = ctx.author.guild_permissions.administrator

        if not (is_creator or is_admin):
            return await ctx.reply("❌ You can only delete events you created unless you are an Administrator.", ephemeral=True)

        del events[event_id]
        save_json(EVENTS_FILE, data)
        await ctx.reply(f"✅ Successfully deleted event `{event_id}`.")

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
'''

def main():
    print("Writing utils/helpers.py with Windows-safe USEastern engine...")
    Path("utils/helpers.py").write_text(HELPERS_CODE, encoding="utf-8")

    print("Writing cogs/events.py...")
    Path("cogs/events.py").write_text(EVENTS_CODE, encoding="utf-8")

    print("✅ Files written cleanly without external dependencies.")

if __name__ == "__main__":
    main()
