import discord
from discord.ext import commands
import urllib.parse
from typing import Optional


class Utility(commands.Cog):
    """General utility tools and interactive components."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="qr", description="Generates a scannable QR code image from text or URL.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def qr_code(self, ctx: commands.Context, *, text_or_url: str):
        encoded = urllib.parse.quote(text_or_url)
        qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded}"

        embed = discord.Embed(
            title="📱 QR Code Generated",
            color=discord.Color.blue()
        )
        embed.set_image(url=qr_api_url)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="kys", description="Disconnects yourself (or a target member if Admin) from voice.")
    async def kys(self, ctx: commands.Context, target: Optional[discord.Member] = None):
        guild = ctx.guild
        if not guild:
            return

        is_admin = ctx.author.guild_permissions.administrator
        member_to_disconnect = target if (target and is_admin) else ctx.author

        if not member_to_disconnect.voice:
            await ctx.send(f"❌ {member_to_disconnect.mention} is not in a voice channel.", ephemeral=True)
            return

        try:
            await member_to_disconnect.move_to(None)
            await ctx.send(f"{member_to_disconnect.mention} Took The Easy Way Out.")
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to disconnect members from voice.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
