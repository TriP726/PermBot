import os
import asyncio
from typing import Optional, Dict, Any
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput

from utils.helpers import load_json, save_json

APPLICATIONS_FILE = "data/applications.json"

def get_app_data() -> Dict[str, Any]:
    return load_json(APPLICATIONS_FILE, default={
        "guilds": {},       # guild_id: {"staff_role_id": int, "recruit_role_id": int, "category_id": int, "counter": int}
        "open_apps": {}     # channel_id: {"user_id": int, "guild_id": int, "app_num": int, "status": str}
    })

def save_app_data(data: Dict[str, Any]):
    save_json(APPLICATIONS_FILE, data)


# ==========================================
# APPLICATION RESUME MODAL
# ==========================================
class RustApplicationModal(Modal, title="Rust Team Application / Resume"):
    age_tz = TextInput(
        label="1. Age & Timezone",
        placeholder="e.g., 20 | EST (US East)",
        min_length=2,
        max_length=60,
        required=True
    )
    console_hours = TextInput(
        label="2. Console / Platform & Rust Hours",
        placeholder="e.g., PS5 | 3,200 hrs (or Xbox / PC)",
        min_length=2,
        max_length=60,
        required=True
    )
    desired_role = TextInput(
        label="3. Primary Role(s)",
        placeholder="e.g., Main PVPer, Base Builder, Monument Runner, Farmer, Electrician",
        min_length=2,
        max_length=100,
        required=True
    )
    activity_level = TextInput(
        label="4. Activity Level & Wipe Availability",
        style=discord.TextStyle.paragraph,
        placeholder="e.g., 5-8 hours daily, 100% active on Wipe Day (Thurs/Fri), weekend roamer",
        min_length=10,
        max_length=300,
        required=True
    )
    rust_experience = TextInput(
        label="5. Rust Experience & Clan Background",
        style=discord.TextStyle.paragraph,
        placeholder="Past clans, spray control, monument experience (Oil/Cargo/Launch), raid defenses, etc.",
        min_length=15,
        max_length=1000,
        required=True
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)

        data = get_app_data()
        g_id = str(guild.id)
        guild_conf = data["guilds"].setdefault(g_id, {
            "staff_role_id": None,
            "recruit_role_id": None,
            "category_id": None,
            "counter": 0
        })

        # Check if user already has an active application
        user_id = interaction.user.id
        for ch_id, app_info in data.get("open_apps", {}).items():
            if app_info.get("user_id") == user_id and app_info.get("guild_id") == guild.id:
                channel = guild.get_channel(int(ch_id))
                if channel:
                    return await interaction.response.send_message(
                        f"❌ You already have an open application ticket: {channel.mention}",
                        ephemeral=True
                    )

        guild_conf["counter"] += 1
        app_num = guild_conf["counter"]
        save_app_data(data)

        # Get or create Applications category
        category = None
        if guild_conf.get("category_id"):
            category = guild.get_channel(guild_conf["category_id"])

        if not category or not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name="📋・APPLICATIONS")
            if not category:
                category = await guild.create_category("📋・APPLICATIONS")
            guild_conf["category_id"] = category.id
            save_app_data(data)

        # Build Permissions
        staff_role = guild.get_role(guild_conf.get("staff_role_id")) if guild_conf.get("staff_role_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)

        clean_user_name = "".join(c for c in interaction.user.name.lower() if c.isalnum() or c in "-_")[:15]
        channel_name = f"app-{app_num:03d}-{clean_user_name}"

        try:
            app_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Rust Application for {interaction.user.name} ({interaction.user.id})"
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Bot lacks permission to create private application channels.", ephemeral=True)

        # Save active ticket record
        data["open_apps"][str(app_channel.id)] = {
            "user_id": interaction.user.id,
            "guild_id": guild.id,
            "app_num": app_num,
            "status": "pending"
        }
        save_app_data(data)

        # Build application embed
        embed = discord.Embed(
            title=f"📋 Application #{app_num:03d} • {interaction.user.display_name}",
            description=f"**Applicant:** {interaction.user.mention} (`{interaction.user.id}`)\n**Account Created:** <t:{int(interaction.user.created_at.timestamp())}:R>\n**Joined Server:** <t:{int(interaction.user.joined_at.timestamp()) if interaction.user.joined_at else 0}:R>",
            color=discord.Color.gold()
        )
        embed.add_field(name="🎂 Age & Timezone", value=self.age_tz.value, inline=True)
        embed.add_field(name="🎮 Console & Rust Hours", value=self.console_hours.value, inline=True)
        embed.add_field(name="⚔️ Desired Clan Role(s)", value=self.desired_role.value, inline=False)
        embed.add_field(name="⏰ Activity Level & Availability", value=self.activity_level.value, inline=False)
        embed.add_field(name="📜 Rust Experience & History", value=self.rust_experience.value, inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="Staff Review Panel • Use buttons below to process this application")

        staff_ping = staff_role.mention if staff_role else "@here"
        await app_channel.send(
            content=f"{interaction.user.mention} {staff_ping}",
            embed=embed,
            view=AppControlView()
        )

        await interaction.response.send_message(
            f"✅ Your application has been submitted! Please view your ticket: {app_channel.mention}",
            ephemeral=True
        )


# ==========================================
# REJECTION REASON MODAL
# ==========================================
class DenyReasonModal(Modal, title="Application Denial Reason"):
    reason = TextInput(
        label="Reason for Denial",
        style=discord.TextStyle.paragraph,
        placeholder="Explain why the application was declined (sent via DM)...",
        default="Thank you for applying, but we have decided to move forward with other applicants at this time.",
        max_length=500,
        required=True
    )

    def __init__(self, target_user: discord.User, channel: discord.TextChannel):
        super().__init__()
        self.target_user = target_user
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Send DM to applicant
        try:
            embed = discord.Embed(
                title="❌ Application Update",
                description=f"Your application for **{interaction.guild.name}** was not accepted.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=self.reason.value, inline=False)
            await self.target_user.send(embed=embed)
        except Exception:
            pass

        data = get_app_data()
        data["open_apps"].pop(str(self.channel.id), None)
        save_app_data(data)

        deny_embed = discord.Embed(
            title="🚫 Application Declined",
            description=f"Applicant {self.target_user.mention} was declined by {interaction.user.mention}.\n**Reason:** {self.reason.value}\n\n*This channel will be deleted in 10 seconds.*",
            color=discord.Color.red()
        )
        await self.channel.send(embed=deny_embed)
        await asyncio.sleep(10)
        try:
            await self.channel.delete()
        except Exception:
            pass


# ==========================================
# PERSISTENT APPLICATION CONTROLS (TICKET)
# ==========================================
class AppControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    def is_staff(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        data = get_app_data()
        role_id = data["guilds"].get(str(interaction.guild_id), {}).get("staff_role_id")
        if role_id and any(r.id == role_id for r in interaction.user.roles):
            return True
        return False

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="permbot:app_accept")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Only staff members can review applications.", ephemeral=True)

        data = get_app_data()
        app_info = data.get("open_apps", {}).get(str(interaction.channel_id))
        if not app_info:
            return await interaction.response.send_message("❌ Application record not found.", ephemeral=True)

        applicant = interaction.guild.get_member(app_info["user_id"])
        recruit_role_id = data["guilds"].get(str(interaction.guild_id), {}).get("recruit_role_id")
        recruit_role = interaction.guild.get_role(recruit_role_id) if recruit_role_id else None

        role_msg = ""
        if applicant and recruit_role:
            try:
                await applicant.add_roles(recruit_role, reason=f"Application #{app_info['app_num']} accepted by {interaction.user.name}")
                role_msg = f"\n🎉 Granted role: {recruit_role.mention}"
            except discord.Forbidden:
                role_msg = "\n⚠️ Failed to assign recruit role (check bot role hierarchy)."

        # DM applicant
        if applicant:
            try:
                dm_embed = discord.Embed(
                    title="🎉 Application Accepted!",
                    description=f"Congratulations! Your application for **{interaction.guild.name}** has been **Accepted** by {interaction.user.mention}!",
                    color=discord.Color.green()
                )
                await applicant.send(embed=dm_embed)
            except Exception:
                pass

        data["open_apps"].pop(str(interaction.channel_id), None)
        save_app_data(data)

        accept_embed = discord.Embed(
            title="✅ Application Accepted",
            description=f"Applicant {applicant.mention if applicant else 'Unknown'} has been **Accepted** by {interaction.user.mention}!{role_msg}\n\n*Closing ticket in 10 seconds...*",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=accept_embed)
        await asyncio.sleep(10)
        try:
            await interaction.channel.delete()
        except Exception:
            pass

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="permbot:app_deny")
    async def deny_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Only staff members can review applications.", ephemeral=True)

        data = get_app_data()
        app_info = data.get("open_apps", {}).get(str(interaction.channel_id))
        if not app_info:
            return await interaction.response.send_message("❌ Application record not found.", ephemeral=True)

        applicant = interaction.guild.get_member(app_info["user_id"])
        if not applicant:
            try:
                applicant = await interaction.client.fetch_user(app_info["user_id"])
            except Exception:
                applicant = None

        if not applicant:
            return await interaction.response.send_message("❌ Could not locate the applicant user.", ephemeral=True)

        await interaction.response.send_modal(DenyReasonModal(target_user=applicant, channel=interaction.channel))

    @discord.ui.button(label="Interview", style=discord.ButtonStyle.primary, emoji="🎙️", custom_id="permbot:app_interview")
    async def interview_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Only staff members can request interviews.", ephemeral=True)

        data = get_app_data()
        app_info = data.get("open_apps", {}).get(str(interaction.channel_id))
        applicant_id = app_info["user_id"] if app_info else None
        mention_str = f"<@{applicant_id}>" if applicant_id else "Applicant"

        embed = discord.Embed(
            title="🎙️ Voice Interview Requested",
            description=f"Hey {mention_str}, the recruitment team ({interaction.user.mention}) would like to conduct a brief voice interview with you!\n\nPlease reply here with when you are free to hop in a voice channel.",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(content=mention_str, embed=embed)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="permbot:app_close")
    async def close_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Only staff members can close this ticket.", ephemeral=True)

        data = get_app_data()
        data["open_apps"].pop(str(interaction.channel_id), None)
        save_app_data(data)

        await interaction.response.send_message("🔒 Closing application ticket in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception:
            pass


# ==========================================
# PERSISTENT RECRUITMENT PANEL VIEW
# ==========================================
class AppLaunchView(View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Apply / Resume", style=discord.ButtonStyle.success, emoji="📝", custom_id="permbot:apply_launch_btn")
    async def apply_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(RustApplicationModal(self.bot))


# ==========================================
# APPLICATIONS COG & COMMANDS
# ==========================================
class Applications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Register persistent views on cog load
        self.bot.add_view(AppLaunchView(self.bot))
        self.bot.add_view(AppControlView())

    @commands.hybrid_command(name="apppanel", description="Deploy the Rust recruitment application panel.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(
        channel="Channel to deploy the panel in (defaults to current channel)",
        title="Custom embed title",
        description="Custom embed description instructions"
    )
    async def apppanel(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        title: Optional[str] = "⚔️ Rust Clan Recruitment & Application",
        description: Optional[str] = "Interested in joining our roster? Click the **Apply / Resume** button below to submit your application!"
    ):
        target_channel = channel or ctx.channel

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.purple()
        )
        embed.add_field(
            name="📋 Requirements",
            value="• Working Microphone & TeamSpeak/Discord\n• Consistent Wipe Day activity\n• Accurate total hours & platform details\n• Mature, no-toxicity clan mindset",
            inline=False
        )
        embed.add_field(
            name="📝 Application Fields",
            value="1️⃣ Age & Timezone\n2️⃣ Console / Platform & Rust Hours\n3️⃣ Primary Role(s)\n4️⃣ Activity Level & Availability\n5️⃣ Rust Experience & Past Clans",
            inline=False
        )
        embed.set_footer(text="PermBot Recruitment Manager • Click below to apply")

        await target_channel.send(embed=embed, view=AppLaunchView(self.bot))
        if target_channel.id != ctx.channel.id:
            await ctx.send(f"✅ Recruitment panel deployed to {target_channel.mention}.", ephemeral=True)
        else:
            if ctx.interaction:
                await ctx.send("✅ Recruitment panel deployed successfully!", ephemeral=True)

    @commands.hybrid_command(name="appsetstaff", description="Set the staff/reviewer role for applications.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(role="Role that can review and manage application tickets")
    async def appsetstaff(self, ctx: commands.Context, role: discord.Role):
        data = get_app_data()
        g_id = str(ctx.guild.id)
        guild_conf = data["guilds"].setdefault(g_id, {
            "staff_role_id": None,
            "recruit_role_id": None,
            "category_id": None,
            "counter": 0
        })
        guild_conf["staff_role_id"] = role.id
        save_app_data(data)
        await ctx.send(f"✅ Application reviewer staff role set to {role.mention}.")

    @commands.hybrid_command(name="appsetrole", description="Set the recruit/member role granted upon application acceptance.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(role="Role automatically given when an applicant is accepted")
    async def appsetrole(self, ctx: commands.Context, role: discord.Role):
        data = get_app_data()
        g_id = str(ctx.guild.id)
        guild_conf = data["guilds"].setdefault(g_id, {
            "staff_role_id": None,
            "recruit_role_id": None,
            "category_id": None,
            "counter": 0
        })
        guild_conf["recruit_role_id"] = role.id
        save_app_data(data)
        await ctx.send(f"✅ Accepted applicant recruit role set to {role.mention}.")

    @commands.hybrid_command(name="appclose", description="Close the active application channel.")
    @commands.has_permissions(administrator=True)
    async def appclose(self, ctx: commands.Context):
        data = get_app_data()
        ch_id = str(ctx.channel.id)
        if ch_id not in data.get("open_apps", {}):
            return await ctx.send("❌ This channel is not an active application ticket.", ephemeral=True)

        data["open_apps"].pop(ch_id, None)
        save_app_data(data)
        await ctx.send("🔒 Closing application ticket in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await ctx.channel.delete()
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Applications(bot))
