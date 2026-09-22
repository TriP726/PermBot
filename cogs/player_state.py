"""
cogs/player_state.py - PermBot Guild Player State & Sticky Interactive UI
"""

import time
import asyncio
import discord
from discord.ext import commands
from typing import Dict, List, Optional, Any
from utils.audio import create_audio_source, EQ_PRESETS, EQ_DESCRIPTIONS


class Song:
    def __init__(self, data: Dict[str, Any], requester: discord.Member):
        self.title: str = data.get("title", "Unknown Title")
        self.url: str = data.get("url", "")
        self.webpage_url: str = data.get("webpage_url", self.url)
        self.duration: int = int(data.get("duration", 0) or 0)
        self.thumbnail: Optional[str] = data.get("thumbnail")
        self.artist: str = data.get("artist", data.get("uploader", "Unknown Artist"))
        self.is_local: bool = data.get("is_local", False)
        self.local_path: Optional[str] = data.get("local_path")
        self.requester: discord.Member = requester


class GuildPlayer:
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.loop_mode: str = "off"
        self.volume: float = 1.0
        self.eq_preset: str = "studio"
        self.paused: bool = False
        self.skip_requested: bool = False
        self.is_reloading: bool = False
        self.play_id: int = 0
        self.start_timestamp: float = 0.0
        self.pause_timestamp: float = 0.0
        self.total_paused_duration: float = 0.0
        self.text_channel: Optional[discord.TextChannel] = None
        self.dashboard_message: Optional[discord.Message] = None
        self._lock = asyncio.Lock()
        self._sticky_lock = asyncio.Lock()
        self._sticky_task: Optional[asyncio.Task] = None

    def get_position(self) -> float:
        if not self.current or self.start_timestamp == 0.0:
            return 0.0
        if self.paused:
            return max(0.0, self.pause_timestamp - self.start_timestamp - self.total_paused_duration)
        return max(0.0, time.time() - self.start_timestamp - self.total_paused_duration)

    def reset_track_timing(self, offset: float = 0.0):
        self.start_timestamp = time.time() - offset
        self.pause_timestamp = 0.0
        self.total_paused_duration = 0.0
        self.paused = False


guild_players: Dict[int, GuildPlayer] = {}

def get_guild_player(bot: commands.Bot, guild_id: int) -> GuildPlayer:
    if guild_id not in guild_players:
        guild_players[guild_id] = GuildPlayer(bot, guild_id)
    player = guild_players[guild_id]
    if not hasattr(player, "play_id"):
        player.play_id = 0
    if not hasattr(player, "is_reloading"):
        player.is_reloading = False
    if not hasattr(player, "_lock"):
        player._lock = asyncio.Lock()
    if not hasattr(player, "_sticky_lock"):
        player._sticky_lock = asyncio.Lock()
    if not hasattr(player, "_sticky_task"):
        player._sticky_task = None
    if not hasattr(player, "eq_preset"):
        player.eq_preset = "studio"
    if not hasattr(player, "volume"):
        player.volume = 1.0
    if not hasattr(player, "loop_mode"):
        player.loop_mode = "off"
    if not hasattr(player, "skip_requested"):
        player.skip_requested = False
    return player


class EQDropdown(discord.ui.Select):
    def __init__(self, current_eq: str = "studio"):
        options = [
            discord.SelectOption(label="Studio Master (Hi-Fi)", value="studio", description="Audiophile clarity & balanced spectrum.", emoji="🎧", default=(current_eq == "studio")),
            discord.SelectOption(label="Punchy Bass Boost", value="bass", description="Clean low-end with soft limiter.", emoji="🔊", default=(current_eq == "bass")),
            discord.SelectOption(label="Sub-Bass Drive", value="heavybass", description="Subwoofer power without clipping.", emoji="💣", default=(current_eq == "heavybass")),
            discord.SelectOption(label="Vocal & Acoustic", value="vocal", description="Speech & acoustic instrument clarity.", emoji="🎙️", default=(current_eq == "vocal")),
            discord.SelectOption(label="Electronic / EDM", value="electronic", description="Punchy transients & synth crispness.", emoji="⚡", default=(current_eq == "electronic")),
            discord.SelectOption(label="Rock / Metal", value="rock", description="Warm rhythm bass & driving guitars.", emoji="🎸", default=(current_eq == "rock")),
            discord.SelectOption(label="Airy Treble Sparkle", value="treble", description="High-frequency shimmer & strings.", emoji="✨", default=(current_eq == "treble")),
            discord.SelectOption(label="8D Surround Experience", value="8d", description="360-degree rotating binaural audio.", emoji="🌀", default=(current_eq == "8d")),
            discord.SelectOption(label="Nightcore Fast", value="nightcore", description="+25% Speed & Pitch with polish.", emoji="🌙", default=(current_eq == "nightcore")),
            discord.SelectOption(label="Vaporwave Lo-Fi", value="vaporwave", description="-18% Slowed & Reverb tape texture.", emoji="📼", default=(current_eq == "vaporwave")),
            discord.SelectOption(label="Auto-Leveler", value="normalized", description="Dynamic volume balancing across tracks.", emoji="📊", default=(current_eq == "normalized")),
            discord.SelectOption(label="Pure Flat / Off", value="off", description="Bit-perfect flat audio with limiter.", emoji="❌", default=(current_eq in ("off", "flat"))),
        ]
        super().__init__(placeholder="🎚️ Select Audio Quality Profile / EQ...", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = get_guild_player(interaction.client, interaction.guild_id)
        player.eq_preset = self.values[0]
        music_cog = interaction.client.get_cog("Music")
        if music_cog:
            await music_cog.reload_current_stream(interaction.guild)


class SeekDropdown(discord.ui.Select):
    def __init__(self, duration: int, current_pos: float):
        options = []
        if duration <= 0:
            options.append(discord.SelectOption(label="Live Stream (Cannot Seek)", value="0", emoji="🔴"))
        else:
            jump_percentages = [0.10, 0.25, 0.50, 0.75, 0.90]
            for pct in jump_percentages:
                sec = int(duration * pct)
                m, s = divmod(sec, 60)
                h, m = divmod(m, 60)
                time_str = f"{h:d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
                label = f"Jump to {int(pct*100)}% ({time_str})"
                options.append(discord.SelectOption(label=label, value=str(sec), emoji="⏩"))

        super().__init__(placeholder="⏩ Fast Seek Timeline...", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        target_sec = float(self.values[0])
        music_cog = interaction.client.get_cog("Music")
        if music_cog:
            await music_cog.reload_current_stream(interaction.guild, seek_seconds=target_sec)


class PlayerView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        player = get_guild_player(bot, guild_id)
        dur = player.current.duration if player.current else 0
        pos = player.get_position()

        self.add_item(EQDropdown(current_eq=player.eq_preset))
        self.add_item(SeekDropdown(duration=dur, current_pos=pos))

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0, custom_id="player_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        music_cog = self.bot.get_cog("Music")
        if music_cog:
            await music_cog.reload_current_stream(interaction.guild, seek_seconds=0.0)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, row=0, custom_id="player_playpause")
    async def playpause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing():
                vc.pause()
                player.paused = True
                player.pause_timestamp = time.time()
            elif vc.is_paused():
                vc.resume()
                player.paused = False
                if player.pause_timestamp > 0:
                    player.total_paused_duration += (time.time() - player.pause_timestamp)
                    player.pause_timestamp = 0.0
            await self.bot.get_cog("Music").update_dashboard(interaction.guild, force_repost=False)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="player_skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            player.skip_requested = True
            vc.stop()

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0, custom_id="player_stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        vc = interaction.guild.voice_client
        player.queue.clear()
        player.current = None
        if vc:
            vc.stop()
            await vc.disconnect()
        await self.bot.get_cog("Music").update_dashboard(interaction.guild, force_repost=False)
        await self.bot.get_cog("Music").update_presence(None)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1, custom_id="player_loop")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        modes = ["off", "song", "queue"]
        idx = (modes.index(player.loop_mode) + 1) % len(modes)
        player.loop_mode = modes[idx]
        await self.bot.get_cog("Music").update_dashboard(interaction.guild, force_repost=False)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1, custom_id="player_shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        import random
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        if len(player.queue) > 1:
            random.shuffle(player.queue)
        await self.bot.get_cog("Music").update_dashboard(interaction.guild, force_repost=False)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=1, custom_id="player_voldown")
    async def voldown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        player.volume = max(0.1, round(player.volume - 0.1, 2))
        music_cog = self.bot.get_cog("Music")
        if music_cog:
            await music_cog.reload_current_stream(interaction.guild)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="player_volup")
    async def volup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        player = get_guild_player(self.bot, self.guild_id)
        player.volume = min(2.0, round(player.volume + 0.1, 2))
        music_cog = self.bot.get_cog("Music")
        if music_cog:
            await music_cog.reload_current_stream(interaction.guild)


async def setup(bot: commands.Bot):
    pass
