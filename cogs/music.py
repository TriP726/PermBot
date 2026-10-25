"""Hybrid music commands backed by yt-dlp and a local audio library."""

import asyncio
import io
import os
import random
import re
import socket
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from cogs.player_state import MAX_QUEUE_SIZE, PlayerView, Song, get_guild_player, guild_players, member_shares_bot_voice
from utils.audio import EQ_DESCRIPTIONS, EQ_PRESETS, create_audio_source
from utils.checks import require_same_voice
from utils.helpers import format_duration

MEDIA_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "soundcloud.com",
    "sndcdn.com",
)
IMAGE_DOMAINS = (
    "ytimg.com",
    "ggpht.com",
    "googleusercontent.com",
    "sndcdn.com",
    "soundcloud.com",
    "discordapp.net",
    "discordapp.com",
    "discord.com",
)
MAX_ART_BYTES = 5 * 1024 * 1024

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "noplaylist": True,
    "extract_flat": False,
    "socket_timeout": 15,
    "retries": 2,
}


def url_has_allowed_domain(value: str, domains: tuple[str, ...]) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        # Accessing .port validates malformed and out-of-range values.
        if parsed.port is not None and parsed.port not in {80, 443}:
            return False
        host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        ip_address(host)
        return False
    except (ValueError, UnicodeError):
        host = locals().get("host", "")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


async def host_resolves_public(value: str) -> bool:
    try:
        host = urlsplit(value).hostname
        if not host:
            return False
        results = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        return False
    for result in results:
        try:
            address = ip_address(result[4][0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return bool(results)


def resolve_media_query(query: str) -> str:
    query = query.strip()
    if not query:
        raise commands.BadArgument("Provide a song name or supported media URL.")
    parsed = urlsplit(query)
    if parsed.scheme or parsed.netloc:
        if not url_has_allowed_domain(query, MEDIA_DOMAINS):
            raise commands.BadArgument("Only YouTube and SoundCloud media URLs are supported.")
        return query
    if "://" in query:
        raise commands.BadArgument("That media URL is invalid.")
    return f"ytsearch1:{query}"



class Music(commands.Cog):
    """High-quality local-first music playback."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.library_dir = os.path.abspath(bot.config.get("library_path", "./library"))
        self.art_cache_dir = os.path.abspath(bot.config.get("album_art_cache", "./data/album_art"))
        os.makedirs(self.library_dir, exist_ok=True)
        os.makedirs(self.art_cache_dir, exist_ok=True)
        self.local_library: list[dict[str, Any]] = []
        self.library_lock = asyncio.Lock()
        self.voice_locks: dict[int, asyncio.Lock] = {}
        self.http_session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        timeout = aiohttp.ClientTimeout(total=15)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        await self.refresh_library()

    async def cog_unload(self) -> None:
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    async def refresh_library(self) -> None:
        from cogs.library import get_local_music_files

        tracks = await asyncio.to_thread(get_local_music_files, None, (self.library_dir,))
        async with self.library_lock:
            self.local_library = tracks

    async def library_snapshot(self) -> list[dict[str, Any]]:
        async with self.library_lock:
            return list(self.local_library)

    def search_local(self, query: str, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_words = [word for word in re.split(r"\s+", query.casefold().strip()) if word]
        if not query_words:
            return list(tracks)

        def score(track: dict[str, Any]) -> tuple[int, str]:
            path = str(track.get("path", ""))
            haystack = f"{track.get('artist', '')} {track.get('title', '')} {Path(path).stem} {path}".casefold()
            if not all(word in haystack for word in query_words):
                return 0, haystack
            normalized_query = os.path.normcase(os.path.abspath(query))
            normalized_path = os.path.normcase(os.path.abspath(path))
            exact = 200 if normalized_query == normalized_path else (100 if query.casefold() in haystack else 0)
            starts = 5 if str(track.get("title", "")).casefold().startswith(query_words[0]) else 0
            return exact + len(query_words) * 10 + starts, haystack

        ranked = [(score(track), track) for track in tracks]
        return [track for (rank, _), track in sorted(ranked, key=lambda item: (-item[0][0], item[0][1])) if rank > 0]

    def song_from_local(self, track: dict[str, Any], requester: discord.Member) -> Song:
        path = os.path.abspath(str(track["path"]))
        if not os.path.isfile(path):
            raise commands.CommandError("That local track is no longer available.")
        return Song(
            {
                "title": track.get("title", Path(path).stem),
                "artist": track.get("artist", "Local Audio"),
                "duration": track.get("duration", 0),
                "url": path,
                "webpage_url": "",
                "is_local": True,
                "local_path": path,
            },
            requester,
        )

    async def extract_web_track(self, query: str, requester: discord.Member) -> Song:
        resolved_query = resolve_media_query(query)
        if resolved_query.startswith(("http://", "https://")) and not await host_resolves_public(resolved_query):
            raise commands.BadArgument("That media host did not resolve to a public address.")

        def extract() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                data = ydl.extract_info(resolved_query, download=False)
                if "entries" in data:
                    entries = [entry for entry in data.get("entries", []) if entry]
                    if not entries:
                        raise ValueError("No playable search result was found.")
                    data = entries[0]
                return data

        try:
            data = await asyncio.wait_for(asyncio.to_thread(extract), timeout=35)
        except asyncio.TimeoutError as exc:
            raise commands.CommandError("The media search timed out. Try again shortly.") from exc
        except Exception as exc:
            raise commands.CommandError("No playable YouTube or SoundCloud result was found.") from exc
        stream_url = str(data.get("url") or "")
        if not stream_url.startswith(("http://", "https://")) or not await host_resolves_public(stream_url):
            raise commands.CommandError("The selected result did not provide a safe public stream URL.")
        return Song(
            {
                "title": data.get("title", "Unknown Title"),
                "artist": data.get("artist") or data.get("uploader", "Unknown Artist"),
                "duration": data.get("duration", 0),
                "url": stream_url,
                "webpage_url": data.get("webpage_url") or data.get("original_url") or "",
                "thumbnail": data.get("thumbnail"),
                "is_local": False,
                "resolved_at": asyncio.get_running_loop().time(),
            },
            requester,
        )

    async def resolve_track(self, query: str, requester: discord.Member) -> Song:
        tracks = await self.library_snapshot()
        local_results = self.search_local(query, tracks)
        if local_results and os.path.isfile(str(local_results[0].get("path", ""))):
            return self.song_from_local(local_results[0], requester)
        # Refresh on a cache miss so newly copied local files always win over web search.
        await self.refresh_library()
        tracks = await self.library_snapshot()
        local_results = self.search_local(query, tracks)
        if local_results:
            return self.song_from_local(local_results[0], requester)
        return await self.extract_web_track(query, requester)

    async def ensure_voice(self, guild: discord.Guild, member: discord.Member) -> discord.VoiceClient:
        if not member.voice or not member.voice.channel:
            raise commands.CommandError("Join a voice channel first.")
        lock = self.voice_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            voice = guild.voice_client
            if voice and not voice.is_connected():
                try:
                    await voice.disconnect(force=True)
                except discord.DiscordException:
                    pass
                voice = None
            if voice and not member_shares_bot_voice(member, voice):
                raise commands.CommandError("The bot is already active in another voice channel.")
            if not voice:
                try:
                    voice = await member.voice.channel.connect(timeout=20, reconnect=True)
                except asyncio.TimeoutError as exc:
                    raise commands.CommandError("Connecting to voice timed out.") from exc
                except discord.ClientException as exc:
                    raise commands.CommandError("A voice connection is already being established.") from exc
            return voice

    async def queue_resolved_song(self, ctx_or_interaction: Any, member: discord.Member, song: Song, play_next: bool = False) -> None:
        guild = ctx_or_interaction.guild
        await self.ensure_voice(guild, member)
        player = get_guild_player(self.bot, guild.id)
        channel = getattr(ctx_or_interaction, "channel", None)
        if isinstance(channel, discord.TextChannel):
            player.text_channel = channel
        voice = guild.voice_client
        if not voice:
            raise commands.CommandError("The voice connection was not established.")
        async with player._lock:
            if len(player.queue) >= MAX_QUEUE_SIZE:
                raise commands.CommandError(f"The queue is full ({MAX_QUEUE_SIZE:,} waiting tracks).")
            if play_next:
                player.queue.insert(0, song)
            else:
                player.queue.append(song)
            should_start = not player.current and not voice.is_playing() and not voice.is_paused()
        if should_start:
            await self.play_next(guild)
        else:
            await self.update_dashboard(guild)

    async def _report_playback_error(self, guild: discord.Guild, message: str) -> None:
        player = get_guild_player(self.bot, guild.id)
        if player.text_channel:
            try:
                await player.text_channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except discord.DiscordException:
                pass

    def _play_source(
        self,
        voice: discord.VoiceClient,
        source: discord.AudioSource,
        guild_id: int,
        play_id: int,
    ) -> None:
        def after(error: Exception | None) -> None:
            def schedule() -> None:
                current_cog = self.bot.get_cog("Music")
                if current_cog:
                    asyncio.create_task(current_cog._after_track(guild_id, play_id, error))

            self.bot.loop.call_soon_threadsafe(schedule)

        voice.play(source, after=after)

    async def play_next(
        self,
        guild: discord.Guild,
        expected_play_id: int | None = None,
        *,
        previous_failed: bool = False,
    ) -> None:
        player = get_guild_player(self.bot, guild.id)
        expected = expected_play_id
        suppress_loop = previous_failed
        while True:
            voice = guild.voice_client
            if not voice:
                return
            queue_empty = False
            async with player._lock:
                if expected is not None and expected != player.play_id:
                    return
                expected = None
                if player.is_reloading or voice.is_playing() or voice.is_paused():
                    return
                finished = player.current
                if finished:
                    skipped = player.skip_requested
                    player.skip_requested = False
                    if not skipped and not suppress_loop and player.loop_mode == "song":
                        player.queue.insert(0, finished)
                    elif not skipped and not suppress_loop and player.loop_mode == "queue":
                        player.queue.append(finished)
                    player.current = None
                    suppress_loop = False
                if not player.queue:
                    player.current = None
                    queue_empty = True
                    song = None
                    play_id = player.play_id
                else:
                    song = player.queue.pop(0)
                    player.current = song
                    player.play_id += 1
                    play_id = player.play_id
                    player.reset_track_timing()
            if queue_empty:
                await self.update_presence(None)
                await self.update_dashboard(guild)
                return

            source: discord.AudioSource | None = None
            try:
                if not song.is_local and asyncio.get_running_loop().time() - song.resolved_at > 240:
                    refreshed = await self.extract_web_track(song.webpage_url or song.title, song.requester)
                    refreshed.title = song.title
                    refreshed.artist = song.artist
                    refreshed.thumbnail = song.thumbnail or refreshed.thumbnail
                    async with player._lock:
                        if player.play_id != play_id or player.current is not song:
                            return
                        player.current = song = refreshed
                source_value = song.local_path if song.is_local else song.url
                if not source_value:
                    raise commands.CommandError("Track source is unavailable.")
                source = create_audio_source(source_value, song.is_local, player.eq_preset, player.volume)
                async with player._lock:
                    current_voice = guild.voice_client
                    if player.play_id != play_id or player.current is not song:
                        source.cleanup()
                        return
                    if not current_voice:
                        player.queue.insert(0, song)
                        player.current = None
                        source.cleanup()
                        return
                    if current_voice.is_playing() or current_voice.is_paused():
                        source.cleanup()
                        return
                    self._play_source(current_voice, source, guild.id, play_id)
            except Exception:
                if source is not None:
                    source.cleanup()
                should_continue = False
                async with player._lock:
                    if player.play_id == play_id and player.current is song:
                        player.current = None
                        player.play_id += 1
                        should_continue = True
                if not should_continue:
                    return
                await self._report_playback_error(guild, "⚠️ A queued track could not be played; trying the next track.")
                continue
            await self.update_presence(song)
            await self.update_dashboard(guild)
            return

    async def _after_track(self, guild_id: int, play_id: int, error: Exception | None) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        player = get_guild_player(self.bot, guild_id)
        # FFmpeg can invoke its callback immediately after a replacement starts.
        # Give the bounded reload critical section time to publish its final state.
        for _ in range(40):
            if play_id != player.play_id or not player.is_reloading:
                break
            await asyncio.sleep(0.05)
        if error and play_id == player.play_id:
            await self._report_playback_error(guild, "⚠️ FFmpeg reported a playback error; moving to the next track.")
        await self.play_next(guild, expected_play_id=play_id, previous_failed=error is not None)

    async def reload_current_stream(self, guild: discord.Guild, seek_seconds: float | None = None) -> None:
        player = get_guild_player(self.bot, guild.id)
        requested_seek = seek_seconds
        while True:
            voice = guild.voice_client
            async with player._lock:
                if player.is_reloading:
                    player.reload_pending = True
                    return
                if not player.current or not voice:
                    return
                player.is_reloading = True
                player.reload_pending = False
                song = player.current
                old_play_id = player.play_id
                player.play_id += 1
                reload_id = player.play_id
                position = player.get_position() if requested_seek is None else max(0.0, float(requested_seek))
                eq_preset = player.eq_preset
                volume = player.volume
            source: discord.AudioSource | None = None
            failure = False
            stale = False
            try:
                if not song.is_local:
                    refreshed = await self.extract_web_track(song.webpage_url or song.title, song.requester)
                    song.url = refreshed.url
                    song.resolved_at = refreshed.resolved_at
                source_value = song.local_path if song.is_local else song.url
                if not source_value:
                    raise commands.CommandError("Track source is unavailable.")
                source = create_audio_source(source_value, song.is_local, eq_preset, volume, position)
                async with player._lock:
                    current_voice = guild.voice_client
                    if player.play_id != reload_id or player.current is not song or not current_voice:
                        stale = True
                    else:
                        if current_voice.is_playing() or current_voice.is_paused():
                            current_voice.stop()
                        self._play_source(current_voice, source, guild.id, reload_id)
                        player.reset_track_timing(position)
            except Exception:
                failure = True
            if stale and source is not None:
                source.cleanup()
            needs_advance = False
            async with player._lock:
                if player.play_id == reload_id and player.current is song:
                    if failure:
                        current_voice = guild.voice_client
                        if current_voice and (current_voice.is_playing() or current_voice.is_paused()):
                            player.play_id = old_play_id
                        else:
                            needs_advance = True
                    player.is_reloading = False
                    run_again = player.reload_pending and not needs_advance
                    player.reload_pending = False
                else:
                    player.is_reloading = False
                    player.reload_pending = False
                    run_again = False
            if failure:
                if source is not None:
                    source.cleanup()
                await self._report_playback_error(guild, "⚠️ The active track could not be reloaded; playback state was preserved where possible.")
            if needs_advance:
                await self.play_next(guild, expected_play_id=reload_id, previous_failed=True)
            await self.update_dashboard(guild)
            if not run_again:
                return
            requested_seek = None

    def dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        player = get_guild_player(self.bot, guild.id)
        embed = discord.Embed(title="🎧 PermBot • Music Player", color=discord.Color.purple())
        song = player.current
        if not song:
            embed.description = "Nothing is playing. Use `!play <song>` or `/play`."
            embed.add_field(name="Queue", value=f"{len(player.queue)} track(s)", inline=True)
            return embed
        position = int(player.get_position())
        total = max(song.duration, 1)
        blocks = 18
        filled = min(blocks, int((position / total) * blocks)) if song.duration else 0
        progress = "▬" * filled + "🔘" + "▬" * max(0, blocks - filled)
        requester = discord.utils.escape_markdown(song.requester.display_name)
        embed.description = f"**{discord.utils.escape_markdown(song.title)}**\nby **{discord.utils.escape_markdown(song.artist)}**"
        embed.add_field(name="Timeline", value=f"`{format_duration(position)}` {progress} `{format_duration(song.duration)}`", inline=False)
        embed.add_field(name="Audio", value=f"EQ: `{player.eq_preset}`\nVolume: `{int(player.volume * 100)}%`", inline=True)
        embed.add_field(name="Queue", value=f"{len(player.queue)} upcoming\nLoop: `{player.loop_mode}`", inline=True)
        embed.add_field(name="Requested by", value=requester[:1024], inline=True)
        if song.thumbnail and url_has_allowed_domain(song.thumbnail, IMAGE_DOMAINS):
            embed.set_thumbnail(url=song.thumbnail)
        embed.set_footer(text="Dashboard stays at the bottom of the active music channel.")
        return embed

    def _schedule_sticky_refresh(self, guild: discord.Guild) -> None:
        player = get_guild_player(self.bot, guild.id)
        if player._sticky_task and not player._sticky_task.done():
            return

        async def delayed_refresh() -> None:
            await asyncio.sleep(1.5)
            await self.update_dashboard(guild)

        player._sticky_task = asyncio.create_task(delayed_refresh())

    async def update_dashboard(self, guild: discord.Guild) -> None:
        player = get_guild_player(self.bot, guild.id)
        channel = player.text_channel
        if not channel:
            return
        async with player._sticky_lock:
            view = PlayerView(self.bot, guild.id)
            embed = self.dashboard_embed(guild)
            should_replace = player.dashboard_message is None
            if player.dashboard_message:
                try:
                    async for latest in channel.history(limit=1):
                        should_replace = latest.id != player.dashboard_message.id
                except (discord.Forbidden, discord.HTTPException):
                    should_replace = False
            try:
                if should_replace:
                    if player.dashboard_message:
                        try:
                            await player.dashboard_message.delete()
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass
                    player.dashboard_message = await channel.send(embed=embed, view=view)
                else:
                    await player.dashboard_message.edit(embed=embed, view=view)
                player.dashboard_view = view
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def update_presence(self, song: Song | None) -> None:
        if song is None:
            song = next((state.current for state in guild_players.values() if state.current), None)
        if song:
            activity = discord.Activity(type=discord.ActivityType.listening, name=song.title[:128])
        else:
            activity = discord.Game(name=f"{self.bot.config.get('prefix', '!')}help | /help")
        try:
            await self.bot.change_presence(activity=activity)
        except discord.DiscordException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        player = get_guild_player(self.bot, message.guild.id)
        if player.text_channel and message.channel.id == player.text_channel.id and player.dashboard_message:
            self._schedule_sticky_refresh(message.guild)

    @commands.hybrid_command(name="play", aliases=["p"], description="Play a local track first, otherwise search YouTube/SoundCloud.")
    @commands.guild_only()
    @commands.cooldown(3, 20, commands.BucketType.user)
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        await ctx.defer()
        song = await self.resolve_track(query, ctx.author)
        await self.queue_resolved_song(ctx, ctx.author, song)
        source = "local library" if song.is_local else "web"
        await ctx.send(f"🎵 Queued **{song.title}** from the {source}.", allowed_mentions=discord.AllowedMentions.none())
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="playnext", aliases=["pn", "nextsong"], description="Insert a song at queue position #1.")
    @commands.guild_only()
    @commands.cooldown(3, 20, commands.BucketType.user)
    async def playnext(self, ctx: commands.Context, *, query: str) -> None:
        await ctx.defer()
        song = await self.resolve_track(query, ctx.author)
        await self.queue_resolved_song(ctx, ctx.author, song, play_next=True)
        await ctx.send(f"⏭️ Inserted **{song.title}** at queue position #1.", allowed_mentions=discord.AllowedMentions.none())
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="pause", description="Pause playback.")
    @commands.guild_only()
    @require_same_voice()
    async def pause(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if not voice or not voice.is_playing():
            raise commands.CommandError("Nothing is playing.")
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            voice.pause()
            player.mark_paused()
        await ctx.send("⏸️ Paused.")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="resume", description="Resume playback.")
    @commands.guild_only()
    @require_same_voice()
    async def resume(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if not voice or not voice.is_paused():
            raise commands.CommandError("Playback is not paused.")
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            voice.resume()
            player.mark_resumed()
        await ctx.send("▶️ Resumed.")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="skip", aliases=["s", "next"], description="Skip the active song.")
    @commands.guild_only()
    @require_same_voice()
    async def skip(self, ctx: commands.Context) -> None:
        voice = ctx.guild.voice_client
        if not voice or not (voice.is_playing() or voice.is_paused()):
            raise commands.CommandError("Nothing is playing.")
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            player.skip_requested = True
        voice.stop()
        await ctx.send("⏭️ Skipped.")

    async def stop_guild_player(self, guild: discord.Guild) -> None:
        player = get_guild_player(self.bot, guild.id)
        async with player._lock:
            player.queue.clear()
            player.current = None
            player.play_id += 1
            player.is_reloading = False
            player.reload_pending = False
        voice_lock = self.voice_locks.setdefault(guild.id, asyncio.Lock())
        async with voice_lock:
            voice = guild.voice_client
            if voice:
                voice.stop()
                try:
                    await voice.disconnect(force=True)
                except discord.DiscordException:
                    pass

    @commands.hybrid_command(name="stop", aliases=["disconnect", "leave"], description="Stop playback and leave voice.")
    @commands.guild_only()
    @require_same_voice()
    async def stop(self, ctx: commands.Context) -> None:
        await self.stop_guild_player(ctx.guild)
        await ctx.send("⏹️ Stopped playback and cleared the queue.")
        await self.update_dashboard(ctx.guild)
        await self.update_presence(None)

    @commands.hybrid_command(name="queue", aliases=["q"], description="Show the active queue.")
    @commands.guild_only()
    async def queue(self, ctx: commands.Context) -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        embed = discord.Embed(title="📜 Active Queue", color=discord.Color.blurple())
        if player.current:
            embed.add_field(name="Now Playing", value=f"**{player.current.title}** — {player.current.artist}"[:1024], inline=False)
        if player.queue:
            value = "\n".join(f"`{index}.` **{song.title[:70]}** — {song.artist[:50]}" for index, song in enumerate(player.queue[:15], 1))
            embed.add_field(name="Up Next", value=value[:1024], inline=False)
        elif not player.current:
            embed.description = "The queue is empty."
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="nowplaying", aliases=["np"], description="Show the active player dashboard.")
    @commands.guild_only()
    async def nowplaying(self, ctx: commands.Context) -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        if isinstance(ctx.channel, discord.TextChannel):
            player.text_channel = ctx.channel
        await self.update_dashboard(ctx.guild)
        await ctx.send("🎧 Player dashboard refreshed.", ephemeral=ctx.interaction is not None)
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="volume", aliases=["vol"], description="Set playback volume from 1 to 200 percent.")
    @commands.guild_only()
    @require_same_voice()
    @app_commands.describe(percent="Volume percentage (1-200)")
    async def volume(self, ctx: commands.Context, percent: commands.Range[int, 1, 200]) -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            player.volume = percent / 100.0
        await self.reload_current_stream(ctx.guild)
        await ctx.send(f"🔊 Volume set to **{percent}%**.")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="clear", description="Clear all waiting tracks without stopping the active song.")
    @commands.guild_only()
    @require_same_voice()
    async def clear(self, ctx: commands.Context) -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            count = len(player.queue)
            player.queue.clear()
        await self.update_dashboard(ctx.guild)
        await ctx.send(f"🗑️ Cleared **{count}** waiting track(s).")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="loop", description="Set loop mode: off, song, or queue.")
    @commands.guild_only()
    @require_same_voice()
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Current song", value="song"),
        app_commands.Choice(name="Whole queue", value="queue"),
    ])
    async def loop(self, ctx: commands.Context, mode: str = "song") -> None:
        if mode not in {"off", "song", "queue"}:
            raise commands.BadArgument("Mode must be `off`, `song`, or `queue`.")
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            player.loop_mode = mode
        await ctx.send(f"🔁 Loop mode set to **{mode}**.")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="shuffle", description="Shuffle the active queue, or use 'all' for the full local library.")
    @commands.guild_only()
    @require_same_voice()
    async def shuffle(self, ctx: commands.Context, scope: str = "queue") -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        all_tracks = scope.casefold() in {"all", "library", "shuffleall"}
        tracks: list[dict[str, Any]] = []
        if all_tracks:
            await self.ensure_voice(ctx.guild, ctx.author)
            await self.refresh_library()
            tracks = await self.library_snapshot()
        async with player._lock:
            if all_tracks:
                capacity = max(0, MAX_QUEUE_SIZE - len(player.queue))
                additions = [self.song_from_local(track, ctx.author) for track in tracks[:capacity]]
                if not additions:
                    raise commands.CommandError("No local tracks could be added; the library is empty or the queue is full.")
                random.shuffle(additions)
                player.queue.extend(additions)
                message = f"🔀 Added and shuffled **{len(additions)}** local tracks."
            else:
                random.shuffle(player.queue)
                message = f"🔀 Shuffled **{len(player.queue)}** queued tracks."
            should_start = not player.current and bool(player.queue)
        await ctx.send(message)
        if should_start:
            await self.play_next(ctx.guild)
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="rescan", description="Rescan the local audio library.")
    @commands.guild_only()
    @commands.cooldown(2, 30, commands.BucketType.guild)
    async def rescan(self, ctx: commands.Context) -> None:
        await ctx.defer()
        await self.refresh_library()
        await ctx.send(f"🔄 Found **{len(await self.library_snapshot())}** local tracks.")
        await self.update_dashboard(ctx.guild)

    @commands.hybrid_command(name="eq", aliases=["equalizer"], description="View or select a studio EQ preset.")
    @commands.guild_only()
    @require_same_voice()
    async def eq(self, ctx: commands.Context, preset: str | None = None) -> None:
        if preset is None:
            current = get_guild_player(self.bot, ctx.guild.id).eq_preset
            lines = [f"**{name}** — {description}" for name, description in EQ_DESCRIPTIONS.items()]
            await ctx.send(embed=discord.Embed(title=f"🎚️ EQ Presets • Current: {current}", description="\n".join(lines), color=discord.Color.purple()))
            await self.update_dashboard(ctx.guild)
            return
        preset = preset.casefold().strip()
        if preset not in EQ_PRESETS:
            raise commands.BadArgument(f"Unknown preset. Choose: {', '.join(EQ_PRESETS)}")
        player = get_guild_player(self.bot, ctx.guild.id)
        async with player._lock:
            player.eq_preset = preset
        await self.reload_current_stream(ctx.guild)
        await ctx.send(f"🎚️ EQ set to **{preset}** — {EQ_DESCRIPTIONS[preset]}")
        await self.update_dashboard(ctx.guild)

    async def _fetch_artwork(self, image_url: str) -> tuple[bytes, str]:
        if not self.http_session:
            raise commands.CommandError("The artwork service is not ready.")
        current_url = image_url
        for _ in range(4):
            if not url_has_allowed_domain(current_url, IMAGE_DOMAINS) or not await host_resolves_public(current_url):
                raise commands.BadArgument("Artwork must use a supported public image host.")
            async with self.http_session.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise commands.CommandError("Artwork redirected without a destination.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status != 200:
                    raise commands.CommandError(f"Artwork download failed with HTTP {response.status}.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise commands.BadArgument("Artwork must be a JPEG, PNG, or WebP image.")
                declared = response.content_length
                if declared is not None and declared > MAX_ART_BYTES:
                    raise commands.BadArgument("Artwork exceeds the 5 MB limit.")
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_ART_BYTES:
                        raise commands.BadArgument("Artwork exceeds the 5 MB limit.")
                if not body:
                    raise commands.CommandError("Artwork download returned an empty image.")
                return bytes(body), content_type
        raise commands.CommandError("Artwork redirected too many times.")

    @commands.hybrid_command(name="art", aliases=["albumart"], description="Show or safely download artwork for the current song.")
    @commands.guild_only()
    async def art(self, ctx: commands.Context, download: bool = False) -> None:
        player = get_guild_player(self.bot, ctx.guild.id)
        if not player.current or not player.current.thumbnail:
            raise commands.CommandError("The current track has no artwork.")
        image_url = player.current.thumbnail
        if not url_has_allowed_domain(image_url, IMAGE_DOMAINS):
            raise commands.CommandError("The artwork host is not supported.")
        if not download:
            embed = discord.Embed(title=player.current.title, color=discord.Color.purple())
            embed.set_image(url=image_url)
            await ctx.send(embed=embed)
            await self.update_dashboard(ctx.guild)
            return
        await ctx.defer()
        body, content_type = await self._fetch_artwork(image_url)
        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
        await ctx.send(file=discord.File(io.BytesIO(body), filename=f"album_art{suffix}"))
        await self.update_dashboard(ctx.guild)

    @play.error
    @playnext.error
    @pause.error
    @resume.error
    @skip.error
    @stop.error
    @volume.error
    @clear.error
    @loop.error
    @shuffle.error
    @eq.error
    @art.error
    async def music_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        original = getattr(error, "original", error)
        await ctx.send(f"❌ {original}", ephemeral=ctx.interaction is not None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
