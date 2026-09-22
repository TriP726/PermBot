import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import uuid

from utils.helpers import load_json, save_json, generate_gcal_link, parse_eastern_datetime, parse_duration, parse_dt_flexible, EASTERN_TZ

EVENTS_FILE = "data/events.json"

class EventRSVPView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)
        self.event_id = event_id

    async def handle_rsvp(self, interaction: discord.Interaction, status: str):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        if self.event_id not in events:
            return await interaction.response.send_message("❌ This event no longer exists.", ephemeral=True)

        event = events[self.event_id]
        user_id = str(interaction.user.id)

        for st in ["going", "maybe", "declined"]:
            if user_id in event.get(st, []):
                event[st].remove(user_id)

        event.setdefault(status, []).append(user_id)
        save_json(EVENTS_FILE, data)

        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name.startswith("✅ Going"):
                embed.set_field_at(i, name=f"✅ Going ({len(event.get('going', []))})", value=", ".join(f"<@{u}>" for u in event.get('going', [])) or "None", inline=True)
            elif field.name.startswith("❔ Maybe"):
                embed.set_field_at(i, name=f"❔ Maybe ({len(event.get('maybe', []))})", value=", ".join(f"<@{u}>" for u in event.get('maybe', [])) or "None", inline=True)
            elif field.name.startswith("❌ Declined"):
                embed.set_field_at(i, name=f"❌ Declined ({len(event.get('declined', []))})", value=", ".join(f"<@{u}>" for u in event.get('declined', [])) or "None", inline=True)

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Going", style=discord.ButtonStyle.success, custom_id="rsvp_going", emoji="✅")
    async def going_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "going")

    @discord.ui.button(label="Maybe", style=discord.ButtonStyle.secondary, custom_id="rsvp_maybe", emoji="❔")
    async def maybe_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "maybe")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="rsvp_decline", emoji="❌")
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_rsvp(interaction, "declined")

class EventDetailsModal(discord.ui.Modal, title="Create Server Event (USA EST)"):
    title_input = discord.ui.TextInput(
        label="Event Title",
        placeholder="Clan Raid / Oil Rig / Game Night",
        max_length=100,
        required=True
    )
    desc_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Brief details, kits needed, voice channel...",
        max_length=1000,
        required=False
    )
    date_input = discord.ui.TextInput(
        label="Date",
        placeholder="Today, Tomorrow, Friday, or MM/DD/YYYY",
        default="Today",
        max_length=20,
        required=True
    )
    time_input = discord.ui.TextInput(
        label="Time (Eastern Standard Time / EST)",
        placeholder="8:00 PM, 7:30pm, 19:00",
        default="8:00 PM",
        max_length=15,
        required=True
    )
    duration_input = discord.ui.TextInput(
        label="Duration",
        placeholder="2 hours, 90 mins, 1.5h",
        default="2 hours",
        max_length=15,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_et = parse_eastern_datetime(self.date_input.value, self.time_input.value)
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ **Invalid Date/Time:** {e}\n\n"
                "**Examples:**\n"
                "• Date: `Today`, `Tomorrow`, `Friday`, `10/25/2026`\n"
                "• Time: `8:00 PM`, `7:30pm`, `19:00` (EST/EDT)",
                ephemeral=True
            )

        dur_parsed = parse_duration(self.duration_input.value)
        duration_td = dur_parsed if dur_parsed else timedelta(hours=2)
        end_et = start_et + duration_td

        event_id = str(uuid.uuid4())[:8]
        title = self.title_input.value.strip()
        desc = self.desc_input.value.strip() or "No description provided."

        gcal_url = generate_gcal_link(title, desc, start_et, end_et)

        data = load_json(EVENTS_FILE, {"events": {}})
        data.setdefault("events", {})[event_id] = {
            "id": event_id,
            "guild_id": interaction.guild_id,
            "creator_id": interaction.user.id,
            "title": title,
            "description": desc,
            "start_iso": start_et.isoformat(),
            "end_iso": end_et.isoformat(),
            "going": [str(interaction.user.id)],
            "maybe": [],
            "declined": []
        }
        save_json(EVENTS_FILE, data)

        ts = int(start_et.timestamp())
        est_formatted = start_et.strftime("%a, %b %d @ %I:%M %p %Z")
        dur_str = f"{int(duration_td.total_seconds() // 3600)}h {(int(duration_td.total_seconds() % 3600) // 60)}m".replace(" 0m", "").strip()

        embed = discord.Embed(title=f"📅 {title}", description=desc, color=0x3498db)
        embed.add_field(name="⏰ Start Time (Local)", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
        embed.add_field(name="🇺🇸 Eastern Time", value=f"`{est_formatted}`", inline=True)
        embed.add_field(name="⏳ Duration", value=f"`{dur_str or '2h'}`", inline=True)
        embed.add_field(name="✅ Going (1)", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="❔ Maybe (0)", value="None", inline=True)
        embed.add_field(name="❌ Declined (0)", value="None", inline=True)
        embed.add_field(name="🔗 Add to Calendar", value=f"[Add to Google Calendar]({gcal_url})", inline=False)
        embed.set_footer(text=f"Event ID: {event_id} • Created by {interaction.user.display_name}")

        view = EventRSVPView(event_id=event_id)
        await interaction.response.send_message(embed=embed, view=view)

class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        data = load_json(EVENTS_FILE, {"events": {}})
        for event_id in data.get("events", {}):
            self.bot.add_view(EventRSVPView(event_id))

    @commands.hybrid_group(name="event", description="Manage server events in US Eastern Time.", fallback="help")
    @commands.guild_only()
    async def event_group(self, ctx: commands.Context):
        embed = discord.Embed(title="📅 US Eastern Event Manager", color=0x3498db)
        embed.add_field(name="`!event create` / `/event create`", value="Opens the EST event setup form.", inline=False)
        embed.add_field(name="`!event list` / `/event list`", value="Lists all scheduled events.", inline=False)
        embed.add_field(name="`!event delete <id>` / `/event delete <id>`", value="Deletes a scheduled event.", inline=False)
        await ctx.reply(embed=embed, ephemeral=True)

    @event_group.command(name="create", description="Create a new server event (EST timezone).")
    @commands.guild_only()
    async def event_create(self, ctx: commands.Context):
        if ctx.interaction:
            await ctx.interaction.response.send_modal(EventDetailsModal())
        else:
            view = discord.ui.View()
            btn = discord.ui.Button(label="Open Event Form (EST)", style=discord.ButtonStyle.primary, emoji="📝")

            async def btn_callback(interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
                await interaction.response.send_modal(EventDetailsModal())

            btn.callback = btn_callback
            view.add_item(btn)
            await ctx.reply("Click below to schedule your event in **US Eastern Time (EST)**:", view=view)

    @event_group.command(name="list", description="List all scheduled server events.")
    @commands.guild_only()
    async def event_list(self, ctx: commands.Context):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        guild_events = [e for e in events.values() if e.get("guild_id") == ctx.guild.id]

        if not guild_events:
            return await ctx.reply("📅 No events are currently scheduled.", ephemeral=True)

        embed = discord.Embed(title=f"📅 Scheduled Events ({len(guild_events)})", color=0x3498db)
        for ev in guild_events[:10]:
            st = parse_dt_flexible(ev.get("start_iso"))
            ts = int(st.timestamp())
            est_str = st.strftime("%b %d @ %I:%M %p %Z")
            going_cnt = len(ev.get("going", []))
            embed.add_field(
                name=f"{ev['title']} (ID: `{ev['id']}`)",
                value=f"⏰ <t:{ts}:F> (<t:{ts}:R>)\n🇺🇸 **EST:** `{est_str}`\n👥 **Going:** {going_cnt}\n📝 {ev['description'][:100]}",
                inline=False
            )
        await ctx.reply(embed=embed)

    @event_group.command(name="delete", description="Delete an event by ID.")
    @commands.guild_only()
    async def event_delete(self, ctx: commands.Context, event_id: str):
        data = load_json(EVENTS_FILE, {"events": {}})
        events = data.get("events", {})
        if event_id not in events:
            return await ctx.reply(f"❌ No event found with ID `{event_id}`.", ephemeral=True)

        ev = events[event_id]
        is_creator = ev.get("creator_id") == ctx.author.id
        is_admin = ctx.author.guild_permissions.administrator

        if not (is_creator or is_admin):
            return await ctx.reply("❌ You can only delete events you created unless you are an Administrator.", ephemeral=True)

        del events[event_id]
        save_json(EVENTS_FILE, data)
        await ctx.reply(f"✅ Successfully deleted event `{event_id}`.")

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
