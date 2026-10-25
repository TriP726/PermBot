"""
cogs/music.py - PermBot Music Playback & Audio Management
Sticky Always-On-Top Dashboard Engine, Local-First Search, Track Disambiguation,
and Hybrid Music Controls.
"""

import os
import random
import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import yt_dlp
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List, Dict, Any
from urllib.parse import urlsplit

from utils.audio import create_audio_source
from cogs.player_state import Song, PlayerView, get_guild_player
from cogs.library import get_local_music_files

ONLINE_SEARCH_COUNT = 10
MAX_LOCAL_CHOICES = 10
MAX_TRACK_CHOICES = 20
LOCAL_MATCH_THRESHOLD = 0.50
TRACK_CHOICE_TIMEOUT = 60.0
ONLINE_MEDIA_DOMAINS = ("youtube.com", "youtu.be", "soundcloud.com", "sndcdn.com")

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

# Flat searches return lightweight metadata only. The chosen result is resolved
# to a playable stream URL afterwards, avoiding ten expensive extractions.
SEARCH_YTDL_OPTIONS = {
    **YTDL_OPTIONS,
    "extract_flat": "in_playlist",
    "skip_download": True,
    "noplaylist": False,
    "ignoreerrors": True,
    "playlistend": ONLINE_SEARCH_COUNT,
    "socket_timeout": 10,
    "retries": 1,
}


def truncate_text(value: Any, max_length: int) -> str:
    text = str(value or "")
    if len(text) <= max_length:
        return text
    if max_length <= 1:
        return text[:max_length]
    return text[:max_length - 1] + "…"


def safe_display_text(value: Any, max_length: int = 100) -> str:
    text = truncate_text(" ".join(str(value or "").split()), max_length)
    text = discord.utils.escape_mentions(text)
    text = discord.utils.escape_markdown(text, ignore_links=False)
    return truncate_text(text, max_length)


def safe_duration(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def normalize_match_text(value: Any) -> str:
    raw = str(value or "").lower()
    return " ".join("".join(char if char.isalnum() else " " for char in raw).split())


def match_score(query: str, *candidates: Any) -> float:
    """Return a tolerant 0..1 score for similar song/file names."""
    normalized_query = normalize_match_text(query)
    if not normalized_query:
        return 0.0

    query_words = set(normalized_query.split())
    best_score = 0.0
    for candidate in candidates:
        normalized_candidate = normalize_match_text(candidate)
        if not normalized_candidate:
            continue
        if normalized_query == normalized_candidate:
            return 1.0

        score = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        if query_words:
            candidate_words = set(normalized_candidate.split())
            word_overlap = len(query_words & candidate_words) / len(query_words)
            score = max(score, word_overlap * 0.9)
        if len(normalized_query) >= 3 and normalized_query in normalized_candidate:
            score = max(score, 0.9)
        if len(normalized_candidate) >= 3 and normalized_candidate in normalized_query:
            score = max(score, 0.9)
        best_score = max(best_score, score)
    return best_score


def online_entry_url(entry: Dict[str, Any], search_source: str = "ytsearch") -> str:
    if not isinstance(entry, dict):
        return ""

    for url in (entry.get("webpage_url"), entry.get("original_url"), entry.get("url")):
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if any(host == domain or host.endswith(f".{domain}") for domain in ONLINE_MEDIA_DOMAINS):
            return url

    # YouTube flat search entries sometimes include only a video ID.
    video_id = entry.get("id")
    if video_id and search_source == "ytsearch":
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def extract_ytdl_info(query: str, flat: bool = False) -> Optional[Dict[str, Any]]:
    options = dict(SEARCH_YTDL_OPTIONS if flat else YTDL_OPTIONS)
    with yt_dlp.YoutubeDL(options) as downloader:
        return downloader.extract_info(query, download=False)


def first_track_data(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    if "entries" in data:
        for entry in data.get("entries") or []:
            if isinstance(entry, dict):
                return entry
        return None
    return data


@dataclass
class TrackChoice:
    title: str
    artist: str
    duration: int
    is_local: bool
    local_path: Optional[str] = None
    webpage_url: str = ""
    score: float = 0.0

    @property
    def source_label(self) -> str:
        if self.is_local:
            return "📁 Local"
        host = (urlsplit(self.webpage_url).hostname or "").lower()
        return "🌐 SoundCloud" if host.endswith(("soundcloud.com", "sndcdn.com")) else "🌐 YouTube"


class TrackChoiceSelect(discord.ui.Select):
    def __init__(self, choices: List[TrackChoice], music_cog: "Music"):
        self.choices = choices
        self.music_cog = music_cog
        options: List[discord.SelectOption] = []
        for index, choice in enumerate(choices, start=1):
            duration = self.music_cog.format_time(choice.duration) if choice.duration > 0 else "--:--"
            options.append(discord.SelectOption(
                label=truncate_text(f"{index}. {choice.title}", 100),
                description=truncate_text(f"{choice.source_label} • {choice.artist} • [{duration}]", 100),
                value=str(index - 1),
                emoji="🎵"
            ))
        super().__init__(
            placeholder="🎶 Choose the track you meant...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, TrackChoiceView):
            return await interaction.response.send_message("This selection is no longer active.", ephemeral=True)
        if interaction.user.id != view.requester_id:
            return await interaction.response.send_message(
                "Only the person who started this search can choose a track.",
                ephemeral=True
            )

        await interaction.response.defer()
        view.selected_index = int(self.values[0])
        view.stop()


class TrackChoiceView(discord.ui.View):
    def __init__(self, music_cog: "Music", requester_id: int, choices: List[TrackChoice]):
        super().__init__(timeout=TRACK_CHOICE_TIMEOUT)
        self.requester_id = requester_id
        self.selected_index: Optional[int] = None
        self.cancelled = False
        self.add_item(TrackChoiceSelect(choices, music_cog))

    @discord.ui.button(label="Cancel", emoji="❌", style=discord.ButtonStyle.danger, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "Only the person who started this search can cancel it.",
                ephemeral=True
            )
        await interaction.response.defer()
        self.cancelled = True
        self.stop()


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

    @staticmethod
    def _is_url(query: str) -> bool:
        return query.startswith(("http://", "https://"))

    @staticmethod
    def _get_local_track_fields(meta: Dict[str, Any]) -> tuple[str, str, str]:
        path = str(meta.get("path") or "")
        filename = os.path.splitext(os.path.basename(path))[0]
        title = str(meta.get("title") or filename or "Unknown Title")
        artist = str(meta.get("artist") or "Local Music")
        return title, artist, filename

    @staticmethod
    def _search_local_matches(query: str) -> List[Dict[str, Any]]:
        try:
            library_files = get_local_music_files()
        except Exception as e:
            print(f"[Local Search Error] {e}")
            return []

        scored_matches = []
        for meta in library_files:
            if not isinstance(meta, dict):
                continue
            path = str(meta.get("path") or "")
            if not path:
                continue
            title, artist, filename = Music._get_local_track_fields(meta)
            score = match_score(query, title, filename, artist, f"{artist} {title}")
            if score >= LOCAL_MATCH_THRESHOLD:
                scored_matches.append((score, title.lower(), path.lower(), meta))

        scored_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [item[3] for item in scored_matches[:MAX_LOCAL_CHOICES * 2]]

    @staticmethod
    def _search_online_entries(query: str, search_source: str = "ytsearch") -> List[Dict[str, Any]]:
        try:
            data = extract_ytdl_info(f"{search_source}{ONLINE_SEARCH_COUNT}:{query}", True)
        except Exception as e:
            print(f"[{search_source} Search Error] {e}")
            return []

        if not isinstance(data, dict):
            return []
        if "entries" in data:
            raw_entries = list(data.get("entries") or [])
        else:
            raw_entries = [data]

        entries = []
        for entry in raw_entries:
            if isinstance(entry, dict):
                entry_with_source = dict(entry)
                entry_with_source["_search_source"] = search_source
                entries.append(entry_with_source)
            if len(entries) >= ONLINE_SEARCH_COUNT:
                break
        return entries

    def build_track_choices(
        self,
        query: str,
        local_matches: List[Dict[str, Any]],
        online_entries: List[Dict[str, Any]]
    ) -> List[TrackChoice]:
        local_choices: List[TrackChoice] = []
        seen_paths = set()
        for meta in local_matches:
            if not isinstance(meta, dict):
                continue
            path = str(meta.get("path") or "")
            if not path or path in seen_paths:
                continue
            title, artist, filename = self._get_local_track_fields(meta)
            score = match_score(query, title, filename, artist, f"{artist} {title}")
            local_choices.append(TrackChoice(
                title=title,
                artist=artist,
                duration=safe_duration(meta.get("duration")),
                is_local=True,
                local_path=path,
                score=score
            ))
            seen_paths.add(path)

        local_choices.sort(key=lambda choice: (-choice.score, choice.title.lower(), choice.local_path or ""))
        if local_choices:
            return local_choices[:MAX_LOCAL_CHOICES]

        online_choices: List[TrackChoice] = []
        seen_urls = set()
        for entry in online_entries:
            url = online_entry_url(entry, str(entry.get("_search_source") or "ytsearch"))
            if not url or url in seen_urls:
                continue
            title = str(entry.get("title") or entry.get("name") or "Unknown Title")
            artist = str(entry.get("artist") or entry.get("uploader") or entry.get("channel") or "Unknown Artist")
            online_choices.append(TrackChoice(
                title=title,
                artist=artist,
                duration=safe_duration(entry.get("duration")),
                is_local=False,
                webpage_url=url,
                score=match_score(query, title, f"{artist} {title}")
            ))
            seen_urls.add(url)

        return online_choices[:MAX_TRACK_CHOICES]

    async def find_track_choices(self, query: str) -> List[TrackChoice]:
        clean_query = query.strip()
        try:
            local_matches = await asyncio.to_thread(self._search_local_matches, clean_query)
        except Exception as e:
            print(f"[Local Search Task Error] {e}")
            local_matches = []

        # Local matches take precedence. Do not mix online tracks into a local result list.
        local_choices = self.build_track_choices(clean_query, local_matches, [])
        if local_choices:
            return local_choices

        results = await asyncio.gather(
            asyncio.to_thread(self._search_online_entries, clean_query, "ytsearch"),
            asyncio.to_thread(self._search_online_entries, clean_query, "scsearch"),
            return_exceptions=True
        )
        online_entries = []
        for source, result in zip(("ytsearch", "scsearch"), results):
            if isinstance(result, Exception):
                print(f"[{source} Search Task Error] {result}")
            else:
                online_entries.extend(result)
        return self.build_track_choices(clean_query, [], online_entries)

    @staticmethod
    def _song_from_extracted_data(
        data: Any,
        requester: discord.Member,
        choice: Optional[TrackChoice] = None
    ) -> Optional[Song]:
        track_data = first_track_data(data)
        if not isinstance(track_data, dict):
            return None

        track_data = dict(track_data)
        if choice:
            track_data["title"] = track_data.get("title") or choice.title
            track_data["artist"] = track_data.get("artist") or track_data.get("uploader") or choice.artist
            track_data["duration"] = safe_duration(track_data.get("duration") or choice.duration)
            track_data["webpage_url"] = track_data.get("webpage_url") or choice.webpage_url
        if not track_data.get("url"):
            return None
        return Song(track_data, requester)

    async def build_song_from_choice(
        self,
        choice: TrackChoice,
        requester: discord.Member
    ) -> Optional[Song]:
        if choice.is_local:
            if not choice.local_path:
                return None
            return Song({
                "title": choice.title,
                "artist": choice.artist,
                "duration": choice.duration,
                "url": "",
                "webpage_url": "",
                "thumbnail": None,
                "is_local": True,
                "local_path": choice.local_path
            }, requester)

        if not choice.webpage_url:
            return None
        try:
            data = await asyncio.to_thread(extract_ytdl_info, choice.webpage_url, False)
        except Exception as e:
            print(f"[Track Resolve Error] {e}")
            return None
        return self._song_from_extracted_data(data, requester, choice)

    async def resolve_track(self, query: str, requester: discord.Member) -> Optional[Song]:
        """Backwards-compatible first-match resolver; commands use the choice UI."""
        clean_query = query.strip()
        if self._is_url(clean_query):
            try:
                data = await asyncio.to_thread(extract_ytdl_info, clean_query, False)
            except Exception as e:
                print(f"[Extract Error] {e}")
                return None
            return self._song_from_extracted_data(data, requester)

        choices = await self.find_track_choices(clean_query)
        if not choices:
            return None
        return await self.build_song_from_choice(choices[0], requester)

    def format_choice_line(self, index: int, choice: TrackChoice) -> str:
        duration = self.format_time(choice.duration) if choice.duration > 0 else "--:--"
        title = safe_display_text(choice.title, 80)
        artist = safe_display_text(choice.artist, 40)
        link_url = choice.webpage_url if choice.webpage_url and len(choice.webpage_url) <= 75 else ""
        prefix = f"`{index:02d}.` "
        suffix = f" — {artist} `[{duration}]` • {choice.source_label}"

        max_line_length = 190
        markup_overhead = len(link_url) + 4 if link_url else 0
        title_budget = max(1, max_line_length - len(prefix) - len(suffix) - 4 - markup_overhead)
        title = truncate_text(title, title_budget)
        if link_url:
            title = f"[{title}]({link_url})"
        return f"{prefix}**{title}**{suffix}"

    def build_choice_embed(
        self,
        query: str,
        choices: List[TrackChoice],
        requester: discord.Member
    ) -> discord.Embed:
        lines = [self.format_choice_line(index, choice) for index, choice in enumerate(choices, start=1)]
        requester_name = safe_display_text(getattr(requester, "display_name", "requester"), 50)
        embed = discord.Embed(
            title=f"🔎 Multiple matches for '{safe_display_text(query, 120)}'",
            description="\n".join(lines),
            color=0x9B59B6
        )
        embed.add_field(
            name="Choose one",
            value=(
                f"Use the dropdown below. Only **{requester_name}** can choose, "
                f"and the menu expires after **{int(TRACK_CHOICE_TIMEOUT)} seconds**."
            ),
            inline=False
        )
        embed.set_footer(
            text=(
                f"Showing {len(choices)} local matches first; YouTube and SoundCloud are searched only when no local tracks match."
            )
        )
        return embed

    @staticmethod
    async def _edit_choice_message(message: Any, embed: discord.Embed) -> None:
        if not message:
            return
        try:
            await message.edit(embed=embed, view=None)
        except Exception:
            pass

    async def prompt_for_choice(
        self,
        ctx: commands.Context,
        query: str,
        choices: List[TrackChoice]
    ) -> Optional[TrackChoice]:
        view = TrackChoiceView(self, ctx.author.id, choices)
        picker_message = await ctx.reply(embed=self.build_choice_embed(query, choices, ctx.author), view=view)
        await view.wait()

        valid_selection = view.selected_index is not None and 0 <= view.selected_index < len(choices)
        if view.cancelled:
            await self._edit_choice_message(
                picker_message,
                discord.Embed(
                    title="🚫 Track selection cancelled",
                    description="Run the command again whenever you want to choose a track.",
                    color=0x2B2D31
                )
            )
            return None
        if not valid_selection:
            await self._edit_choice_message(
                picker_message,
                discord.Embed(
                    title="⌛ Track selection expired",
                    description="Run the command again to see fresh results.",
                    color=0x2B2D31
                )
            )
            return None

        selected = choices[view.selected_index]
        await self._edit_choice_message(
            picker_message,
            discord.Embed(
                title="✅ Track selected",
                description=f"Loading **{safe_display_text(selected.title, 150)}** — {selected.source_label}...",
                color=0x57F287
            )
        )
        return selected

    async def resolve_play_query(self, ctx: commands.Context, query: str) -> Optional[Song]:
        clean_query = (query or "").strip()
        if not clean_query:
            await ctx.reply("❌ Please provide a song title, local filename, or URL.")
            return None
        if not ctx.guild:
            await ctx.reply("❌ Music commands can only be used in a server.")
            return None

        author_voice = getattr(ctx.author, "voice", None)
        if not author_voice or not author_voice.channel:
            await ctx.reply("❌ You must be inside a voice channel to play music.")
            return None

        if self._is_url(clean_query):
            song = await self.resolve_track(clean_query, ctx.author)
            if not song:
                await ctx.reply("❌ I couldn't load a playable track from that link.")
            return song

        try:
            choices = await self.find_track_choices(clean_query)
        except Exception as e:
            print(f"[Track Search Error] {e}")
            await ctx.reply("❌ I couldn't finish the music search. Please try again.")
            return None

        if not choices:
            await ctx.reply(f"❌ No matching track found for **'{safe_display_text(clean_query, 150)}'**.")
            return None

        if len(choices) == 1:
            song = await self.build_song_from_choice(choices[0], ctx.author)
            if not song:
                await ctx.reply("❌ I couldn't load the matching track. Please try another search.")
            return song

        selected = await self.prompt_for_choice(ctx, clean_query, choices)
        if not selected:
            return None

        song = await self.build_song_from_choice(selected, ctx.author)
        if not song:
            await ctx.reply("❌ I couldn't load that track. Please try again or choose another result.")
        return song

    async def queue_song(self, ctx: commands.Context, song: Song, *, play_next: bool = False) -> None:
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        player = get_guild_player(self.bot, ctx.guild.id)
        player.text_channel = ctx.channel
        title = safe_display_text(song.title, 150)
        source_tag = "📁 Local" if song.is_local else "🌐 Web"

        if play_next:
            player.queue.insert(0, song)
        else:
            player.queue.append(song)

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(ctx.guild)
            await ctx.reply(f"🎶 Now playing [{source_tag}]: **{title}**")
            return

        await self.update_dashboard(ctx.guild, force_repost=True)
        if play_next:
            await ctx.reply(f"⏭️ Queued to play next [{source_tag}]: **{title}** (Position: `#1` in queue)")
        else:
            await ctx.reply(f"📥 Added to queue [{source_tag}]: **{title}** (Position: `#{len(player.queue)}`)")

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

    async def ensure_voice(
        self,
        guild: discord.Guild,
        member: discord.Member
    ) -> discord.VoiceClient:
        """Compatibility helper for cogs that connect with (guild, member)."""
        member_voice = getattr(member, "voice", None)
        target_channel = getattr(member_voice, "channel", None)
        if not guild or not target_channel:
            raise commands.CommandError("Join a voice channel before starting music.")

        voice = guild.voice_client
        if not voice:
            voice = await target_channel.connect()
        elif voice.channel != target_channel:
            await voice.move_to(target_channel)
        return voice

    async def ensure_voice_connection(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        author_voice = getattr(ctx.author, "voice", None)
        if not ctx.guild or not author_voice or not author_voice.channel:
            await ctx.reply("❌ You must be inside a voice channel to play music.")
            return None

        target_vc = author_voice.channel
        vc = ctx.guild.voice_client

        if not vc:
            vc = await target_vc.connect()
        elif vc.channel != target_vc:
            await vc.move_to(target_vc)

        return vc

    @commands.hybrid_command(name="play", description="Play a song, with options when your search has multiple matches.")
    @app_commands.describe(query="Song title, local filename, or URL")
    async def play(self, ctx: commands.Context, *, query: str):
        await ctx.defer()
        song = await self.resolve_play_query(ctx, query)
        if not song:
            return
        await self.queue_song(ctx, song)

    @commands.hybrid_command(name="playnext", aliases=["pn", "nextsong"], description="Queue a song next, with options when your search has multiple matches.")
    @app_commands.describe(query="Song title, local filename, or URL")
    async def playnext(self, ctx: commands.Context, *, query: str):
        await ctx.defer()
        song = await self.resolve_play_query(ctx, query)
        if not song:
            return
        await self.queue_song(ctx, song, play_next=True)

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

    async def stop_playback(self, guild: discord.Guild) -> None:
        """Invalidate the active stream, clear the queue, and disconnect voice."""
        player = get_guild_player(self.bot, guild.id)
        voice = guild.voice_client

        async with player._lock:
            # Prevent the old FFmpeg after-callback from starting another queued song.
            player.play_id += 1
            player.queue.clear()
            player.current = None
            player.skip_requested = False
            player.paused = False
            player.pause_timestamp = 0.0
            player.total_paused_duration = 0.0
            player.start_timestamp = 0.0

        if voice:
            try:
                if voice.is_playing() or voice.is_paused():
                    voice.stop()
                if voice.is_connected():
                    await voice.disconnect(force=True)
            except discord.DiscordException as e:
                print(f"[Stop Playback Error] {e}")

        await self.update_dashboard(guild, force_repost=False)
        await self.update_presence(None)

    @commands.hybrid_command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, ctx: commands.Context):
        await self.stop_playback(ctx.guild)
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
