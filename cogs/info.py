import discord
from discord.ext import commands
import os
from typing import Optional
from utils.helpers import load_json, format_duration

WARNINGS_PATH = os.path.join("data", "warnings.json")


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="userinfo", aliases=["ui", "whois", "user"])
    async def userinfo(self, ctx: commands.Context, *, member: Optional[discord.Member] = None):
        """Displays detailed profile information and server history for a member."""
        target = member or ctx.author

        # Calculate roles (excluding @everyone)
        roles = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
        roles_str = " ".join(roles[:15]) if roles else "No roles assigned"
        if len(target.roles) - 1 > 15:
            roles_str += f" *(+{len(target.roles) - 16} more)*"

        # Key Permissions
        key_perms = []
        if target.guild_permissions.administrator:
            key_perms.append("Administrator")
        if target.guild_permissions.manage_guild:
            key_perms.append("Manage Server")
        if target.guild_permissions.manage_channels:
            key_perms.append("Manage Channels")
        if target.guild_permissions.ban_members:
            key_perms.append("Ban Members")
        if target.guild_permissions.kick_members:
            key_perms.append("Kick Members")
        if target.guild_permissions.mention_everyone:
            key_perms.append("Mention Everyone")
        if target.guild_permissions.manage_messages:
            key_perms.append("Manage Messages")
        perms_str = ", ".join(key_perms) if key_perms else "Standard Member"

        # Warning count from warnings.json
        warn_data = load_json(WARNINGS_PATH, {})
        user_warns = warn_data.get(str(ctx.guild.id), {}).get(str(target.id), [])
        warn_count = len(user_warns)

        embed = discord.Embed(
            title=f"👤 User Profile — {target.display_name}",
            color=target.color if target.color.value != 0 else discord.Color.blurple()
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="📛 Username", value=f"`{target.name}`", inline=True)
        embed.add_field(name="🆔 User ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="🤖 Account Type", value="Bot" if target.bot else "Human", inline=True)

        embed.add_field(
            name="📅 Account Created",
            value=f"{discord.utils.format_dt(target.created_at, 'D')}\n({discord.utils.format_dt(target.created_at, 'R')})",
            inline=True
        )
        if target.joined_at:
            embed.add_field(
                name="📥 Joined Server",
                value=f"{discord.utils.format_dt(target.joined_at, 'D')}\n({discord.utils.format_dt(target.joined_at, 'R')})",
                inline=True
            )

        embed.add_field(name="⭐ Highest Role", value=target.top_role.mention, inline=True)
        embed.add_field(name="⚠️ Warnings on File", value=f"**{warn_count}** warnings", inline=True)
        embed.add_field(name="💎 Server Booster", value="Yes" if target.premium_since else "No", inline=True)
        embed.add_field(name="🛡️ Key Permissions", value=f"`{perms_str}`", inline=False)
        embed.add_field(name=f"🎭 Roles [{len(target.roles) - 1}]", value=roles_str, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo", aliases=["si", "server", "guildinfo"])
    async def serverinfo(self, ctx: commands.Context):
        """Displays comprehensive server analytics and settings."""
        guild = ctx.guild
        total_members = guild.member_count or len(guild.members)
        humans = len([m for m in guild.members if not m.bot])
        bots = len([m for m in guild.members if m.bot])

        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        stage_channels = len(guild.stage_channels)

        embed = discord.Embed(
            title=f"🏰 {guild.name}",
            description=guild.description or "No server description set.",
            color=discord.Color.blurple()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.add_field(name="👑 Server Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(
            name="📅 Created",
            value=f"{discord.utils.format_dt(guild.created_at, 'D')}\n({discord.utils.format_dt(guild.created_at, 'R')})",
            inline=True
        )

        embed.add_field(
            name=f"👥 Members ({total_members})",
            value=f"👤 Humans: `{humans}`\n🤖 Bots: `{bots}`",
            inline=True
        )
        embed.add_field(
            name=f"📁 Channels ({text_channels + voice_channels + stage_channels})",
            value=f"💬 Text: `{text_channels}`\n🔊 Voice: `{voice_channels}`\n📂 Categories: `{categories}`",
            inline=True
        )
        embed.add_field(
            name="✨ Boost Status",
            value=f"Tier: **Level {guild.premium_tier}**\nBoosts: `{guild.premium_subscription_count}`",
            inline=True
        )

        embed.add_field(name="🎭 Total Roles", value=f"`{len(guild.roles)}`", inline=True)
        embed.add_field(name="😀 Emojis / Stickers", value=f"`{len(guild.emojis)}` / `{len(guild.stickers)}`", inline=True)
        embed.add_field(name="🔒 Verification Level", value=f"`{str(guild.verification_level).capitalize()}`", inline=True)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="avatar", aliases=["av", "pfp"])
    async def avatar(self, ctx: commands.Context, *, member: Optional[discord.Member] = None):
        """Displays a member's high-resolution avatar with download links."""
        target = member or ctx.author
        av_url = target.display_avatar.url

        embed = discord.Embed(
            title=f"🖼️ Avatar — {target.display_name}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=av_url)
        embed.description = f"[Download PNG]({target.display_avatar.with_format('png').url}) • [Download JPG]({target.display_avatar.with_format('jpg').url}) • [Download WEBP]({target.display_avatar.with_format('webp').url})"
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Info(bot))
