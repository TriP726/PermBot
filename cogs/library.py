"""
cogs/library.py - PermBot Interactive Local Music Library
Interactive paginated song browser, search filtering, and click-to-play UI.
"""

import os
import math
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Optional, Dict, Any

from utils.audio import scan_library, get_local_file_metadata, SUPPORTED_MEDIA_EXTENSIONS
from cogs.player_state import Song, GuildPlayer, get_guild_player

MUSIC_DIRECTORIES = ["music", "songs", "audio", "library", "local_music"]


def get_local_music_files(filter_query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Scans all music directories and returns sorted metadata, with optional filter query."""
    all_files = []
    seen_paths = set()

    for d in MUSIC_DIRECTORIES:
        if os.path.exists(d) and os.path.isdir(d):
            scanned = scan_library(d)
            for item in scanned:
                if item["path"] not in seen_paths:
                    seen_paths.add(item["path"])
                    all_files.append(item)

    # Sort alphabetically by title
    all_files.sort(key=lambda x: str(x.get("title", "")).lower())

    if filter_query:
        q = filter_query.lower().strip()
        all_files = [
            f for f in all_files
            if q in str(f.get("title", "")).lower()
            or q in str(f.get("artist", "")).lower()
            or q in os.path.basename(f.get("path", "")).lower()
        ]

    return all_files


class LibrarySongSelect(discord.ui.Select):
    def __init__(self, page_files: List[Dict[str, Any]], bot: commands.Bot):
        self.bot = bot
        self.page_files = page_files
        options = []
        for idx, f in enumerate(page_files[:25]):
            m, s = divmod(f.get("duration", 0), 60)
            dur_str = f"{m}:{s:02d}" if f.get("duration", 0) > 0 else "--:--"
            title = f.get("title", "Unknown")[:75]
            desc = f"{f.get('artist', 'Local')} • [{dur_str}]"[:100]
            options.append(discord.SelectOption(
                label=f"{idx+1}. {title}",
                description=desc,
                value=f["path"],
                emoji="🎵"
            ))
        super().__init__(placeholder="🎶 Select a track to play immediately...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ You must be in a voice channel to play music.", ephemeral=True)

        target_vc = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            vc = await target_vc.connect()
        elif vc.channel != target_vc:
            await vc.move_to(target_vc)

        chosen_path = self.values[0]
        meta = get_local_file_metadata(chosen_path)
        song_data = {
            "title": meta.get("title", os.path.splitext(os.path.basename(chosen_path))[0]),
            "artist": meta.get("artist", "Local Music"),
            "duration": meta.get("duration", 0),
            "url": "",
            "webpage_url": "",
            "thumbnail": None,
            "is_local": True,
            "local_path": chosen_path
        }
        song = Song(song_data, interaction.user)
        player = get_guild_player(self.bot, interaction.guild_id)
        player.text_channel = interaction.channel
        player.queue.append(song)

        music_cog = self.bot.get_cog("Music")
        if not vc.is_playing() and not vc.is_paused():
            if music_cog:
                await music_cog.play_next(interaction.guild)
            await interaction.followup.send(f"🎶 Now playing: **{song.title}**")
        else:
            if music_cog:
                await music_cog.update_dashboard(interaction.guild)
            await interaction.followup.send(f"📥 Added to queue: **{song.title}** (Position: `#{len(player.queue)}`)")


class LibraryPaginationView(discord.ui.View):
    def __init__(self, bot: commands.Bot, files: List[Dict[str, Any]], page: int = 0, query: Optional[str] = None):
        super().__init__(timeout=180)
        self.bot = bot
        self.files = files
        self.page = page
        self.per_page = 10
        self.query = query
        self.total_pages = max(1, math.ceil(len(files) / self.per_page))
        self.refresh_items()

    def get_current_page_files(self) -> List[Dict[str, Any]]:
        start = self.page * self.per_page
        return self.files[start:start + self.per_page]

    def refresh_items(self):
        self.clear_items()
        page_files = self.get_current_page_files()
        if page_files:
            self.add_item(LibrarySongSelect(page_files, self.bot))

        # Nav buttons
        prev_btn = discord.ui.Button(label="◀️ Prev", style=discord.ButtonStyle.secondary, disabled=(self.page <= 0), row=1)
        prev_btn.callback = self.prev_page
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(label="Next ▶️", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1), row=1)
        next_btn.callback = self.next_page
        self.add_item(next_btn)

        shuffle_btn = discord.ui.Button(label="🔀 Shuffle All", style=discord.ButtonStyle.primary, row=1)
        shuffle_btn.callback = self.shuffle_all_action
        self.add_item(shuffle_btn)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="📁 Local Audio Library", color=0x3498DB)
        start = self.page * self.per_page
        page_files = self.files[start:start + self.per_page]

        if not self.files:
            embed.description = "No audio files found. Place songs inside a `./music` folder!"
            return embed

        lines = []
        for idx, f in enumerate(page_files, start=start + 1):
            m, s = divmod(f.get("duration", 0), 60)
            dur_str = f"{m}:{s:02d}" if f.get("duration", 0) > 0 else "--:--"
            lines.append(f"`{idx:02d}.` **{f.get('title', 'Unknown')}** • *{f.get('artist', 'Local')}* `[{dur_str}]`")

        embed.description = "\n".join(lines)
        footer_text = f"Page {self.page + 1} of {self.total_pages} • Total: {len(self.files)} tracks"
        if self.query:
            footer_text += f" • Filter: '{self.query}'"
        embed.set_footer(text=footer_text)
        return embed

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
            self.refresh_items()
            await interaction.message.edit(embed=self.build_embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.page < self.total_pages - 1:
            self.page += 1
            self.refresh_items()
            await interaction.message.edit(embed=self.build_embed(), view=self)

    async def shuffle_all_action(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ You must be in a voice channel to shuffle!", ephemeral=True)

        target_vc = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            vc = await target_vc.connect()
        elif vc.channel != target_vc:
            await vc.move_to(target_vc)

        shuffled_files = list(self.files)
        random.shuffle(shuffled_files)
        player = get_guild_player(self.bot, interaction.guild_id)
        player.text_channel = interaction.channel

        new_songs = []
        for meta in shuffled_files:
            song_data = {
                "title": meta.get("title", os.path.splitext(os.path.basename(meta["path"]))[0]),
                "artist": meta.get("artist", "Local Music"),
                "duration": meta.get("duration", 0),
                "url": "",
                "webpage_url": "",
                "thumbnail": None,
                "is_local": True,
                "local_path": meta["path"]
            }
            new_songs.append(Song(song_data, interaction.user))

        player.queue.extend(new_songs)
        music_cog = self.bot.get_cog("Music")
        if not vc.is_playing() and not vc.is_paused():
            if music_cog:
                await music_cog.play_next(interaction.guild)
            await interaction.followup.send(f"🔀 Shuffled and started playing **{len(new_songs)} tracks**!")
        else:
            if music_cog:
                await music_cog.update_dashboard(interaction.guild)
            await interaction.followup.send(f"🔀 Added **{len(new_songs)} shuffled tracks** to the queue!")


class Library(commands.Cog):
    """Local music library browser, player, and queue management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

    @commands.hybrid_command(name="list", aliases=["library", "songs", "tracks", "locallist"], description="Browse all songs in the local music library with interactive controls.")
    @app_commands.describe(search="Optional search keyword to filter library songs")
    async def list_cmd(self, ctx: commands.Context, *, search: Optional[str] = None):
        files = get_local_music_files(filter_query=search)
        if not files:
            if search:
                return await ctx.reply(f"🔍 No local songs found matching **'{search}'**.")
            return await ctx.reply("📁 No local audio files found in `./music`, `./songs`, or `./audio` folders.")

        view = LibraryPaginationView(self.bot, files, page=0, query=search)
        await ctx.reply(embed=view.build_embed(), view=view)

    @commands.hybrid_command(name="shuffleall", aliases=["shuffle_all", "randomall"], description="Shuffle and play all songs from your local music library.")
    async def shuffleall(self, ctx: commands.Context):
        await ctx.defer()
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        files = get_local_music_files()
        if not files:
            return await ctx.reply("📁 No local audio files found in `./music`, `./songs`, or `./audio` folders.")

        random.shuffle(files)
        player = get_guild_player(self.bot, ctx.guild.id)
        player.text_channel = ctx.channel

        new_songs = []
        for meta in files:
            song_data = {
                "title": meta.get("title", os.path.splitext(os.path.basename(meta["path"]))[0]),
                "artist": meta.get("artist", "Local Music"),
                "duration": meta.get("duration", 0),
                "url": "",
                "webpage_url": "",
                "thumbnail": None,
                "is_local": True,
                "local_path": meta["path"]
            }
            new_songs.append(Song(song_data, ctx.author))

        player.queue.extend(new_songs)
        music_cog = self.bot.get_cog("Music")

        if not vc.is_playing() and not vc.is_paused():
            if music_cog:
                await music_cog.play_next(ctx.guild)
            await ctx.reply(f"🔀 Shuffled and queued **{len(new_songs)} local tracks** from your library!")
        else:
            if music_cog:
                await music_cog.update_dashboard(ctx.guild)
            await ctx.reply(f"🔀 Added **{len(new_songs)} shuffled local tracks** to the queue!")

    @commands.hybrid_command(name="playall", aliases=["queueall"], description="Queue all local library songs in order.")
    async def playall(self, ctx: commands.Context):
        await ctx.defer()
        vc = await self.ensure_voice_connection(ctx)
        if not vc:
            return

        files = get_local_music_files()
        if not files:
            return await ctx.reply("📁 No local audio files found in `./music`, `./songs`, or `./audio` folders.")

        player = get_guild_player(self.bot, ctx.guild.id)
        player.text_channel = ctx.channel

        new_songs = []
        for meta in files:
            song_data = {
                "title": meta.get("title", os.path.splitext(os.path.basename(meta["path"]))[0]),
                "artist": meta.get("artist", "Local Music"),
                "duration": meta.get("duration", 0),
                "url": "",
                "webpage_url": "",
                "thumbnail": None,
                "is_local": True,
                "local_path": meta["path"]
            }
            new_songs.append(Song(song_data, ctx.author))

        player.queue.extend(new_songs)
        music_cog = self.bot.get_cog("Music")

        if not vc.is_playing() and not vc.is_paused():
            if music_cog:
                await music_cog.play_next(ctx.guild)
            await ctx.reply(f"🎶 Queued and started playing **{len(new_songs)} local tracks**!")
        else:
            if music_cog:
                await music_cog.update_dashboard(ctx.guild)
            await ctx.reply(f"📥 Added **{len(new_songs)} local tracks** to the queue!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Library(bot))
