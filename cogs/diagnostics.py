import os
import sys
import time
import platform
import discord
from discord.ext import commands
from datetime import datetime, timezone
from utils.helpers import format_duration

# Try importing psutil for CPU/RAM metrics
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Native Windows fallback for memory stats if psutil is not installed
if not PSUTIL_AVAILABLE and sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("sllAvailExtendedVirtual", ctypes.c_uint64),
        ]


class Diagnostics(commands.Cog):
    """System health monitoring, hardware diagnostics, and error-handling verification."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()
        self.process = psutil.Process(os.getpid()) if PSUTIL_AVAILABLE else None

    # ------------------------------------------------------------------
    # Memory & CPU Metric Helper
    # ------------------------------------------------------------------
    def _get_hardware_metrics(self) -> tuple[str, str]:
        """Returns formatted (CPU, RAM) strings using psutil or native Windows fallback."""
        if PSUTIL_AVAILABLE and self.process:
            try:
                mem_info = self.process.memory_info()
                ram_used_mb = mem_info.rss / (1024 * 1024)
                ram_percent = self.process.memory_percent()
                cpu_percent = self.process.cpu_percent(interval=0.1)

                sys_ram = psutil.virtual_memory()
                total_sys_ram_gb = sys_ram.total / (1024 ** 3)
                sys_ram_percent = sys_ram.percent

                ram_str = f"`{ram_used_mb:.2f} MB` ({ram_percent:.1f}% bot | {sys_ram_percent:.1f}% sys of {total_sys_ram_gb:.1f} GB)"
                cpu_str = f"`{cpu_percent:.1f}%` (Bot process)"
                return cpu_str, ram_str
            except Exception:
                pass

        # Windows Native Fallback
        if sys.platform == "win32":
            try:
                # Process Memory
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                pmc = PROCESS_MEMORY_COUNTERS_EX()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
                ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
                ram_used_mb = pmc.WorkingSetSize / (1024 * 1024)

                # System Memory
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                total_sys_ram_gb = stat.ullTotalPhys / (1024 ** 3)
                sys_ram_percent = stat.dwMemoryLoad

                ram_str = f"`{ram_used_mb:.2f} MB` ({sys_ram_percent}% sys of {total_sys_ram_gb:.1f} GB)"
                cpu_str = "`N/A` *(Run `pip install psutil` for CPU)*"
                return cpu_str, ram_str
            except Exception:
                pass

        return "`N/A` *(Run `pip install psutil`)*", "`N/A` *(Run `pip install psutil`)*"

    # ------------------------------------------------------------------
    # Error Pipeline Verification Command
    # ------------------------------------------------------------------
    @commands.command(name="testerror")
    @commands.has_permissions(administrator=True)
    async def test_error(self, ctx: commands.Context, *, custom_message: str = "Administrator triggered test error."):
        """
        [Admin] Manually throws an unhandled exception to verify the #error-logs dispatcher.
        """
        await ctx.send("⚡ **Throwing test exception...** Check `#error-logs` for the generated embed.")
        raise RuntimeError(f"Verified Test Exception: {custom_message}")

    # ------------------------------------------------------------------
    # System Diagnostics & Stats Command
    # ------------------------------------------------------------------
    @commands.command(name="stats", aliases=["system", "diagnostics", "botinfo"])
    async def system_stats(self, ctx: commands.Context):
        """Displays real-time hardware telemetry, bot uptime, and voice stream metrics."""
        uptime_seconds = int(time.time() - self.start_time)
        formatted_uptime = format_duration(uptime_seconds)

        # Discord Gateway Latency
        latency_ms = round(self.bot.latency * 1000)

        # Voice Stream Metrics
        active_vcs = len(self.bot.voice_clients)
        streaming_vcs = sum(1 for vc in self.bot.voice_clients if vc.is_playing())

        # Total Guilds / Members
        total_guilds = len(self.bot.guilds)
        total_members = sum(g.member_count or len(g.members) for g in self.bot.guilds)

        # CPU & RAM Metrics
        cpu_field, mem_field = self._get_hardware_metrics()

        embed = discord.Embed(
            title="⚡ PermBot System Diagnostics",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Column 1: Core Bot Stats
        embed.add_field(
            name="📊 Bot Overview",
            value=(
                f"• **Uptime:** `{formatted_uptime}`\n"
                f"• **Latency:** `{latency_ms} ms`\n"
                f"• **Active Modules:** `{len(self.bot.cogs)} loaded`\n"
                f"• **Command Prefix:** `!`"
            ),
            inline=True
        )

        # Column 2: Voice & Network Stats
        embed.add_field(
            name="🔊 Audio & Network",
            value=(
                f"• **Voice Connections:** `{active_vcs}`\n"
                f"• **Active Streams:** `{streaming_vcs}`\n"
                f"• **Guilds:** `{total_guilds:,}`\n"
                f"• **Cached Users:** `{total_members:,}`"
            ),
            inline=True
        )

        # Hardware Row
        embed.add_field(
            name="💻 Hardware & Environment",
            value=(
                f"• **CPU Usage:** {cpu_field}\n"
                f"• **RAM Usage:** {mem_field}\n"
                f"• **Python:** `v{platform.python_version()}`\n"
                f"• **Discord.py:** `v{discord.__version__}`\n"
                f"• **Platform:** `{platform.system()} {platform.release()}`"
            ),
            inline=False
        )

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name} • PID: {os.getpid()}",
            icon_url=ctx.author.display_avatar.url
        )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Diagnostics(bot))
