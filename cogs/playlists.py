import discord
from discord.ext import commands
import json
import os
import asyncio
from utils.helpers import delete_after_delay

DATA_FILE = os.path.join("data", "playlists.json")

def load_data():
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": {}, "favorites": {}}, f, indent=4)
        return {"users": {}, "favorites": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "favorites": {}}

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def format_duration(seconds: int) -> str:
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

class Playlists(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    def _get_user_playlists(self, user_id: int) -> dict:
        uid = str(user_id)
        if "users" not in self.data:
            self.data["users"] = {}
        if uid not in self.data["users"]:
            self.data["users"][uid] = {}
        return self.data["users"][uid]

    def _get_user_favorites(self, user_id: int) -> list:
        uid = str(user_id)
        if "favorites" not in self.data:
            self.data["favorites"] = {}
        if uid not in self.data["favorites"]:
            self.data["favorites"][uid] = []
        return self.data["favorites"][uid]

    @commands.command(name="favorite", aliases=["fav", "star"])
    @commands.guild_only()
    async def favorite_current_song(self, ctx):
        music_cog = self.bot.get_cog("Music")
        player = None
        if music_cog:
            if hasattr(music_cog, "get_player"):
                player = music_cog.get_player(ctx.guild.id)
            elif hasattr(music_cog, "players"):
                player = music_cog.players.get(ctx.guild.id)

        if not player or not getattr(player, "current_track", None):
            msg = await ctx.send("❌ Nothing is currently playing to add to favorites.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        track = player.current_track
        favs = self._get_user_favorites(ctx.author.id)

        if any(f.get("url") == track.url or f.get("title") == track.title for f in favs):
            msg = await ctx.send(f"⭐ **{track.title}** is already in your Favorites!")
            return asyncio.create_task(delete_after_delay(msg, 10))

        fav_item = {
            "title": track.title,
            "url": getattr(track, "url", track.title),
            "duration": getattr(track, "duration", 0),
            "artist": getattr(track, "artist", "Unknown"),
            "is_local": getattr(track, "is_local", False)
        }
        favs.append(fav_item)
        save_data(self.data)

        msg = await ctx.send(f"⭐ Added **{track.title}** to your **Favorites** (`{len(favs)} total`)!")
        asyncio.create_task(delete_after_delay(msg, 10))

    @commands.command(name="favorites", aliases=["favs", "stars"])
    async def list_favorites(self, ctx):
        favs = self._get_user_favorites(ctx.author.id)
        if not favs:
            return await ctx.send("⭐ You haven't added any songs to your Favorites yet! Use `!fav` while listening to save songs.")

        embed = discord.Embed(
            title=f"⭐ {ctx.author.display_name}'s Favorite Tracks",
            description=f"Total saved songs: `{len(favs)}`\nType `!playfavs` to queue your favorites!",
            color=0xF1C40F
        )

        for i, f in enumerate(favs[:15], 1):
            dur = format_duration(f.get("duration", 0))
            embed.add_field(name=f"`{i}.` {f['title']}", value=f"⏱️ `{dur}` • {f.get('artist', 'Unknown Artist')}", inline=False)

        if len(favs) > 15:
            embed.set_footer(text=f"...and {len(favs) - 15} more tracks")

        await ctx.send(embed=embed)

    @commands.command(name="playfavs", aliases=["playfav", "favplay"])
    @commands.guild_only()
    async def play_favorites(self, ctx):
        favs = self._get_user_favorites(ctx.author.id)
        if not favs:
            return await ctx.send("❌ Your Favorites list is empty! Use `!fav` to add tracks.")

        play_cmd = self.bot.get_command("play")
        if not play_cmd:
            return await ctx.send("❌ Music player is not available.")

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ You must be connected to a voice channel to play favorites.")

        status_msg = await ctx.send(f"⏳ Queuing `{len(favs)}` songs from your Favorites...")

        count = 0
        for f in favs:
            q = f.get("url") or f.get("title")
            if q:
                try:
                    await ctx.invoke(play_cmd, query=q)
                    count += 1
                    await asyncio.sleep(0.2)
                except Exception:
                    continue

        await status_msg.edit(content=f"⭐ Successfully queued `{count}` songs from your **Favorites**!")
        asyncio.create_task(delete_after_delay(status_msg, 10))

    @commands.group(name="playlist", aliases=["pl"], invoke_without_command=True)
    async def playlist_group(self, ctx):
        embed = discord.Embed(
            title="📂 Custom Playlists & Favorites Guide",
            description=(
                "• `!playlist create <name>` - Create a new playlist\n"
                "• `!playlist delete <name>` - Delete a playlist\n"
                "• `!playlist add <name> [song/url]` - Add track (or current song)\n"
                "• `!playlist remove <name> <#>` - Remove track # from playlist\n"
                "• `!playlist list` (`!playlists`) - View your saved playlists\n"
                "• `!playlist view <name>` - Show songs in playlist\n"
                "• `!playlist play <name>` - Queue an entire playlist\n\n"
                "⭐ **One-Click Favorites:**\n"
                "• `!fav` / `!favorite` - Save currently playing track\n"
                "• `!favs` / `!favorites` - View favorite songs\n"
                "• `!playfavs` - Play your favorites list"
            ),
            color=0x3498DB
        )
        await ctx.send(embed=embed)

    @playlist_group.command(name="create", aliases=["new"])
    async def create_playlist(self, ctx, name: str):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean in user_pls:
            return await ctx.send(f"❌ You already have a playlist named `{name_clean}`.")

        user_pls[name_clean] = []
        save_data(self.data)
        await ctx.send(f"✅ Playlist **{name_clean}** created! Add songs with `!playlist add {name_clean} <song>`.")

    @playlist_group.command(name="delete", aliases=["del", "removepl"])
    async def delete_playlist(self, ctx, name: str):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean not in user_pls:
            return await ctx.send(f"❌ Playlist `{name_clean}` not found.")

        del user_pls[name_clean]
        save_data(self.data)
        await ctx.send(f"🗑️ Deleted playlist **{name_clean}**.")

    @playlist_group.command(name="add")
    @commands.guild_only()
    async def add_to_playlist(self, ctx, name: str, *, query: str = None):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean not in user_pls:
            return await ctx.send(f"❌ Playlist `{name_clean}` not found. Create it with `!playlist create {name_clean}`.")

        music_cog = self.bot.get_cog("Music")
        player = None
        if music_cog:
            if hasattr(music_cog, "get_player"):
                player = music_cog.get_player(ctx.guild.id)
            elif hasattr(music_cog, "players"):
                player = music_cog.players.get(ctx.guild.id)

        title = query
        url = query
        duration = 0
        artist = "Custom"
        is_local = False

        if not query:
            if not player or not getattr(player, "current_track", None):
                return await ctx.send("❌ No track currently playing. Specify a song: `!playlist add <name> <song>`")
            track = player.current_track
            title = track.title
            url = getattr(track, "url", track.title)
            duration = getattr(track, "duration", 0)
            artist = getattr(track, "artist", "Unknown")
            is_local = getattr(track, "is_local", False)

        item = {
            "title": title,
            "url": url,
            "duration": duration,
            "artist": artist,
            "is_local": is_local
        }
        user_pls[name_clean].append(item)
        save_data(self.data)

        await ctx.send(f"✅ Added **{title}** to playlist **{name_clean}** (`{len(user_pls[name_clean])}` songs total)!")

    @playlist_group.command(name="remove", aliases=["rem"])
    async def remove_from_playlist(self, ctx, name: str, index: int):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean not in user_pls:
            return await ctx.send(f"❌ Playlist `{name_clean}` not found.")

        tracks = user_pls[name_clean]
        if index < 1 or index > len(tracks):
            return await ctx.send(f"❌ Invalid song number. Choose between 1 and {len(tracks)}.")

        removed = tracks.pop(index - 1)
        save_data(self.data)
        await ctx.send(f"🗑️ Removed **{removed['title']}** from playlist **{name_clean}**.")

    @playlist_group.command(name="list", aliases=["all"])
    async def list_user_playlists(self, ctx):
        user_pls = self._get_user_playlists(ctx.author.id)
        if not user_pls:
            return await ctx.send("📂 You have no saved playlists. Create one with `!playlist create <name>`!")

        embed = discord.Embed(
            title=f"📂 {ctx.author.display_name}'s Playlists",
            color=0x3498DB
        )
        for pl_name, tracks in user_pls.items():
            total_dur = sum(t.get("duration", 0) for t in tracks)
            embed.add_field(
                name=f"📁 {pl_name.capitalize()}",
                value=f"• `{len(tracks)}` songs\n• Total: `{format_duration(total_dur)}`",
                inline=True
            )
        await ctx.send(embed=embed)

    @playlist_group.command(name="view", aliases=["show"])
    async def view_playlist(self, ctx, name: str):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean not in user_pls:
            return await ctx.send(f"❌ Playlist `{name_clean}` not found.")

        tracks = user_pls[name_clean]
        if not tracks:
            return await ctx.send(f"📁 Playlist **{name_clean}** is currently empty. Add songs with `!playlist add {name_clean} <song>`!")

        total_dur = sum(t.get("duration", 0) for t in tracks)
        embed = discord.Embed(
            title=f"📁 Playlist: {name_clean.capitalize()}",
            description=f"Total: `{len(tracks)}` tracks • `{format_duration(total_dur)}`\nType `!playlist play {name_clean}` to play!",
            color=0x3498DB
        )

        for i, t in enumerate(tracks[:15], 1):
            dur = format_duration(t.get("duration", 0))
            embed.add_field(name=f"`{i}.` {t['title']}", value=f"⏱️ `{dur}` • {t.get('artist', 'Unknown')}", inline=False)

        if len(tracks) > 15:
            embed.set_footer(text=f"...and {len(tracks) - 15} more songs")

        await ctx.send(embed=embed)

    @playlist_group.command(name="play")
    @commands.guild_only()
    async def play_playlist(self, ctx, name: str):
        user_pls = self._get_user_playlists(ctx.author.id)
        name_clean = name.lower().strip()

        if name_clean not in user_pls:
            return await ctx.send(f"❌ Playlist `{name_clean}` not found.")

        tracks = user_pls[name_clean]
        if not tracks:
            return await ctx.send(f"❌ Playlist `{name_clean}` has 0 songs.")

        play_cmd = self.bot.get_command("play")
        if not play_cmd:
            return await ctx.send("❌ Music player is not available.")

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ You must be in a voice channel to play playlists.")

        status_msg = await ctx.send(f"⏳ Loading `{len(tracks)}` songs from **{name_clean}**...")

        count = 0
        for t in tracks:
            q = t.get("url") or t.get("title")
            if q:
                try:
                    await ctx.invoke(play_cmd, query=q)
                    count += 1
                    await asyncio.sleep(0.2)
                except Exception:
                    continue

        await status_msg.edit(content=f"🎵 Successfully queued `{count}` songs from **{name_clean}**!")
        asyncio.create_task(delete_after_delay(status_msg, 10))

    @commands.command(name="playlists")
    async def playlists_alias(self, ctx):
        await self.list_user_playlists(ctx)

async def setup(bot):
    await bot.add_cog(Playlists(bot))
