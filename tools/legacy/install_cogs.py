import os

os.makedirs("cogs", exist_ok=True)
os.makedirs("data", exist_ok=True)

# =====================================================================
# 1. cogs/tempvoice.py
# =====================================================================
TEMPVOICE_CODE = '''import discord
from discord.ext import commands
import json
import os
import asyncio
from utils.helpers import delete_after_delay

DATA_FILE = os.path.join("data", "tempvoice.json")

def load_data():
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=4)
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

class RenameRoomModal(discord.ui.Modal, title="Rename Voice Channel"):
    room_name = discord.ui.TextInput(
        label="New Channel Name",
        placeholder="e.g. Chill & Gaming",
        min_length=1,
        max_length=32
    )

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.channel.edit(name=self.room_name.value)
            await interaction.response.send_message(f"Channel renamed to **{self.room_name.value}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to rename: `{e}`", ephemeral=True)

class SetLimitModal(discord.ui.Modal, title="Set Member Limit"):
    limit = discord.ui.TextInput(
        label="Member Limit (0 for Unlimited)",
        placeholder="0-99",
        min_length=1,
        max_length=2
    )

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.limit.value)
            if val < 0 or val > 99:
                return await interaction.response.send_message("Limit must be between 0 and 99.", ephemeral=True)
            await self.channel.edit(user_limit=val)
            status = "Unlimited" if val == 0 else str(val)
            await interaction.response.send_message(f"Member limit set to **{status}**.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Please enter a valid number.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: `{e}`", ephemeral=True)

class KickUserSelect(discord.ui.UserSelect):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        self.channel = channel
        self.owner_id = owner_id
        super().__init__(placeholder="Select member to kick...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only the room owner can kick members.", ephemeral=True)

        target = self.values[0]
        if target.id == self.owner_id:
            return await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)

        member = self.channel.guild.get_member(target.id)
        if member and member in self.channel.members:
            try:
                await member.move_to(None, reason="Kicked by voice room owner")
                await self.channel.set_permissions(member, connect=False)
                await interaction.response.send_message(f"**{member.display_name}** has been kicked from the room.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"Failed to kick user: `{e}`", ephemeral=True)
        else:
            await interaction.response.send_message("That member is not in this voice channel.", ephemeral=True)

class TransferOwnerSelect(discord.ui.UserSelect):
    def __init__(self, channel: discord.VoiceChannel):
        self.channel = channel
        super().__init__(placeholder="Select new room owner...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        gid = str(self.channel.guild.id)
        cid = str(self.channel.id)
        current_owner = data.get(gid, {}).get("active_channels", {}).get(cid, {}).get("owner_id")

        if interaction.user.id != current_owner and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Only the current room owner can transfer ownership.", ephemeral=True)

        new_owner = self.values[0]
        if new_owner.id == current_owner:
            return await interaction.response.send_message("You are already the owner.", ephemeral=True)

        if gid in data and "active_channels" in data[gid] and cid in data[gid]["active_channels"]:
            data[gid]["active_channels"][cid]["owner_id"] = new_owner.id
            save_data(data)
            await interaction.response.send_message(f"Room ownership transferred to **{new_owner.mention}**!", ephemeral=False)

class TempVoiceControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _verify_owner(self, interaction: discord.Interaction) -> bool:
        data = load_data()
        gid = str(interaction.guild_id)
        cid = str(interaction.channel_id)
        owner_id = data.get(gid, {}).get("active_channels", {}).get(cid, {}).get("owner_id")

        if interaction.user.id == owner_id or interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Only the room owner can use these controls.", ephemeral=True)
        return False

    @discord.ui.button(label="Lock / Unlock", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="vc_btn_lock")
    async def lock_unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._verify_owner(interaction):
            return

        channel: discord.VoiceChannel = interaction.channel
        current_perm = channel.overwrites_for(interaction.guild.default_role).connect

        if current_perm is False:
            await channel.set_permissions(interaction.guild.default_role, connect=None)
            await interaction.response.send_message("Room is now **Public** (Unlocked).", ephemeral=True)
        else:
            await channel.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message("Room is now **Locked**.", ephemeral=True)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="vc_btn_rename")
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._verify_owner(interaction):
            return
        await interaction.response.send_modal(RenameRoomModal(interaction.channel))

    @discord.ui.button(label="Set Limit", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="vc_btn_limit")
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._verify_owner(interaction):
            return
        await interaction.response.send_modal(SetLimitModal(interaction.channel))

    @discord.ui.button(label="Kick User", style=discord.ButtonStyle.danger, emoji="🚫", custom_id="vc_btn_kick")
    async def kick_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._verify_owner(interaction):
            return
        view = discord.ui.View(timeout=60)
        view.add_item(KickUserSelect(interaction.channel, interaction.user.id))
        await interaction.response.send_message("Select a member to kick from your room:", view=view, ephemeral=True)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.primary, emoji="👑", custom_id="vc_btn_transfer")
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._verify_owner(interaction):
            return
        view = discord.ui.View(timeout=60)
        view.add_item(TransferOwnerSelect(interaction.channel))
        await interaction.response.send_message("Select who to transfer ownership to:", view=view, ephemeral=True)

class TempVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    async def cog_load(self):
        self.bot.add_view(TempVoiceControlView())

    @commands.command(name="setuptempvoice", aliases=["setupvoice", "voicehub"])
    @commands.guild_only()
    async def setup_temp_voice(self, ctx):
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("Only Administrators can configure temporary voice channels.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        status_msg = await ctx.send("Setting up Dynamic Voice Channel Hub...")

        try:
            category = await ctx.guild.create_category("Voice Rooms")
            hub_channel = await ctx.guild.create_voice_channel("➕ Join to Create", category=category)

            gid = str(ctx.guild.id)
            if gid not in self.data:
                self.data[gid] = {}

            self.data[gid]["category_id"] = category.id
            self.data[gid]["hub_id"] = hub_channel.id
            if "active_channels" not in self.data[gid]:
                self.data[gid]["active_channels"] = {}

            save_data(self.data)

            embed = discord.Embed(
                title="Join-to-Create Voice Setup Complete",
                description=(
                    f"• **Category:** `{category.name}`\\n"
                    f"• **Master Hub:** {hub_channel.mention}\\n\\n"
                    "Members joining the hub will get their own temporary voice room with control buttons!"
                ),
                color=0x2ECC71
            )
            await status_msg.edit(content=None, embed=embed)
            asyncio.create_task(delete_after_delay(status_msg, 15))

        except Exception as e:
            await status_msg.edit(content=f"Setup failed: `{e}`")
            asyncio.create_task(delete_after_delay(status_msg, 10))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or not member.guild:
            return

        gid = str(member.guild.id)
        guild_cfg = self.data.get(gid)
        if not guild_cfg:
            return

        hub_id = guild_cfg.get("hub_id")
        category_id = guild_cfg.get("category_id")
        active_channels = guild_cfg.get("active_channels", {})

        if after.channel and after.channel.id == hub_id:
            category = member.guild.get_channel(category_id)
            room_name = f"🔊 {member.display_name}'s Room"

            try:
                new_room = await member.guild.create_voice_channel(
                    name=room_name,
                    category=category,
                    user_limit=0
                )

                await member.move_to(new_room)

                active_channels[str(new_room.id)] = {
                    "owner_id": member.id,
                    "created_at": discord.utils.utcnow().isoformat()
                }
                guild_cfg["active_channels"] = active_channels
                save_data(self.data)

                panel_embed = discord.Embed(
                    title=f"🎮 {member.display_name}'s Room Controls",
                    description=(
                        f"Welcome to your private room, {member.mention}!\\n"
                        "Use the buttons below to customize and manage your room."
                    ),
                    color=0x5865F2
                )
                panel_embed.add_field(name="🔒 Access", value="Lock / unlock room", inline=True)
                panel_embed.add_field(name="👥 User Limit", value="Set participant limit", inline=True)
                panel_embed.add_field(name="✏️ Rename", value="Change room title", inline=True)
                panel_embed.add_field(name="🚫 Kick User", value="Remove a member", inline=True)
                panel_embed.add_field(name="👑 Transfer", value="Transfer ownership", inline=True)
                panel_embed.set_footer(text="Room will automatically delete when empty.")

                await new_room.send(embed=panel_embed, view=TempVoiceControlView())

            except Exception as e:
                print(f"[TempVoice Error] {e}")

        if before.channel and str(before.channel.id) in active_channels:
            temp_channel = before.channel
            human_members = [m for m in temp_channel.members if not m.bot]
            if len(human_members) == 0:
                try:
                    del active_channels[str(temp_channel.id)]
                    guild_cfg["active_channels"] = active_channels
                    save_data(self.data)
                    await temp_channel.delete(reason="Temporary voice room emptied")
                except Exception as e:
                    print(f"[TempVoice Error] {e}")

async def setup(bot):
    await bot.add_cog(TempVoice(bot))
'''

# =====================================================================
# 2. cogs/tickets.py
# =====================================================================
TICKETS_CODE = '''import discord
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

class CreateTicketModal(discord.ui.Modal, title="Open Support Ticket"):
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
        placeholder = "Select user to add..." if mode == "add" else "Select user to remove..."
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        channel: discord.TextChannel = interaction.channel
        member = interaction.guild.get_member(target.id)

        if not member:
            return await interaction.response.send_message("User not found in this server.", ephemeral=True)

        if self.mode == "add":
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
            await interaction.response.send_message(f"Added {member.mention} to this ticket.", ephemeral=False)
        else:
            await channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"Removed {member.mention} from this ticket.", ephemeral=False)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_btn_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View(timeout=30)
        confirm_btn = discord.ui.Button(label="Confirm Close", style=discord.ButtonStyle.danger, emoji="✅")
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")

        async def on_confirm(inter: discord.Interaction):
            await inter.response.send_message("Generating transcript and closing ticket...", ephemeral=False)
            cog = inter.client.get_cog("Tickets")
            if cog:
                await cog._close_ticket_channel(interaction.channel, inter.user)

        async def on_cancel(inter: discord.Interaction):
            await inter.response.send_message("Ticket closure cancelled.", ephemeral=True)

        confirm_btn.callback = on_confirm
        cancel_btn.callback = on_cancel
        confirm_view.add_item(confirm_btn)
        confirm_view.add_item(cancel_btn)

        await interaction.response.send_message("Are you sure you want to close this ticket?", view=confirm_view, ephemeral=True)

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="ticket_btn_transcript")
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("Tickets")
        if cog:
            file_data, filename = await cog._generate_transcript(interaction.channel)
            await interaction.followup.send(content="📄 Here is the ticket transcript:", file=discord.File(file_data, filename=filename), ephemeral=True)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.primary, emoji="➕", custom_id="ticket_btn_adduser")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_messages):
            return await interaction.response.send_message("Only staff can add members to tickets.", ephemeral=True)
        v = discord.ui.View(timeout=60)
        v.add_item(ManageTicketUserSelect(mode="add"))
        await interaction.response.send_message("Select a user to grant ticket access:", view=v, ephemeral=True)

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="ticket_btn_removeuser")
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_messages):
            return await interaction.response.send_message("Only staff can remove members from tickets.", ephemeral=True)
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
                "active_tickets": {}
            }
            save_json(DATA_FILE, self.data)
        return self.data[gid]

    @commands.command(name="ticketpanel", aliases=["settickets", "ticketsetup"])
    @commands.guild_only()
    async def post_ticket_panel(self, ctx, channel: discord.TextChannel = None):
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.author.guild_permissions.administrator:
            msg = await ctx.send("Only Administrators can deploy the ticket panel.")
            return asyncio.create_task(delete_after_delay(msg, 10))

        target_channel = channel or ctx.channel

        embed = discord.Embed(
            title="🎫 Support & Inquiries",
            description=(
                "Need help, have a question, or want to report an issue?\\n\\n"
                "Click the **Open Ticket** button below to create a private support ticket with server staff."
            ),
            color=0x5865F2
        )
        embed.add_field(name="Privacy", value="Tickets are private and visible only to you and staff.", inline=False)

        await target_channel.send(embed=embed, view=TicketLauncherView())
        confirm = await ctx.send(f"Ticket panel deployed to {target_channel.mention}!")
        asyncio.create_task(delete_after_delay(confirm, 8))

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
            embed.add_field(name="Creator", value=f"{interaction.user.mention}", inline=True)
            embed.add_field(name="Subject", value=subject, inline=True)
            embed.add_field(name="Issue Details", value=f"```text\\n{details}\\n```", inline=False)

            await ticket_channel.send(content=f"{interaction.user.mention} | Staff Team", embed=embed, view=TicketControlView())
            await interaction.response.send_message(f"Your ticket has been created: {ticket_channel.mention}", ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"Failed to create ticket: `{e}`", ephemeral=True)

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

        transcript_text = f"=== TICKET TRANSCRIPT: #{channel.name} ===\\n"
        transcript_text += f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\\n"
        transcript_text += f"Total Messages: {len(messages)}\\n"
        transcript_text += "=" * 45 + "\\n\\n"
        transcript_text += "\\n".join(messages)

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
                log_embed.add_field(name="Closed By", value=f"{closed_by.mention}", inline=True)
                log_embed.add_field(name="Subject", value=subject, inline=False)

                buffer.seek(0)
                try:
                    await log_channel.send(embed=log_embed, file=discord.File(buffer, filename=filename))
                except Exception:
                    pass

        if str(channel.id) in gdata.get("active_tickets", {}):
            del gdata["active_tickets"][str(channel.id)]
            save_json(DATA_FILE, self.data)

        await channel.send("🛑 **Ticket closed. Channel will delete in 5 seconds...**")
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket #{ticket_id} closed by {closed_by.name}")
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(Tickets(bot))
'''

# =====================================================================
# 3. cogs/playlists.py
# =====================================================================
PLAYLISTS_CODE = '''import discord
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
            description=f"Total saved songs: `{len(favs)}`\\nType `!playfavs` to queue your favorites!",
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
                "• `!playlist create <name>` - Create a new playlist\\n"
                "• `!playlist delete <name>` - Delete a playlist\\n"
                "• `!playlist add <name> [song/url]` - Add track (or current song)\\n"
                "• `!playlist remove <name> <#>` - Remove track # from playlist\\n"
                "• `!playlist list` (`!playlists`) - View your saved playlists\\n"
                "• `!playlist view <name>` - Show songs in playlist\\n"
                "• `!playlist play <name>` - Queue an entire playlist\\n\\n"
                "⭐ **One-Click Favorites:**\\n"
                "• `!fav` / `!favorite` - Save currently playing track\\n"
                "• `!favs` / `!favorites` - View favorite songs\\n"
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
                value=f"• `{len(tracks)}` songs\\n• Total: `{format_duration(total_dur)}`",
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
            description=f"Total: `{len(tracks)}` tracks • `{format_duration(total_dur)}`\\nType `!playlist play {name_clean}` to play!",
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
'''

# Write files cleanly
with open(os.path.join("cogs", "tempvoice.py"), "w", encoding="utf-8") as f:
    f.write(TEMPVOICE_CODE)
print(" Written cogs/tempvoice.py")

with open(os.path.join("cogs", "tickets.py"), "w", encoding="utf-8") as f:
    f.write(TICKETS_CODE)
print(" Written cogs/tickets.py")

with open(os.path.join("cogs", "playlists.py"), "w", encoding="utf-8") as f:
    f.write(PLAYLISTS_CODE)
print(" Written cogs/playlists.py")

print("\n All 3 cogs generated with 100% clean UTF-8 encoding!")
