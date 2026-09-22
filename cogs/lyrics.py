import urllib.parse
from typing import Optional
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

from cogs.player_state import guild_players

class Lyrics(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def fetch_lyrics(self, track_name: str) -> Optional[str]:
        # Clean track name (remove extensions and bracketed tags)
        cleaned = track_name
        for ext in ('.mp3', '.wav', '.ogg', '.m4a', '.flac', '.mp4', '.mkv', '.webm'):
            if cleaned.lower().endswith(ext):
                cleaned = cleaned[:-len(ext)]

        encoded_query = urllib.parse.quote(cleaned)
        url = f"https://lrclib.net/api/search?q={encoded_query}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            item = data[0]
                            return item.get("plainLyrics") or item.get("syncedLyrics")
        except Exception:
            pass
        return None

    @commands.hybrid_command(name="lyrics", description="Look up lyrics for the current song or a specific title.")
    @app_commands.describe(query="Song title to look up (leave blank for currently playing track)")
    async def lyrics(self, ctx: commands.Context, *, query: Optional[str] = None):
        target_title = query
        if not target_title and ctx.guild:
            player = guild_players.get(ctx.guild.id)
            if player and player.current_track:
                target_title = player.current_track.title

        if not target_title:
            return await ctx.send("❌ No track currently playing and no song title was specified.", ephemeral=True)

        await ctx.defer()
        lyrics_text = await self.fetch_lyrics(target_title)

        if not lyrics_text:
            return await ctx.send(f"❌ Lyrics for **{target_title}** could not be found.")

        chunks = [lyrics_text[i:i + 4000] for i in range(0, len(lyrics_text), 4000)]
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📜 Lyrics: {target_title}" if i == 0 else f"📜 Lyrics: {target_title} (Part {i + 1})",
                description=chunk,
                color=discord.Color.purple()
            )
            embed.set_footer(text="Lyrics provided by LRCLIB")
            await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Lyrics(bot))
