import discord
from discord.ext import commands
import asyncio
import traceback
import signal
import sys
import time
from datetime import datetime, timezone
from utils.helpers import load_json, save_json, log

CATEGORY_ONLINE = "🟢・PermBot"
CATEGORY_OFFLINE = "🔴・PermBot"
ERROR_CHANNEL_NAME = "error-logs"
RATELIMIT_FILE = "data/status_ratelimit.json"

# Discord's strict bucket: 2 renames per 10 minutes (600 seconds)
RENAME_WINDOW_SECONDS = 600
RENAME_MAX_ATTEMPTS = 2


class StatusManager(commands.Cog):
    """
    Manages the dynamic status category, nested #error-logs channel,
    and persistent rate-limit tracking across bot restarts.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._shutdown_registered = False
        self._error_channels: dict[int, discord.TextChannel] = {}

    def _get_rename_history(self, guild_id: int) -> list[float]:
        """Loads persistent rename timestamps from disk."""
        data = load_json(RATELIMIT_FILE, default={})
        return data.get(str(guild_id), [])

    def _record_rename(self, guild_id: int):
        """Records a successful rename timestamp to disk."""
        data = load_json(RATELIMIT_FILE, default={})
        history = data.get(str(guild_id), [])
        now = time.time()
        # Keep only timestamps within the 10-minute window
        history = [ts for ts in history if now - ts < RENAME_WINDOW_SECONDS]
        history.append(now)
        data[str(guild_id)] = history
        save_json(RATELIMIT_FILE, data)

    def _can_rename(self, guild_id: int) -> tuple[bool, int]:
        """Checks if a category can be renamed without hitting Discord 429."""
        history = self._get_rename_history(guild_id)
        now = time.time()
        active = [ts for ts in history if now - ts < RENAME_WINDOW_SECONDS]
        if len(active) >= RENAME_MAX_ATTEMPTS:
            oldest = min(active)
            retry_after = int(RENAME_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(1, retry_after)
        return True, 0

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await self._ensure_category_and_channel(guild, online=True)
            except Exception as e:
                log.warning(f"[StatusManager] Setup notice for '{guild.name}': {e}")

        if not self._shutdown_registered:
            self._register_shutdown_hooks()
            self._shutdown_registered = True

    async def _ensure_category_and_channel(self, guild: discord.Guild, online: bool):
        desired_name = CATEGORY_ONLINE if online else CATEGORY_OFFLINE

        # Find existing status category
        category = discord.utils.find(
            lambda c: c.name in (CATEGORY_ONLINE, CATEGORY_OFFLINE),
            guild.categories
        )

        if category is None:
            try:
                category = await guild.create_category(desired_name, reason="PermBot status category")
                self._record_rename(guild.id)
            except discord.Forbidden:
                return
            except discord.HTTPException:
                return
        else:
            # Only rename if name differs AND not rate-limited
            if category.name != desired_name:
                can_edit, wait_time = self._can_rename(guild.id)
                if can_edit:
                    try:
                        await category.edit(name=desired_name, reason="PermBot status update")
                        self._record_rename(guild.id)
                    except discord.HTTPException as e:
                        if e.status == 429:
                            log.warning(f"[StatusManager] Category rename skipped (Discord 429 rate limit active).")
                else:
                    log.info(f"[StatusManager] Skipping category rename in '{guild.name}' (Rate limit cooldown: {wait_time}s remaining).")

        # Locate or create nested #error-logs
        error_channel = discord.utils.get(category.text_channels, name=ERROR_CHANNEL_NAME)
        if error_channel is None:
            error_channel = discord.utils.get(guild.text_channels, name=ERROR_CHANNEL_NAME)
            if error_channel:
                try:
                    await error_channel.edit(category=category, reason="Re-nest error-logs")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if error_channel is None:
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True),
                }
                error_channel = await guild.create_text_channel(
                    ERROR_CHANNEL_NAME,
                    category=category,
                    overwrites=overwrites,
                    reason="PermBot error logging channel"
                )
            except (discord.Forbidden, discord.HTTPException):
                return

        self._error_channels[guild.id] = error_channel

    def _register_shutdown_hooks(self):
        loop = asyncio.get_event_loop()

        def _handle_signal():
            asyncio.create_task(self._graceful_shutdown())

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, RuntimeError):
            def _win_signal(signum, frame):
                asyncio.run_coroutine_threadsafe(self._graceful_shutdown(), loop)
            try:
                signal.signal(signal.SIGINT, _win_signal)
                signal.signal(signal.SIGTERM, _win_signal)
            except Exception:
                pass

    async def _graceful_shutdown(self):
        for guild in self.bot.guilds:
            category = discord.utils.find(
                lambda c: c.name in (CATEGORY_ONLINE, CATEGORY_OFFLINE),
                guild.categories
            )
            if category and category.name != CATEGORY_OFFLINE:
                can_edit, _ = self._can_rename(guild.id)
                if can_edit:
                    try:
                        await category.edit(name=CATEGORY_OFFLINE, reason="PermBot shutdown")
                        self._record_rename(guild.id)
                    except discord.HTTPException:
                        pass
        await self.bot.close()

    @commands.hybrid_command(name="shutdown", description="Gracefully shuts down PermBot (Admin only).")
    @commands.has_permissions(administrator=True)
    async def shutdown(self, ctx: commands.Context):
        await ctx.send("🔴 Marking category offline and stopping bot...", ephemeral=True)
        await self._graceful_shutdown()
        sys.exit(0)

    # ------------------------------------------------------------------
    # Error Handlers
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                description=f"⏳ **Cooldown:** Please wait `{error.retry_after:.1f}s` before using `{ctx.prefix}{ctx.command.name}` again.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=5)
            return

        if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
            embed = discord.Embed(
                description="🔒 **Access Denied:** You do not have permission to execute this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=6)
            return

        if isinstance(error, commands.BotMissingPermissions):
            perms = ", ".join(f"`{p}`" for p in error.missing_permissions)
            embed = discord.Embed(
                description=f"⚠️ **Bot Missing Permissions:** I need {perms} to execute this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            embed = discord.Embed(
                title="⚠️ Invalid Arguments",
                description=f"{error}\n\n*Type `!help {ctx.command.name}` or `/{ctx.command.name}` for syntax.*",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=10)
            return

        if isinstance(error, commands.CommandNotFound):
            return

        original = getattr(error, "original", error)
        tb_str = "".join(traceback.format_exception(type(original), original, original.__traceback__))

        user_err = discord.Embed(
            description="❌ An unexpected internal error occurred. It has been reported to `#error-logs`.",
            color=discord.Color.red()
        )
        try:
            await ctx.send(embed=user_err, ephemeral=True)
        except Exception:
            pass

        await self._dispatch_error_embed(
            guild=ctx.guild,
            title=f"Command Error: {type(original).__name__}",
            traceback_str=tb_str,
            command=ctx.command.qualified_name if ctx.command else "Unknown",
            user=ctx.author,
            channel_source=ctx.channel
        )

    @commands.Cog.listener()
    async def on_error(self, event_method: str, *args, **kwargs):
        exc_type, exc_value, exc_tb = sys.exc_info()
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        target_guild = None
        for arg in args:
            if isinstance(arg, discord.Guild):
                target_guild = arg
                break
            if hasattr(arg, "guild") and isinstance(arg.guild, discord.Guild):
                target_guild = arg.guild
                break

        guilds = [target_guild] if target_guild else self.bot.guilds
        for guild in guilds:
            await self._dispatch_error_embed(
                guild=guild,
                title=f"Event Error: {event_method}",
                traceback_str=tb_str,
                command=None,
                user=None,
                channel_source=None
            )

    async def _dispatch_error_embed(
        self,
        guild: discord.Guild | None,
        title: str,
        traceback_str: str,
        command: str | None,
        user: discord.abc.User | None,
        channel_source: discord.abc.GuildChannel | None
    ):
        if guild is None:
            return

        error_channel = self._error_channels.get(guild.id)
        if error_channel is None:
            category = discord.utils.find(
                lambda c: c.name in (CATEGORY_ONLINE, CATEGORY_OFFLINE),
                guild.categories
            )
            if category:
                error_channel = discord.utils.get(category.text_channels, name=ERROR_CHANNEL_NAME)
                if error_channel:
                    self._error_channels[guild.id] = error_channel

        if error_channel is None:
            return

        max_len = 1850
        if len(traceback_str) > max_len:
            traceback_str = traceback_str[:max_len] + "\n... [Traceback Truncated]"

        embed = discord.Embed(
            title=f"⚠️ {title}",
            description=f"```py\n{traceback_str}\n```",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        if command:
            embed.add_field(name="Command", value=f"`!{command}` / `/{command}`", inline=True)
        if user:
            embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        if channel_source:
            embed.add_field(name="Channel", value=f"{channel_source.mention}", inline=True)

        embed.set_footer(text="PermBot Global Error Handler")
        try:
            await error_channel.send(embed=embed)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusManager(bot))
