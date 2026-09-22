import os
import discord
from discord.ext import commands
from typing import List, Dict, Any, Optional
from utils.helpers import load_json, save_json, log

ROLEPANELS_FILE = "data/rolepanels.json"


# =====================================================================
# Persistent Role Button & View (Live Server Panel)
# =====================================================================
class PersistentRoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, style: discord.ButtonStyle = discord.ButtonStyle.primary, emoji: str = None):
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            custom_id=f"permbot_role_{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        # 1. Defer immediately (<100ms) to eliminate timeout errors
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This button can only be used inside a server.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if not role:
            await interaction.followup.send("❌ That role no longer exists.", ephemeral=True)
            return

        # 2. Check Bot Permissions & Role Hierarchy
        bot_member = guild.me
        if not bot_member.guild_permissions.manage_roles:
            await interaction.followup.send("❌ I do not have the **Manage Roles** permission.", ephemeral=True)
            return

        if role >= bot_member.top_role:
            await interaction.followup.send(
                f"❌ I cannot assign **{role.name}** because it is equal to or higher than my highest role.\n"
                "*(An Admin must move PermBot's role above this role in Server Settings > Roles)*",
                ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = await guild.fetch_member(interaction.user.id)

        # 3. Toggle Role (Add if missing, remove if present)
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="PermBot Self-Role Button Toggle")
                await interaction.followup.send(f"🗑️ Removed role: **{role.name}**", ephemeral=True)
            else:
                await member.add_roles(role, reason="PermBot Self-Role Button Toggle")
                await interaction.followup.send(f"✅ Added role: **{role.name}**", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Discord rejected the permission to edit your roles.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error updating role: `{e}`", ephemeral=True)


class PersistentRoleView(discord.ui.View):
    def __init__(self, role_configs: List[Dict[str, Any]]):
        super().__init__(timeout=None)  # timeout=None is required for reboot persistence
        for cfg in role_configs:
            self.add_item(
                PersistentRoleButton(
                    role_id=cfg["role_id"],
                    label=cfg.get("label", "Role"),
                    style=discord.ButtonStyle(cfg.get("style", 1)),
                    emoji=cfg.get("emoji")
                )
            )


# =====================================================================
# DM Setup Wizard UI Components (Modal, Selects, Buttons)
# =====================================================================
class PanelTextModal(discord.ui.Modal):
    def __init__(self, wizard_view: "RolePanelWizardView"):
        super().__init__(title="Customize Panel Title & Text")
        self.wizard_view = wizard_view

        self.title_input = discord.ui.TextInput(
            label="Panel Title",
            default=self.wizard_view.title,
            placeholder="e.g. Select Your Game Roles",
            max_length=100,
            required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="Panel Description",
            style=discord.TextStyle.paragraph,
            default=self.wizard_view.description,
            placeholder="e.g. Click the buttons below to toggle your roles:",
            max_length=1000,
            required=True
        )
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard_view.title = self.title_input.value.strip()
        self.wizard_view.description = self.desc_input.value.strip()
        await self.wizard_view.refresh_ui(interaction)


class ChannelSelectDropdown(discord.ui.Select):
    def __init__(self, channels: List[discord.TextChannel], current_channel: discord.TextChannel):
        options = [
            discord.SelectOption(
                label=f"#{ch.name}"[:100],
                value=str(ch.id),
                description=f"Category: {ch.category.name if ch.category else 'None'}"[:100],
                default=(ch.id == current_channel.id)
            )
            for ch in channels[:25]
        ]
        super().__init__(
            placeholder="📌 Select Target Channel...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        wizard: "RolePanelWizardView" = self.view
        ch_id = int(self.values[0])
        wizard.target_channel = wizard.guild.get_channel(ch_id)
        # Update default markers
        for opt in self.options:
            opt.default = (opt.value == str(ch_id))
        await wizard.refresh_ui(interaction)


class RoleSelectDropdown(discord.ui.Select):
    def __init__(self, roles: List[discord.Role], selected_role_ids: List[int]):
        options = [
            discord.SelectOption(
                label=r.name[:100],
                value=str(r.id),
                description=f"Members: {len(r.members)}",
                default=(r.id in selected_role_ids)
            )
            for r in roles[:25]
        ]
        super().__init__(
            placeholder="🎭 Select Roles (Choose up to 25)...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        wizard: "RolePanelWizardView" = self.view
        selected_ids = [int(v) for v in self.values]
        wizard.selected_roles = [wizard.guild.get_role(rid) for rid in selected_ids if wizard.guild.get_role(rid)]
        # Update default markers
        for opt in self.options:
            opt.default = (opt.value in self.values)
        await wizard.refresh_ui(interaction)


class RolePanelWizardView(discord.ui.View):
    def __init__(
        self,
        cog: "Roles",
        guild: discord.Guild,
        author: discord.Member,
        initial_channel: discord.TextChannel
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.author = author
        self.target_channel = initial_channel
        self.title = "🎭 Self-Service Roles"
        self.description = "Click the buttons below to toggle your roles:"
        self.selected_roles: List[discord.Role] = []
        self.message: Optional[discord.Message] = None

        # Filter valid text channels & assignable roles
        bot_top = guild.me.top_role
        available_channels = [
            c for c in guild.text_channels
            if c.permissions_for(guild.me).send_messages
        ]
        available_roles = [
            r for r in reversed(guild.roles)
            if r < bot_top and not r.is_default() and not r.managed
        ]

        if available_channels:
            self.add_item(ChannelSelectDropdown(available_channels, self.target_channel))
        if available_roles:
            self.add_item(RoleSelectDropdown(available_roles, []))

    def build_preview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🛠️ Role Panel Designer (DM Setup)",
            description="Configure your panel below. Changes update in real time.",
            color=discord.Color.blurple()
        )
        embed.add_field(name="🌐 Server", value=f"`{self.guild.name}`", inline=True)
        embed.add_field(name="📌 Target Channel", value=self.target_channel.mention if self.target_channel else "`None`", inline=True)
        embed.add_field(name="🏷️ Title", value=f"`{self.title}`", inline=False)
        embed.add_field(name="📝 Description", value=f"*{self.description}*", inline=False)

        if self.selected_roles:
            roles_preview = "\n".join(f"• {r.mention} (`{r.name}`)" for r in self.selected_roles)
            embed.add_field(name=f"🎭 Selected Roles ({len(self.selected_roles)})", value=roles_preview, inline=False)
        else:
            embed.add_field(name="🎭 Selected Roles (0)", value="*No roles selected yet. Use the dropdown below.*", inline=False)

        embed.set_footer(text="Use the buttons below to edit text, deploy to the server, or cancel.")
        return embed

    async def refresh_ui(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

    @discord.ui.button(label="Edit Title & Description", style=discord.ButtonStyle.secondary, emoji="📝", row=2)
    async def edit_text_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PanelTextModal(self))

    @discord.ui.button(label="Deploy Panel", style=discord.ButtonStyle.success, emoji="🚀", row=2)
    async def deploy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_roles:
            await interaction.response.send_message("❌ Please select at least one role from the dropdown menu first.", ephemeral=True)
            return

        if not self.target_channel:
            await interaction.response.send_message("❌ Please select a valid target channel.", ephemeral=True)
            return

        await interaction.response.defer()

        # Build final server panel
        role_configs = []
        styles = [
            discord.ButtonStyle.primary,
            discord.ButtonStyle.secondary,
            discord.ButtonStyle.success
        ]

        desc_lines = [self.description, ""]
        for idx, role in enumerate(self.selected_roles):
            style = styles[idx % len(styles)]
            role_configs.append({
                "role_id": role.id,
                "label": role.name,
                "style": int(style.value),
                "emoji": None
            })
            desc_lines.append(f"• {role.mention}")

        server_embed = discord.Embed(
            title=f"🎭 {self.title}",
            description="\n".join(desc_lines),
            color=discord.Color.blue()
        )
        server_embed.set_footer(text="PermBot Self-Service Roles • Click to Add/Remove")

        view = PersistentRoleView(role_configs)
        try:
            panel_msg = await self.target_channel.send(embed=server_embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ Failed to send panel to {self.target_channel.mention}. Missing permissions.", ephemeral=True)
            return

        # Save to database for reboot persistence
        if "panels" not in self.cog.data:
            self.cog.data["panels"] = {}

        self.cog.data["panels"][str(panel_msg.id)] = {
            "channel_id": self.target_channel.id,
            "guild_id": self.guild.id,
            "title": self.title,
            "roles": role_configs
        }
        save_json(ROLEPANELS_FILE, self.cog.data)

        # Disable wizard and show success in DM
        for child in self.children:
            child.disabled = True

        success_embed = discord.Embed(
            title="🎉 Role Panel Successfully Deployed!",
            description=(
                f"Your interactive role panel is now live in {self.target_channel.mention}!\n\n"
                f"🔗 **[Click here to jump to your panel]({panel_msg.jump_url})**"
            ),
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=success_embed, view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", row=2)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        cancel_embed = discord.Embed(
            title="❌ Setup Cancelled",
            description="The role panel setup was cancelled. No changes were made.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=cancel_embed, view=self)
        self.stop()


# =====================================================================
# Roles Cog
# =====================================================================
class Roles(commands.Cog):
    """Self-assignable button role panels with DM UI setup and manual admin role management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data: Dict[str, Any] = load_json(ROLEPANELS_FILE, default={"panels": {}})

    async def cog_load(self):
        """Re-registers all persistent views when the cog loads / bot starts."""
        panels = self.data.get("panels", {})
        registered = 0
        for msg_id, panel_data in panels.items():
            role_configs = panel_data.get("roles", [])
            if role_configs:
                self.bot.add_view(PersistentRoleView(role_configs), message_id=int(msg_id))
                registered += 1
        if registered > 0:
            log.info(f"Registered {registered} persistent role panel views.")

    # ------------------------------------------------------------------
    # 1. Interactive DM UI Setup Command
    # ------------------------------------------------------------------
    @commands.command(name="rolepanel", aliases=["selfroles", "rolespanel"])
    @commands.has_permissions(administrator=True)
    async def rolepanel_cmd(self, ctx: commands.Context):
        """
        Launches an interactive DM Setup UI to design and deploy a button role panel.
        """
        author = ctx.author
        guild = ctx.guild

        # Clean trigger message
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        try:
            dm_channel = await author.create_dm()
            wizard_view = RolePanelWizardView(
                cog=self,
                guild=guild,
                author=author,
                initial_channel=ctx.channel
            )
            wizard_msg = await dm_channel.send(embed=wizard_view.build_preview_embed(), view=wizard_view)
            wizard_view.message = wizard_msg

            # Send a self-deleting confirmation in server
            conf = await ctx.send(f"📬 {author.mention} I've sent you a DM with the interactive Role Panel UI designer!")
            await conf.delete(delay=5)
        except discord.Forbidden:
            await ctx.send(f"❌ {author.mention} I couldn't DM you! Please enable **Direct Messages from server members** in your Privacy Settings.")

    # ------------------------------------------------------------------
    # 2. Manual Member Role Assignment / Toggle Command
    # ------------------------------------------------------------------
    @commands.command(name="role", aliases=["giverole", "setrole", "togglerole"])
    @commands.has_permissions(administrator=True)
    async def manual_role(self, ctx: commands.Context, member: Optional[discord.Member] = None, *, role: Optional[discord.Role] = None):
        """
        Manually assigns, removes, or toggles a role for a specific member.
        Usage:
          !role @User @Role
          !role @User VIP
        """
        if member is None or role is None:
            embed = discord.Embed(
                title="👤 Manual Role Manager",
                description=(
                    "**Usage:** `!role @User @Role`\n\n"
                    "**Examples:**\n"
                    "`!role @Alex @VIP`\n"
                    "`!role @Sarah Moderator`"
                ),
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)
            return

        bot_member = ctx.guild.me
        if not bot_member.guild_permissions.manage_roles:
            await ctx.send("❌ I do not have the **Manage Roles** permission.")
            return

        # Check Bot Hierarchy
        if role >= bot_member.top_role:
            await ctx.send(
                f"❌ I cannot manage **{role.name}** because it is equal to or higher than my highest role (**{bot_member.top_role.name}**)."
            )
            return

        # Check Author Hierarchy (unless server owner)
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            await ctx.send(f"❌ You cannot assign or remove **{role.name}** because it is equal to or higher than your highest role.")
            return

        # Toggle or Assign
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Manual role removal by {ctx.author}")
                embed = discord.Embed(
                    description=f"🗑️ Removed {role.mention} from {member.mention}.",
                    color=discord.Color.orange()
                )
            else:
                await member.add_roles(role, reason=f"Manual role assignment by {ctx.author}")
                embed = discord.Embed(
                    description=f"✅ Added {role.mention} to {member.mention}.",
                    color=discord.Color.green()
                )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ Discord rejected the role update due to missing permissions.")
        except Exception as e:
            await ctx.send(f"⚠️ An error occurred: `{e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
