import discord
from discord.ext import commands
import json
import os
import io
import asyncio
from datetime import datetime, timezone
from utils.helpers import delete_after_delay

DATA_FILE = os.path.join("data", "tickets.json")
MODCONFIG_FILE = os.path.join("data", "modconfig.json")

def load_json(path, default_data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        return default_data
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_data

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# ==========================================
# MODALS & VIEWS
# ==========================================
class CreateTicketModal(discord.ui.Modal, title="📩 Open Support Ticket"):
    subject = discord.ui.TextInput(
        label="Ticket Subject",
        placeholder="Brief description of the issue...",
        max_length=64,
        required=True
    )
    details = discord.ui.TextInput(
        label="Details / Message",
        placeholder="Explain what you need assistance with in detail...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog._create_ticket_channel(interaction, self.subject.value, self.details.value)

class ManageTicketUserSelect(discord.ui.UserSelect):
    def __init__(self, mode: str):
        self.mode = mode
        placeholder = "Select user to add to ticket..." if mode == "add" else "Select user to remove..."
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        channel: discord.TextChannel = interaction.channel
        member = interaction.guild.get_member(target.id)

        if not member:
            return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

        if self.mode == "add":
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
            await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.", ephemeral=False)
        else:
            await channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"🚫 Removed {member.mention} from this ticket.", ephemeral=False)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_btn_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View(timeout=30)
        confirm_btn = discord.ui.Button(label="Confirm Close", style=discord.ButtonStyle.danger, emoji="✅")
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")

        async def on_confirm(inter: discord.Interaction):
            await inter.response.send_message("💾 Generating transcript and closing ticket...", ephemeral=False)
            cog = inter.client.get_cog("Tickets")
            if cog:
                await cog._close_ticket_channel(interaction.channel, inter.user)

        async def on_cancel(inter: discord.Interaction):
            await inter.response.send_message("❌ Ticket closure cancelled.", ephemeral=True)

        confirm_btn.callback = on_confirm
        cancel_btn.callback = on_cancel
        confirm_view.add_item(confirm_btn)
        confirm_view.add_item(cancel_btn)

        await interaction.response.send_message("⚠️ Are you sure you want to close this ticket? A transcript will be saved.", view=confirm_view, ephemeral=True)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="ticket_btn_transcript")
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("Tickets")
        if cog:
            file_data, filename = await cog._generate_transcript(interaction.channel)
            await interaction.followup.send(content="📄 Here is the live ticket transcript:", file=discord.File(file_data, filename=filename), ephemeral=True)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.primary, emoji="➕", custom_id="ticket_btn_adduser")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_messages):
            return await interaction.response.send_message("❌ Only staff can add members to tickets.", ephemeral=True)
        v = discord.ui.View(timeout=60)
        v.add_item(ManageTicketUserSelect(mode="add"))
        await interaction.response.send_message("Select a user to grant ticket access:", view=v, ephemeral=True)

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="ticket_btn_removeuser")
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_messages):
            return await interaction.response.send_message("❌ Only staff can remove members from tickets.", ephemeral=True)
        v = discord.ui.View(timeout=60)
        v.add_item(ManageTicketUserSelect(mode="remove"))
        await interaction.response.send_message("Select a user to remove ticket access:", view=v, ephemeral=True)

class TicketLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="📩", custom_id="ticket_launch_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        if cog:
            await interaction.response.send_modal(CreateTicketModal(cog))

# ==========================================
# MAIN TICKETS COG
# ==========================================
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_json(DATA_FILE, {})

    async def cog_load(self):
        self.bot.add_view(TicketLauncherView())
        self.bot.add_view(TicketControlView())

    def _get_guild_data(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self.data:
            self.data[gid] = {
                "ticket_counter": 1,
                "category_id": None,
                "panel_channel_id": None,
                "active_tickets": {}
            }
            save_json(DATA_FILE, self.data)
        return self.data[gid]

    @commands.command(name="setuptickets", aliases=["ticketsetup", "ticketpanel", "settickets"])
    @commands.guild_only()
    async def setup_tickets(self, ctx, channel: discord.TextChannel = None):
        """1-Click Setup: Creates Support category, locked support channel & deploys panel."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("❌ Only Administrators can set up the ticket system.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        status_msg = await ctx.send("⚙️ Setting up Support Category & Channel...")
        gdata = self._get_guild_data(ctx.guild.id)

        try:
            category = None
            if gdata.get("category_id"):
                category = ctx.guild.get_channel(gdata["category_id"])

            if not category:
                cat_overwrites = {
                    ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    ctx.guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, manage_permissions=True)
                }
                category = await ctx.guild.create_category("🎫 Support", overwrites=cat_overwrites)
                gdata["category_id"] = category.id

            target_channel = channel
            if not target_channel:
                support_overwrites = {
                    ctx.guild.default_role: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=False,
                        read_message_history=True,
                        add_reactions=False,
                        create_public_threads=False,
                        create_private_threads=False,
                        send_messages_in_threads=False
                    ),
                    ctx.guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        embed_links=True,
                        attach_files=True,
                        manage_channels=True,
                        manage_permissions=True
                    )
                }
                target_channel = await ctx.guild.create_text_channel(
                    name="🎫・support",
                    category=category,
                    overwrites=support_overwrites,
                    topic="Open a private support ticket with server staff."
                )

            gdata["panel_channel_id"] = target_channel.id
            save_json(DATA_FILE, self.data)

            embed = discord.Embed(
                title="🎫 Server Support & Help Desk",
                description=(
                    "Need assistance, have questions, or need to reach server staff privately?\n\n"
                    "Click the **Open Ticket** button below to create your private support room."
                ),
                color=0x5865F2
            )
            embed.add_field(name="🔒 Private & Secure", value="Only you and server administrators can view your ticket.", inline=True)
            embed.add_field(name="📜 Full Transcripts", value="A complete transcript is saved upon closure.", inline=True)
            embed.set_footer(text="Staff team is ready to assist you • Click Open Ticket to begin")

            await target_channel.send(embed=embed, view=TicketLauncherView())

            res_embed = discord.Embed(
                title="✅ Support Ticket System Ready",
                description=(
                    f"• **Category:** `{category.name}`\n"
                    f"• **Support Channel:** {target_channel.mention}\n"
                    f"• **Permissions:** `@everyone` can view & click, but **only Admins can type**!\n\n"
                    "New tickets will automatically open in the `🎫 Support` category."
                ),
                color=0x2ECC71
            )
            await status_msg.edit(content=None, embed=res_embed)
            asyncio.create_task(delete_after_delay(status_msg, 15))

        except Exception as e:
            await status_msg.edit(content=f"❌ Failed to set up ticket system: `{e}`")
            asyncio.create_task(delete_after_delay(status_msg, 10))

    async def _create_ticket_channel(self, interaction: discord.Interaction, subject: str, details: str):
        gdata = self._get_guild_data(interaction.guild.id)
        counter = gdata.get("ticket_counter", 1)
        gdata["ticket_counter"] = counter + 1

        category_id = gdata.get("category_id")
        category = interaction.guild.get_channel(category_id) if category_id else None

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_permissions=True)
        }

        channel_name = f"ticket-{counter:04d}"
        try:
            ticket_channel = await interaction.guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{counter:04d} | Created by {interaction.user.name} ({interaction.user.id})"
            )

            gdata["active_tickets"][str(ticket_channel.id)] = {
                "user_id": interaction.user.id,
                "ticket_id": counter,
                "subject": subject,
                "opened_at": datetime.now(timezone.utc).isoformat()
            }
            save_json(DATA_FILE, self.data)

            embed = discord.Embed(
                title=f"🎫 Ticket #{counter:04d} — {subject}",
                description=f"Welcome {interaction.user.mention}! Support staff has been notified.",
                color=0x2ECC71,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Creator", value=f"{interaction.user.mention} (`{interaction.user.name}`)", inline=True)
            embed.add_field(name="Subject", value=subject, inline=True)
            embed.add_field(name="Issue Details", value=f"```text\n{details}\n```", inline=False)
            embed.set_footer(text="Use the buttons below to manage this ticket.")

            await ticket_channel.send(content=f"{interaction.user.mention} | Staff Team", embed=embed, view=TicketControlView())
            await interaction.response.send_message(f"✅ Your ticket has been created: {ticket_channel.mention}", ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to create ticket: `{e}`", ephemeral=True)

    async def _generate_transcript(self, channel: discord.TextChannel) -> tuple[io.BytesIO, str]:
        messages = []
        async for m in channel.history(limit=500, oldest_first=True):
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            author = f"{m.author.name} ({m.author.id})"
            content = m.clean_content or "[No text content]"
            if m.attachments:
                att_urls = ", ".join([a.url for a in m.attachments])
                content += f" [Attachments: {att_urls}]"
            messages.append(f"[{ts}] {author}: {content}")

        transcript_text = f"=== TICKET TRANSCRIPT: #{channel.name} ===\n"
        transcript_text += f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        transcript_text += f"Total Messages: {len(messages)}\n"
        transcript_text += "=" * 45 + "\n\n"
        transcript_text += "\n".join(messages)

        buffer = io.BytesIO(transcript_text.encode("utf-8"))
        filename = f"transcript-{channel.name}.txt"
        return buffer, filename

    async def _close_ticket_channel(self, channel: discord.TextChannel, closed_by: discord.User | discord.Member):
        gdata = self._get_guild_data(channel.guild.id)
        tinfo = gdata.get("active_tickets", {}).get(str(channel.id), {})

        creator_id = tinfo.get("user_id")
        ticket_id = tinfo.get("ticket_id", 0)
        subject = tinfo.get("subject", "Support")
        creator = channel.guild.get_member(creator_id) or await self.bot.fetch_user(creator_id) if creator_id else None

        buffer, filename = await self._generate_transcript(channel)

        if creator:
            try:
                dm_embed = discord.Embed(
                    title=f"🔒 Ticket Closed: #{ticket_id:04d}",
                    description=f"Your ticket **{subject}** in **{channel.guild.name}** has been closed by {closed_by.mention}.",
                    color=0xE74C3C,
                    timestamp=datetime.now(timezone.utc)
                )
                buffer.seek(0)
                await creator.send(embed=dm_embed, file=discord.File(buffer, filename=filename))
            except Exception:
                pass

        modconfig = load_json(MODCONFIG_FILE, {})
        log_channel_id = modconfig.get(str(channel.guild.id), {}).get("modlog_channel")
        if log_channel_id:
            log_channel = channel.guild.get_channel(log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title=f"🎫 Mod-Log: Ticket #{ticket_id:04d} Closed",
                    color=0x3498DB,
                    timestamp=datetime.now(timezone.utc)
                )
                log_embed.add_field(name="Ticket", value=f"`#{channel.name}`", inline=True)
                log_embed.add_field(name="Closed By", value=f"{closed_by.mention} (`{closed_by.id}`)", inline=True)
                log_embed.add_field(name="Subject", value=subject, inline=False)

                buffer.seek(0)
                try:
                    await log_channel.send(embed=log_embed, file=discord.File(buffer, filename=filename))
                except Exception:
                    pass

        if str(channel.id) in gdata.get("active_tickets", {}):
            del gdata["active_tickets"][str(channel.id)]
            save_json(DATA_FILE, self.data)

        await channel.send("🛑 **Ticket closed. Channel will automatically delete in 5 seconds...**")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket #{ticket_id} closed by {closed_by.name}")
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(Tickets(bot))
