import os
import datetime
import discord
from discord.ext import commands
from typing import Optional
from utils.helpers import load_json, save_json, parse_duration, format_duration, log

WARNINGS_FILE = os.path.join("data", "warnings.json")
MODCONFIG_FILE = os.path.join("data", "modconfig.json")

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_mod_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        config = load_json(MODCONFIG_FILE)
        ch_id = config.get(str(guild.id), {}).get("mod_logs")
        if ch_id:
            return guild.get_channel(int(ch_id))
        return None

    async def log_action(self, guild: discord.Guild, embed: discord.Embed):
        ch = self._get_mod_log_channel(guild)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception as e:
                log.error(f"Failed to post to mod-log channel on guild {guild.id}: {e}")

    # ==========================================
    # AUDIT LOGGING: GHOST-PING, DELETE, EDIT
    # ==========================================
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        log_ch = self._get_mod_log_channel(message.guild)
        if not log_ch or log_ch.id == message.channel.id:
            return

        # Collect all ping targets in the deleted message
        pinged = []
        if message.mention_everyone:
            pinged.append("@everyone / @here")
        for m in message.mentions:
            if not m.bot and m.id != message.author.id:
                pinged.append(m.mention)
        for r in message.role_mentions:
            pinged.append(r.mention)

        is_ghost_ping = len(pinged) > 0

        if is_ghost_ping:
            embed = discord.Embed(
                title="🚨 Ghost Ping Detected! (Deleted Message)",
                description=f"A message containing mentions was deleted in {message.channel.mention}.",
                color=0xe74c3c,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Pinged Target(s)", value=", ".join(pinged), inline=False)
        else:
            embed = discord.Embed(
                title="🗑️ Message Deleted",
                description=f"Message deleted in {message.channel.mention}.",
                color=0xe67e22,
                timestamp=discord.utils.utcnow()
            )

        embed.set_author(name=f"{message.author} ({message.author.id})", icon_url=message.author.display_avatar.url)
        content_preview = message.clean_content if message.clean_content else "*[No text content / Attachment]*"
        if len(content_preview) > 1000:
            content_preview = content_preview[:997] + "..."
        embed.add_field(name="Deleted Content", value=content_preview, inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)

        if message.created_at:
            time_sent = discord.utils.format_dt(message.created_at, style="R")
            embed.add_field(name="Originally Sent", value=time_sent, inline=True)

        if message.attachments:
            att_list = [f"[{a.filename}]({a.url})" for a in message.attachments]
            embed.add_field(name="Attachments", value="\n".join(att_list), inline=False)

        embed.set_footer(text=f"Author ID: {message.author.id} • Message ID: {message.id}")
        await self.log_action(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return

        log_ch = self._get_mod_log_channel(before.guild)
        if not log_ch or log_ch.id == before.channel.id:
            return

        # Check for user, role, and everyone mentions in the original message
        before_user_mentions = {m.id: m for m in before.mentions if not m.bot and m.id != before.author.id}
        after_user_mentions = {m.id for m in after.mentions}
        removed_user_mentions = [m for m_id, m in before_user_mentions.items() if m_id not in after_user_mentions]

        before_role_mentions = {r.id: r for r in before.role_mentions}
        after_role_mentions = {r.id for r in after.role_mentions}
        removed_role_mentions = [r for r_id, r in before_role_mentions.items() if r_id not in after_role_mentions]

        removed_pings = []
        if before.mention_everyone and not after.mention_everyone:
            removed_pings.append("@everyone / @here")
        for m in removed_user_mentions:
            removed_pings.append(m.mention)
        for r in removed_role_mentions:
            removed_pings.append(r.mention)

        original_pings = []
        if before.mention_everyone:
            original_pings.append("@everyone / @here")
        for m in before_user_mentions.values():
            original_pings.append(m.mention)
        for r in before.role_mentions:
            original_pings.append(r.mention)

        is_edit_ghost_ping = len(original_pings) > 0

        if is_edit_ghost_ping:
            embed = discord.Embed(
                title="🚨 Ghost Ping Detected! (Edited Message)",
                description=f"A message containing mentions was edited in {before.channel.mention}.\n[Jump to Message]({after.jump_url})",
                color=0xe74c3c,
                timestamp=discord.utils.utcnow()
            )
            if removed_pings:
                embed.add_field(name="Removed Ping Target(s)", value=", ".join(removed_pings), inline=False)
            else:
                embed.add_field(name="Original Ping Target(s)", value=", ".join(original_pings), inline=False)
        else:
            embed = discord.Embed(
                title="✏️ Message Edited",
                description=f"Message edited in {before.channel.mention} • [Jump to Message]({after.jump_url})",
                color=0x3498db,
                timestamp=discord.utils.utcnow()
            )

        embed.set_author(name=f"{before.author} ({before.author.id})", icon_url=before.author.display_avatar.url)

        before_text = before.clean_content if before.clean_content else "*[Empty / Attachment]*"
        after_text = after.clean_content if after.clean_content else "*[Empty / Attachment]*"
        if len(before_text) > 900:
            before_text = before_text[:897] + "..."
        if len(after_text) > 900:
            after_text = after_text[:897] + "..."

        embed.add_field(name="Before", value=before_text, inline=False)
        embed.add_field(name="After", value=after_text, inline=False)
        embed.set_footer(text=f"Author ID: {before.author.id} • Message ID: {before.id}")
        await self.log_action(before.guild, embed)

    # ==========================================
    # MOD CONFIG
    # ==========================================
    @commands.command(name="setmodlogs")
    @commands.has_permissions(administrator=True)
    async def setmodlogs(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the moderation and audit log channel."""
        config = load_json(MODCONFIG_FILE)
        guild_id = str(ctx.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]["mod_logs"] = str(channel.id)
        save_json(MODCONFIG_FILE, config)
        await ctx.send(f"✅ Moderation logs will now be sent to {channel.mention}.")

    # ==========================================
    # SERVER VOICE MUTE / DEAFEN
    # ==========================================
    @commands.command(name="mute")
    @commands.has_permissions(administrator=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Server voice mutes a member."""
        if not member.voice or not member.voice.channel:
            return await ctx.send(f"❌ {member.mention} is not currently connected to any voice channel.")

        try:
            await member.edit(mute=True, reason=reason)
            embed = discord.Embed(
                title="🎙️ Member Server Voice Muted",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0xe67e22,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to voice mute that member.")

    @commands.command(name="unmute")
    @commands.has_permissions(administrator=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        """Unmutes a server voice muted member."""
        if not member.voice or not member.voice.channel:
            return await ctx.send(f"❌ {member.mention} is not currently connected to any voice channel.")

        try:
            await member.edit(mute=False, reason=f"Unmuted by {ctx.author}")
            embed = discord.Embed(
                title="🎙️ Member Server Voice Unmuted",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to unmute that member.")

    @commands.command(name="deaf")
    @commands.has_permissions(administrator=True)
    async def deaf(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Server voice deafens a member."""
        if not member.voice or not member.voice.channel:
            return await ctx.send(f"❌ {member.mention} is not connected to a voice channel.")

        try:
            await member.edit(deafen=True, reason=reason)
            embed = discord.Embed(
                title="🔇 Member Server Voice Deafened",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0xe67e22,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to deafen that member.")

    @commands.command(name="undeaf")
    @commands.has_permissions(administrator=True)
    async def undeaf(self, ctx: commands.Context, member: discord.Member):
        """Undeafens a server voice deafened member."""
        if not member.voice or not member.voice.channel:
            return await ctx.send(f"❌ {member.mention} is not connected to a voice channel.")

        try:
            await member.edit(deafen=False, reason=f"Undeafened by {ctx.author}")
            embed = discord.Embed(
                title="🔊 Member Server Voice Undeafened",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to undeafen that member.")

    # ==========================================
    # CHAT TIMEOUTS
    # ==========================================
    @commands.command(name="timeout")
    @commands.has_permissions(administrator=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        """Times out (chat mutes) a member for a given duration (e.g., 10m, 1h, 1d)."""
        seconds = parse_duration(duration)
        if seconds <= 0:
            return await ctx.send("❌ Invalid duration format. Use `10m`, `1h`, `1d`.")

        until_time = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(until_time, reason=reason)
            embed = discord.Embed(
                title="⏳ Member Timed Out",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Duration:** {format_duration(seconds)}\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0xe67e22,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to timeout that member.")

    @commands.command(name="untimeout")
    @commands.has_permissions(administrator=True)
    async def untimeout(self, ctx: commands.Context, member: discord.Member):
        """Removes a chat timeout from a member."""
        try:
            await member.timeout(None, reason=f"Untimeout by {ctx.author}")
            embed = discord.Embed(
                title="⏳ Member Timeout Removed",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permissions to remove timeout for that member.")

    # ==========================================
    # CHANNEL LOCK / UNLOCK (THREAD SUPPRESSION)
    # ==========================================
    @commands.command(name="lock")
    @commands.has_permissions(administrator=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *, reason: str = "No reason provided"):
        """Locks a channel, suppressing chat and all thread creations/replies."""
        target_channel = channel or ctx.channel
        overwrite = target_channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        overwrite.send_messages_in_threads = False
        overwrite.create_public_threads = False
        overwrite.create_private_threads = False

        try:
            await target_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
            embed = discord.Embed(
                title="🔒 Channel Locked",
                description=f"{target_channel.mention} has been locked.\n**Reason:** {reason}\n**Admin:** {ctx.author.mention}",
                color=0xe74c3c,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to lock this channel.")

    @commands.command(name="unlock")
    @commands.has_permissions(administrator=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Unlocks a channel and restores standard thread permissions."""
        target_channel = channel or ctx.channel
        overwrite = target_channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        overwrite.send_messages_in_threads = None
        overwrite.create_public_threads = None
        overwrite.create_private_threads = None

        try:
            await target_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author}")
            embed = discord.Embed(
                title="🔓 Channel Unlocked",
                description=f"{target_channel.mention} has been unlocked by {ctx.author.mention}.",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to unlock this channel.")

    # ==========================================
    # KICK, BAN, UNBAN
    # ==========================================
    @commands.command(name="kick")
    @commands.has_permissions(administrator=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Kicks a member from the server."""
        try:
            await member.kick(reason=reason)
            embed = discord.Embed(
                title="👢 Member Kicked",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0xe67e22,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to kick that member.")

    @commands.command(name="ban")
    @commands.has_permissions(administrator=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Bans a member from the server."""
        try:
            await member.ban(reason=reason, delete_message_days=1)
            embed = discord.Embed(
                title="🔨 Member Banned",
                description=f"**Target:** {member.mention} (`{member.id}`)\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0xe74c3c,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to ban that member.")

    @commands.command(name="unban")
    @commands.has_permissions(administrator=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason provided"):
        """Unbans a user by ID."""
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            embed = discord.Embed(
                title="🔓 Member Unbanned",
                description=f"**Target:** {user.mention} (`{user.id}`)\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}",
                color=0x2ecc71,
                timestamp=discord.utils.utcnow()
            )
            await ctx.send(embed=embed)
            await self.log_action(ctx.guild, embed)
        except Exception as e:
            await ctx.send(f"❌ Failed to unban user `{user_id}`: `{e}`")

    # ==========================================
    # PURGE & SLOWMODE
    # ==========================================
    @commands.command(name="purge")
    @commands.has_permissions(administrator=True)
    async def purge(self, ctx: commands.Context, count: int, user: Optional[discord.Member] = None):
        """Purges messages from the chat (optional user filter)."""
        if count < 1 or count > 100:
            return await ctx.send("❌ Count must be between 1 and 100.")

        await ctx.message.delete()
        if user:
            def check(m):
                return m.author.id == user.id
            deleted = await ctx.channel.purge(limit=count, check=check)
        else:
            deleted = await ctx.channel.purge(limit=count)

        msg = await ctx.send(f"🧹 Purged `{len(deleted)}` messages.")
        await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=4))
        try:
            await msg.delete()
        except Exception:
            pass

    @commands.command(name="slowmode")
    @commands.has_permissions(administrator=True)
    async def slowmode(self, ctx: commands.Context, seconds: int):
        """Sets slowmode delay in seconds (0 to disable)."""
        if seconds < 0 or seconds > 21600:
            return await ctx.send("❌ Slowmode must be between 0 and 21,600 seconds.")

        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("⏱️ Slowmode has been disabled.")
        else:
            await ctx.send(f"⏱️ Slowmode set to `{seconds}` seconds.")

    # ==========================================
    # WARNINGS & AUTO-ESCALATION
    # 3 = 1h Timeout | 5 = Kick | 7 = Ban
    # ==========================================
    @commands.command(name="warn")
    @commands.has_permissions(administrator=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Issues a formal warning with auto-escalations (3=Timeout, 5=Kick, 7=Ban)."""
        warnings_db = load_json(WARNINGS_FILE)
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        if guild_id not in warnings_db:
            warnings_db[guild_id] = {}
        if user_id not in warnings_db[guild_id]:
            warnings_db[guild_id][user_id] = []

        warn_entry = {
            "admin": ctx.author.display_name,
            "admin_id": ctx.author.id,
            "reason": reason,
            "timestamp": str(discord.utils.utcnow())
        }
        warnings_db[guild_id][user_id].append(warn_entry)
        save_json(WARNINGS_FILE, warnings_db)

        total_warns = len(warnings_db[guild_id][user_id])
        escalation_note = ""

        if total_warns == 3:
            until_time = discord.utils.utcnow() + datetime.timedelta(hours=1)
            try:
                await member.timeout(until_time, reason="Auto-escalation: 3 Warnings")
                escalation_note = "\n⚠️ **Auto-Escalation:** 1-Hour Chat Timeout applied."
            except Exception:
                pass
        elif total_warns == 5:
            try:
                await member.kick(reason="Auto-escalation: 5 Warnings")
                escalation_note = "\n🚨 **Auto-Escalation:** Member has been kicked from the server."
            except Exception:
                pass
        elif total_warns >= 7:
            try:
                await member.ban(reason="Auto-escalation: 7 Warnings", delete_message_days=1)
                escalation_note = "\n⛔ **Auto-Escalation:** Member has been permanently banned."
            except Exception:
                pass

        embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"**Target:** {member.mention} (`{member.id}`)\n**Total Warnings:** `{total_warns}`\n**Admin:** {ctx.author.mention}\n**Reason:** {reason}{escalation_note}",
            color=0xf1c40f,
            timestamp=discord.utils.utcnow()
        )
        await ctx.send(embed=embed)
        await self.log_action(ctx.guild, embed)

    @commands.command(name="warnings")
    async def warnings(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Checks warning history for a user or yourself."""
        target = member or ctx.author
        warnings_db = load_json(WARNINGS_FILE)
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        user_warns = warnings_db.get(guild_id, {}).get(user_id, [])
        if not user_warns:
            return await ctx.send(f"✅ {target.mention} has 0 recorded warnings.")

        embed = discord.Embed(
            title=f"⚠️ Warning History: {target.display_name}",
            description=f"Total Warnings: **{len(user_warns)}**",
            color=0xf1c40f
        )
        for i, w in enumerate(user_warns[-10:], start=1):
            embed.add_field(
                name=f"Warning #{i}",
                value=f"**Reason:** {w['reason']}\n**Issued By:** {w['admin']}\n**Date:** {w['timestamp'][:10]}",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns")
    @commands.has_permissions(administrator=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member):
        """Clears all warnings for a member."""
        warnings_db = load_json(WARNINGS_FILE)
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        if guild_id in warnings_db and user_id in warnings_db[guild_id]:
            del warnings_db[guild_id][user_id]
            save_json(WARNINGS_FILE, warnings_db)
            await ctx.send(f"🧹 Cleared all warning history for {member.mention}.")
        else:
            await ctx.send(f"✅ {member.mention} already has 0 warnings.")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
