import discord
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
                    f"• **Category:** `{category.name}`\n"
                    f"• **Master Hub:** {hub_channel.mention}\n\n"
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
                        f"Welcome to your private room, {member.mention}!\n"
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
