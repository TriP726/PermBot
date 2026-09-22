import discord
from discord.ext import commands
import os
import sys
import asyncio
import logging
from typing import Optional
from utils.helpers import delete_after_delay

logger = logging.getLogger("DiscordBot.Admin")


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="reload")
    @commands.has_permissions(administrator=True)
    async def reload_cog(self, ctx: commands.Context, cog_name: Optional[str] = None):
        """Reloads a specific cog or all cogs dynamically."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        cogs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cogs")
        reloaded = []
        failed = []

        if cog_name:
            target = cog_name.lower().replace("cogs.", "")
            try:
                await self.bot.reload_extension(f"cogs.{target}")
                reloaded.append(target)
            except Exception as e:
                failed.append((target, str(e)))
        else:
            for filename in sorted(os.listdir(cogs_dir)):
                if filename.endswith(".py") and not filename.startswith("__"):
                    name = filename[:-3]
                    try:
                        await self.bot.reload_extension(f"cogs.{name}")
                        reloaded.append(name)
                    except Exception as e:
                        # If not loaded yet, try loading it
                        try:
                            await self.bot.load_extension(f"cogs.{name}")
                            reloaded.append(name)
                        except Exception as e2:
                            failed.append((name, str(e2)))

        embed = discord.Embed(title="🔄 System Reload", color=discord.Color.blurple())
        if reloaded:
            embed.add_field(name="✅ Reloaded Extensions", value=", ".join([f"`{c}`" for c in reloaded]), inline=False)
        if failed:
            err_lines = [f"`{c}`: {err}" for c, err in failed]
            embed.add_field(name="❌ Failed Extensions", value="\n".join(err_lines), inline=False)

        try:
            await ctx.author.send(embed=embed)
            confirm = await ctx.send(f"🔄 Reloaded `{len(reloaded)}` module(s). Sent report to your DMs.")
            asyncio.create_task(delete_after_delay(confirm, 6))
        except Exception:
            await ctx.send(embed=embed)

    @commands.command(name="dm")
    @commands.has_permissions(administrator=True)
    async def dm_member(self, ctx: commands.Context, member: discord.Member, *, message: str):
        """Sends a direct private message to a member through the bot."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title=f"📨 Message from {ctx.guild.name} Staff",
            description=message,
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Official server communication")

        try:
            await member.send(embed=embed)
            confirm = await ctx.send(f"✅ Successfully sent DM to **{member.display_name}**.")
            asyncio.create_task(delete_after_delay(confirm, 8))
        except discord.Forbidden:
            await ctx.author.send(f"❌ Could not DM **{member.display_name}**. Their DMs are closed or they blocked the bot.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
