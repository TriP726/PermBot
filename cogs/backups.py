import discord
from discord.ext import commands, tasks
import os
import zipfile
from datetime import datetime, timezone
import asyncio
from utils.helpers import delete_after_delay

class Backups(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.backup_dir = "backups"
        self.data_dir = "data"
        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        self.auto_backup_loop.start()

    def cog_unload(self):
        self.auto_backup_loop.cancel()

    def _create_zip(self, tag: str = "manual") -> tuple[str, str, int, int]:
        """Compress the entire data/ folder into a timestamped zip archive."""
        now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"backup_{tag}_{now_str}.zip"
        filepath = os.path.join(self.backup_dir, filename)

        file_count = 0
        with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(self.data_dir):
                for file in files:
                    if not file.lower().endswith(".json"):
                        continue
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, start=self.data_dir)
                    zipf.write(full_path, arcname)
                    file_count += 1

        size_bytes = os.path.getsize(filepath)
        return filepath, filename, file_count, size_bytes

    def _prune_old_backups(self, max_keep: int = 30):
        """Keep only the latest N backup archives."""
        if not os.path.exists(self.backup_dir):
            return
        files = [
            os.path.join(self.backup_dir, f)
            for f in os.listdir(self.backup_dir)
            if f.endswith(".zip")
        ]
        files.sort(key=os.path.getmtime)
        while len(files) > max_keep:
            oldest = files.pop(0)
            try:
                os.remove(oldest)
            except Exception:
                pass

    # ==========================================
    # AUTOMATED 24-HOUR BACKGROUND TASK
    # ==========================================
    @tasks.loop(hours=24.0)
    async def auto_backup_loop(self):
        """Automated daily backup task."""
        try:
            filepath, filename, count, size = self._create_zip(tag="daily")
            self._prune_old_backups(max_keep=30)
            print(f"[Backups] Automated daily backup created: {filename} ({count} files, {size} bytes)")
        except Exception as e:
            print(f"[Backups Error] Auto-backup failed: {e}")

    @auto_backup_loop.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # ADMIN BACKUP COMMANDS
    # ==========================================
    @commands.group(name="backup", invoke_without_command=True)
    @commands.guild_only()
    async def backup_group(self, ctx):
        """Create an immediate database backup and send the .zip to admin's DMs."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("❌ Only Administrators can create backups.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        # Perform manual backup
        try:
            filepath, filename, file_count, size_bytes = self._create_zip(tag="manual")
            size_kb = round(size_bytes / 1024, 2)

            embed = discord.Embed(
                title="💾 Server Data Backup Snapshot",
                description=(
                    "Here is the latest complete snapshot of your bot's database (`data/` folder).\n"
                    "Keep this file safe as an off-site recovery backup."
                ),
                color=0x2ECC71,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="📦 Archive Name", value=f"`{filename}`", inline=False)
            embed.add_field(name="📄 Files Saved", value=f"`{file_count}` files", inline=True)
            embed.add_field(name="⚖️ Size", value=f"`{size_kb} KB`", inline=True)
            embed.add_field(name="Server", value=ctx.guild.name, inline=True)
            embed.set_footer(text="Automated daily backups run every 24 hours.")

            with open(filepath, "rb") as f:
                backup_file = discord.File(f, filename=filename)
                await ctx.author.send(embed=embed, file=backup_file)

            confirm = await ctx.send(f"📬 {ctx.author.mention}, a fresh database backup has been created and sent to your DMs!")
            asyncio.create_task(delete_after_delay(confirm, 8))

        except discord.Forbidden:
            err = await ctx.send(f"❌ {ctx.author.mention}, I could not send you a DM. Please enable direct messages in your Privacy settings.")
            asyncio.create_task(delete_after_delay(err, 10))
        except Exception as e:
            err = await ctx.send(f"❌ Backup failed: `{e}`")
            asyncio.create_task(delete_after_delay(err, 10))

    @backup_group.command(name="list", aliases=["all"])
    @commands.guild_only()
    async def list_backups(self, ctx):
        """List all available backup archives stored on disk."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("❌ Only Administrators can view backups.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        if not os.path.exists(self.backup_dir):
            return await ctx.author.send("📂 No backups exist yet.")

        files = [f for f in os.listdir(self.backup_dir) if f.endswith(".zip")]
        files.sort(reverse=True)

        if not files:
            msg = await ctx.author.send("📂 No backup archives found on disk.")
            return

        embed = discord.Embed(
            title="💾 Saved Database Backup Archives",
            description=f"Total backups stored: `{len(files)}` (Last 30 kept)",
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc)
        )

        for f in files[:15]:
            path = os.path.join(self.backup_dir, f)
            size_kb = round(os.path.getsize(path) / 1024, 2)
            mod_time = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            embed.add_field(
                name=f"📦 {f}",
                value=f"• **Size:** `{size_kb} KB`\n• **Created:** `{mod_time}`",
                inline=False
            )

        if len(files) > 15:
            embed.set_footer(text=f"...and {len(files) - 15} older archives")

        try:
            await ctx.author.send(embed=embed)
            confirm = await ctx.send(f"📬 {ctx.author.mention}, sent the backup catalog to your DMs!")
            asyncio.create_task(delete_after_delay(confirm, 6))
        except discord.Forbidden:
            msg = await ctx.send(f"❌ {ctx.author.mention}, unable to send DMs. Please check privacy settings.")
            asyncio.create_task(delete_after_delay(msg, 10))

    @backup_group.command(name="download", aliases=["get"])
    @commands.guild_only()
    async def download_backup(self, ctx, filename: str):
        """Download a specific backup archive by filename."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("❌ Only Administrators can download backups.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        if not filename.endswith(".zip"):
            filename += ".zip"

        filepath = os.path.join(self.backup_dir, filename)
        if not os.path.exists(filepath):
            msg = await ctx.send(f"❌ Backup file `{filename}` not found on disk. Run `!backup list` to see available files.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        try:
            with open(filepath, "rb") as f:
                await ctx.author.send(content=f"📦 Here is your requested backup: `{filename}`", file=discord.File(f, filename=filename))
            confirm = await ctx.send(f"📬 Sent `{filename}` to your DMs!")
            asyncio.create_task(delete_after_delay(confirm, 6))
        except Exception as e:
            msg = await ctx.send(f"❌ Failed to send backup: `{e}`")
            asyncio.create_task(delete_after_delay(msg, 10))

    @commands.command(name="backups")
    @commands.guild_only()
    async def backups_alias(self, ctx):
        """Shorthand alias for !backup list."""
        await self.list_backups(ctx)


async def setup(bot):
    await bot.add_cog(Backups(bot))
