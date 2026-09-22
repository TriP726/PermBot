"""
cogs/music.py - PermBot Music Playback & Audio Management
Sticky Always-On-Top Dashboard Engine, Local-First Search, and Hybrid Music Controls
"""

import os
import random
import asyncio
import yt_dlp
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List, Dict, Any

from utils.audio import create_audio_source, EQ_PRESETS, EQ_DESCRIPTIONS
from cogs.player_state import Song, GuildPlayer, PlayerView, get_guild_player, guild_players
from cogs.library import get_local_music_files

YTDL_OPTIONS = {
    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def format_time(self, seconds: int) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def generate_progress_bar(self, position: float, duration: int, length: int = 15) -> str:
        if duration <= 0:
            return "🔘" + "▬" * (length - 1) + " (Live)"
        progress = min(1.0, max(0.0, position / duration))
        filled = int(progress * length)
        bar = ""
        for i in range(length):
            if i == filled:
                bar += "🔘"
            elif i < filled:
                bar += "▬"
            else:
                bar += "─"
        return bar

    async def update_presence(self, song_title: Optional[str] = None):
        try:
            if song_title:
                activity = discord.Activity(type=discord.ActivityType.listening, name=song_title[:120])
            else:
                activity = discord.Activity(type=discord.ActivityType.listening, name="!help | /help")
            await self.bot.change_presence(activity=activity)
        except Exception:
            pass

    async def resolve_track(self, query: str, requester: discord.Member) -> Optional[Song]:
        clean_query = query.strip()
        is_url = clean_query.startswith("http://") or clean_query.startswith("https://")

        # Local search first
        if not is_url:
            local_matches = get_local_music_files(filter_query=clean_query)
            if local_matches:
                best_match = local_matches[0]
                for f in local_matches:
                    title = str(f.get("title", "")).lower()
                    fname = os.path.splitext(os.path.basename(f["path"]))[0].lower()
                    if clean_query.lower() in (title, fname):
                        best_match = f
                        break

                song_data = {
                    "title": best_match.get("title", os.path.splitext(os.path.basename(best_match["path"]))[0]),
                    "artist": best_match.get("artist", "Local Music"),
                    "duration": best_match.get("duration", 0),
                    "url": "",
                    "webpage_url": "",
                    "thumbnail": None,
                    "is_local": True,
                    "local_path": best_match["path"]
                }
                return Song(song_data, requester)

        # Fallback to online stream
        loop = self.bot.loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(clean_query, download=False))
        except Exception as e:
            print(f"[Extract Error] {e}")
            return None

        if not data:
            return None

        if "entries" in data:
            entries = [e for e in data["entries"] if e]
            if not entries:
                return None
            track_data = entries[0]
        else:
            track_data = data

        return Song(track_data, requester)

    async def update_dashboard(self, guild: discord.Guild, force_repost: bool = False):
        """Maintains sticky status at the bottom of the channel."""
        player = get_guild_player(self.bot, guild.id)
        if not player.text_channel:
            return

        async with player._sticky_lock:
            vc = guild.voice_client
            embed = discord.Embed(color=0x2B2D31)

            if player.current:
                pos = player.get_position()
                dur = player.current.duration
                bar = self.generate_progress_bar(pos, dur, length=14)
                pos_str = self.format_time(int(pos))
                dur_str = self.format_time(dur) if dur > 0 else "Live Stream"

                status_emoji = "⏸️ Paused" if player.paused else "▶️ Playing"
                loop_str = f"🔂 {player.loop_mode.title()}" if player.loop_mode != "off" else "➡️ Off"
                vol_pct = int(player.volume * 100)

                embed.title = f"{status_emoji}: {player.current.title}"
                if player.current.webpage_url:
                    embed.url = player.current.webpage_url
                embed.description = f"`{bar}`\n`[{pos_str} / {dur_str}]`"

                source_type = "📁 Local Track" if player.current.is_local else "🌐 Online Stream"
                embed.add_field(name="Source", value=f"`{source_type}`", inline=True)
                embed.add_field(name="Artist / Channel", value=f"`{player.current.artist}`", inline=True)
                embed.add_field(name="Requested By", value=f"{player.current.requester.mention}", inline=True)
                embed.add_field(name="Volume", value=f"`{vol_pct}%`", inline=True)
                embed.add_field(name="Audio Profile / EQ", value=f"`{player.eq_preset.upper()}`", inline=True)
                embed.add_field(name="Queue Length", value=f"`{len(player.queue)} track(s)`", inline=True)

                if player.current.thumbnail:
                    embed.set_thumbnail(url=player.current.thumbnail)
            else:
                embed.title = "🎵 PermBot Studio Audio Hub"
                embed.description = "No track currently playing.\nUse `!play <song>` or `/play` to stream high-resolution music!"
                embed.add_field(name="Default Profile", value=f"`{player.eq_preset.upper()}`", inline=True)
                embed.add_field(name="Volume", value=f"`{int(player.volume * 100)}%`", inline=True)
                embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else None)

            view = PlayerView(self.bot, guild.id) if (player.current and vc and vc.is_connected()) else None

            if force_repost or not player.dashboard_message:
                if player.dashboard_message:
                    try:
                        await player.dashboard_message.delete()
                    except Exception:
                        pass
                    player.dashboard_message = None
                try:
                    player.dashboard_message = await player.text_channel.send(embed=embed, view=view)
                except Exception:
                    pass
            else:
                try:
                    await player.dashboard_message.edit(embed=embed, view=view)
                except Exception:
                    try:
                        player.dashboard_message = await player.text_channel.send(embed=embed, view=view)
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatically moves the sticky dashboard to stay on top whenever people chat."""
        if not message.guild or message.author.bot:
            return
        player = get_guild_player(self.bot, message.guild.id)
        if not player.text_channel or message.channel.id != player.text_channel.id:
            return
        if not player.current or not message.guild.voice_client:
            return

        # If sticky task already scheduled, avoid spamming reposts
        if player._sticky_task and not player._sticky_task.done():
            return

        async def _repost():
            await asyncio.sleep(0.5)
            await self.update_dashboard(message.guild, force_repost=True)

        player._sticky_task = self.bot.loop.create_task(_repost())

    async def reload_current_stream(self, guild: discord.Guild, seek_seconds: Optional[float] = None):
        player = get_guild_player(self.bot, guild.id)
        vc = guild.voice_client

        if not vc or not player.current:
            return

        async with player._lock:
            player.play_id += 1
            session_id = player.play_id
            pos = player.get_position() if seek_seconds is None else seek_seconds
            target_audio = player.current.local_path if player.current.is_local else player.current.url

            try:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                    for _ in range(15):
                        if not vc.is_playing() and not vc.is_paused():
                            break
                        await asyncio.sleep(0.03)

                source = create_audio_source(
                    target_audio,
                    is_local=player.current.is_local,
                    eq_preset=player.eq_preset,
                    volume=player.volume,
                    seek_seconds=pos
                )
                player.reset_track_timing(offset=pos)

                def after_callback(err):
                    if err:
                        print(f"[Audio Stream Error] {err}")
                    if player.play_id == session_id:
                        self.bot.loop.create_task(self.play_next(guild))

                vc.play(source, after=after_callback)
            except Exception as e:
                print(f"[Stream Reload Error] {e}")
            finally:
                await self.update_dashboard(guild, force_repost=False)

    async def play_next(self, guild: discord.Guild):
        player = get_guild_player(self.bot, guild.id)
        vc = guild.voice_client

        if getattr(player, "is_reloading", False):
            return

        if not vc or not vc.is_connected():
            player.current = None
            await self.update_presence(None)
            return

        if vc.is_playing() or vc.is_paused():
            return

        player.play_id += 1
        session_id = player.play_id

        if not getattr(player, "skip_requested", False) and player.current:
            if player.loop_mode == "song":
                player.queue.insert(0, player.current)
            elif player.loop_mode == "queue":
                player.queue.append(player.current)

        player.skip_requested = False

        if not player.queue:
            player.current = None
            await self.update_dashboard(guild, force_repost=True)
            await self.update_presence(None)
            return

        player.current = player.queue.pop(0)
        target_audio = player.current.local_path if player.current.is_local else player.current.url

        try:
            source = create_audio_source(
                target_audio,
                is_local=player.current.is_local,
                eq_preset=player.eq_preset,
                volume=player.volume,
                seek_seconds=0
            )
            player.reset_track_timing(offset=0)

            def after_callback(err):
                if err:
                    print(f"[Audio Engine Error] {err}")
                if player.play_id == session_id:
                    self.bot.loop.create_task(self.play_next(guild))

            vc.play(source, after=after_callback)
            await self.update_dashboard(guild, force_repost=True)
            await self.update_presence(player.current.title)
        except Exception as err:
            print(f"[Playback Error] {err}")
            player.current = None
            if player.queue:
                await asyncio.sleep(0.5)
                await self.play_next(guild)

    async def ensure_voice_connection(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply("❌ You must be inside a voice channel to play music.")
            return None

        target_vc = ctx.author.voice.channel
        vc = ctx.guild.voice_client

        if not vc:
            vc = await target_vc.connect()
        elif vc.channel != target_vc:
            await vc.move_to(target_vc)

        return vc

    @commands.hybrid_command(name="play", description="Play a song (searches local library first, then online).")
    @app_commands.describe(query="Song title, local filename, or URL")
    async def play(self, ctx: commands.Context, *, query: str):
        await ctx.defer()
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        player = get_guild_player(self.bot, ctx.guild.id)
        player.text_channel = ctx.channel

        song = await self.resolve_track(query, ctx.author)
        if not song:
            return await ctx.reply(f"❌ No matching track found for **'{query}'**.")

        player.queue.append(song)
        source_tag = "📁 Local" if song.is_local else "🌐 Web"

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(ctx.guild)
            await ctx.reply(f"🎶 Now playing [{source_tag}]: **{song.title}**")
        else:
            await self.update_dashboard(ctx.guild, force_repost=True)
            await ctx.reply(f"📥 Added to queue [{source_tag}]: **{song.title}** (Position: `#{len(player.queue)}`)")

    @commands.hybrid_command(name="playnext", aliases=["pn", "nextsong"], description="Queue a song to play immediately next after the current track.")
    @app_commands.describe(query="Song title, local filename, or URL")
    async def playnext(self, ctx: commands.Context, *, query: str):
        await ctx.defer()
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        player = get_guild_player(self.bot, ctx.guild.id)
        player.text_channel = ctx.channel

        song = await self.resolve_track(query, ctx.author)
        if not song:
            return await ctx.reply(f"❌ No matching track found for **'{query}'**.")

        source_tag = "📁 Local" if song.is_local else "🌐 Web"

        if not vc.is_playing() and not vc.is_paused():
            player.queue.insert(0, song)
            await self.play_next(ctx.guild)
            await ctx.reply(f"🎶 Now playing [{source_tag}]: **{song.title}**")
        else:
            player.queue.insert(0, song)
            await self.update_dashboard(ctx.guild, force_repost=True)
            await ctx.reply(f"⏭️ Queued to play next [{source_tag}]: **{song.title}** (Position: `#1` in queue)")

    @commands.hybrid_command(name="shuffle", description="Shuffle current queue or all local tracks (!shuffle all).")
    @app_commands.describe(target="Leave empty to shuffle current queue, or type 'all' to shuffle local music")
    async def shuffle(self, ctx: commands.Context, target: Optional[str] = None):
        if target and target.lower() in ("all", "local", "library"):
            lib_cog = self.bot.get_cog("Library")
            if lib_cog:
                return await lib_cog.shuffleall(ctx)

        player = get_guild_player(self.bot, ctx.guild.id)
        if len(player.queue) < 2:
            return await ctx.reply("⚠️ You need at least **2 songs** in the queue to shuffle. (Use `!shuffleall` to shuffle local files!)")

        random.shuffle(player.queue)
        await self.update_dashboard(ctx.guild, force_repost=True)
        await ctx.reply(f"🔀 Shuffled **{len(player.queue)} tracks** in the queue!")

    @commands.hybrid_command(name="queue", aliases=["q"], description="Display all upcoming tracks in the music queue.")
    async def queue_cmd(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        if not player.current and not player.queue:
            return await ctx.reply("📭 The queue is currently empty.")

        embed = discord.Embed(title="📜 Music Queue", color=0x3498DB)
        if player.current:
            pos = self.format_time(int(player.get_position()))
            dur = self.format_time(player.current.duration) if player.current.duration > 0 else "Live"
            link = player.current.webpage_url if player.current.webpage_url else "#"
            embed.add_field(
                name="▶️ Currently Playing",
                value=f"[{player.current.title}]({link}) `[{pos}/{dur}]` (Requested by {player.current.requester.mention})",
                inline=False
            )

        if player.queue:
            queue_list = []
            for idx, song in enumerate(player.queue[:10], start=1):
                dur_str = self.format_time(song.duration) if song.duration > 0 else "Live"
                link = song.webpage_url if song.webpage_url else "#"
                tag = "📁 " if song.is_local else ""
                queue_list.append(f"`{idx}.` {tag}[{song.title}]({link}) `[{dur_str}]` - {song.requester.mention}")
            embed.description = "\n".join(queue_list)
            if len(player.queue) > 10:
                embed.set_footer(text=f"... and {len(player.queue) - 10} more track(s) in queue")
        else:
            embed.description = "*No upcoming songs in queue. Add songs using `!play <song>`.*"

        await ctx.reply(embed=embed)

    @commands.hybrid_command(name="nowplaying", aliases=["np"], description="Display current song details.")
    async def nowplaying(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        if not player.current:
            return await ctx.reply("❌ Nothing is currently playing.")
        await self.update_dashboard(ctx.guild, force_repost=True)

    @commands.hybrid_command(name="pause", description="Pause the currently playing track.")
    async def pause(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        vc = ctx.guild.voice_client
        if not vc or not vc.is_playing():
            return await ctx.reply("❌ Nothing is currently playing.")
        vc.pause()
        player.paused = True
        player.pause_timestamp = asyncio.get_event_loop().time()
        await self.update_dashboard(ctx.guild, force_repost=False)
        await ctx.reply("⏸️ Playback paused.")

    @commands.hybrid_command(name="resume", description="Resume paused music playback.")
    async def resume(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        vc = ctx.guild.voice_client
        if not vc or not vc.is_paused():
            return await ctx.reply("❌ Playback is not paused.")
        vc.resume()
        player.paused = False
        await self.update_dashboard(ctx.guild, force_repost=False)
        await ctx.reply("▶️ Playback resumed.")

    @commands.hybrid_command(name="skip", aliases=["s"], description="Skip the currently playing track.")
    async def skip(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        vc = ctx.guild.voice_client
        if not vc or not player.current:
            return await ctx.reply("❌ Nothing is currently playing.")
        player.skip_requested = True
        vc.stop()
        await ctx.reply("⏭️ Skipped track.")

    @commands.hybrid_command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        vc = ctx.guild.voice_client
        player.queue.clear()
        player.current = None
        if vc:
            vc.stop()
            await vc.disconnect()
        await self.update_dashboard(ctx.guild, force_repost=False)
        await self.update_presence(None)
        await ctx.reply("⏹️ Playback stopped and disconnected.")

    @commands.hybrid_command(name="clear", description="Clear all tracks from the queue.")
    async def clear(self, ctx: commands.Context):
        player = get_guild_player(self.bot, ctx.guild.id)
        count = len(player.queue)
        player.queue.clear()
        await self.update_dashboard(ctx.guild, force_repost=False)
        await ctx.reply(f"🗑️ Cleared **{count}** tracks from the queue.")

    @commands.hybrid_command(name="loop", description="Toggle playback loop (off, song, queue).")
    @app_commands.describe(mode="Choose loop mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Current Song (🔂)", value="song"),
        app_commands.Choice(name="Entire Queue (🔁)", value="queue"),
    ])
    async def loop_cmd(self, ctx: commands.Context, mode: Optional[str] = None):
        player = get_guild_player(self.bot, ctx.guild.id)
        if mode:
            player.loop_mode = mode
        else:
            modes = ["off", "song", "queue"]
            idx = (modes.index(player.loop_mode) + 1) % len(modes)
            player.loop_mode = modes[idx]
        await self.update_dashboard(ctx.guild, force_repost=False)
        await ctx.reply(f"🔁 Loop mode set to: **`{player.loop_mode.upper()}`**")

    @commands.hybrid_command(name="volume", aliases=["vol"], description="Adjust playback volume (1% - 200%).")
    @app_commands.describe(percent="Volume level between 1 and 200")
    async def volume(self, ctx: commands.Context, percent: int):
        if not 1 <= percent <= 200:
            return await ctx.reply("❌ Volume must be between `1` and `200`.")
        await ctx.defer()
        player = get_guild_player(self.bot, ctx.guild.id)
        player.volume = percent / 100.0
        await self.reload_current_stream(ctx.guild)
        await ctx.reply(f"🔊 Volume set to **{percent}%**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
