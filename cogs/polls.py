import discord
from discord.ext import commands
import asyncio
import os
import shlex
from typing import Dict, List, Optional
from utils.helpers import load_json, save_json, create_progress_bar

POLLS_PATH = os.path.join("data", "polls.json")


class PollButton(discord.ui.Button):
    def __init__(self, option_index: int, label: str, custom_id: str):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label[:80],
            custom_id=custom_id,
            row=option_index // 5
        )
        self.option_index = option_index

    async def callback(self, interaction: discord.Interaction):
        polls_data = load_json(POLLS_PATH, {})
        msg_id = str(interaction.message.id)

        if msg_id not in polls_data:
            return await interaction.response.send_message("❌ This poll has expired or was removed.", ephemeral=True)

        poll = polls_data[msg_id]
        user_id = str(interaction.user.id)

        # Check if already voted for this option
        votes = poll["votes"]  # {user_id: option_index}
        if votes.get(user_id) == self.option_index:
            # Remove vote (toggle)
            del votes[user_id]
            response_msg = "🗳️ Your vote was removed."
        else:
            votes[user_id] = self.option_index
            response_msg = f"✅ You voted for **{poll['options'][self.option_index]}**!"

        save_json(POLLS_PATH, polls_data)

        # Update poll embed
        cog = interaction.client.get_cog("Polls")
        if cog:
            embed = cog._build_poll_embed(poll)
            await interaction.message.edit(embed=embed)

        await interaction.response.send_message(response_msg, ephemeral=True)


class PollEndButton(discord.ui.Button):
    def __init__(self, author_id: int):
        super().__init__(style=discord.ButtonStyle.danger, label="End Poll", emoji="🛑", custom_id="poll_end_btn", row=4)
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only the poll creator or an administrator can end this poll.", ephemeral=True)

        polls_data = load_json(POLLS_PATH, {})
        msg_id = str(interaction.message.id)

        if msg_id in polls_data:
            poll = polls_data[msg_id]
            cog = interaction.client.get_cog("Polls")
            if cog:
                embed = cog._build_poll_embed(poll, finished=True)
                await interaction.message.edit(embed=embed, view=None)
            del polls_data[msg_id]
            save_json(POLLS_PATH, polls_data)

        await interaction.response.send_message("🛑 Poll has been concluded.", ephemeral=True)


class PollView(discord.ui.View):
    def __init__(self, options: List[str], author_id: int, message_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.author_id = author_id

        for i, opt in enumerate(options):
            cid = f"poll_opt_{i}_{message_id or 'new'}"
            self.add_item(PollButton(option_index=i, label=f"{i+1}. {opt}", custom_id=cid))

        self.add_item(PollEndButton(author_id=author_id))


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_poll_embed(self, poll: dict, finished: bool = False) -> discord.Embed:
        question = poll["question"]
        options = poll["options"]
        votes = poll["votes"]  # {user_id: opt_index}
        total_votes = len(votes)

        # Tally
        tally = {i: 0 for i in range(len(options))}
        for uid, opt_idx in votes.items():
            if opt_idx in tally:
                tally[opt_idx] += 1

        embed = discord.Embed(
            title=f"📊 {'[FINAL RESULTS] ' if finished else ''}{question}",
            color=discord.Color.green() if finished else discord.Color.blurple()
        )

        lines = []
        for i, opt in enumerate(options):
            count = tally[i]
            pct = (count / total_votes * 100) if total_votes > 0 else 0
            bar = create_progress_bar(count, max(1, total_votes))
            lines.append(f"**{i+1}. {opt}**\n`{bar}` **{pct:.1f}%** ({count} votes)\n")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Total Votes: {total_votes} • Created by {poll['author_name']}")
        return embed

    @commands.command(name="poll")
    async def poll(self, ctx: commands.Context, *, content: str):
        """
        Creates an interactive button poll with live results.
        Example: !poll "Favorite Fruit?" "Apple" "Banana" "Orange"
        Or: !poll Best game? | Minecraft | GTA V | Valorant
        """
        # Parse inputs (supports pipe or quoted format)
        if "|" in content:
            parts = [p.strip() for p in content.split("|") if p.strip()]
            question = parts[0]
            options = parts[1:]
        else:
            try:
                parts = shlex.split(content)
                if len(parts) < 2:
                    return await ctx.send("❌ Usage: `!poll \"Question?\" \"Option 1\" \"Option 2\"` or `!poll Question | Opt 1 | Opt 2`")
                question = parts[0]
                options = parts[1:]
            except Exception:
                return await ctx.send("❌ Could not parse options. Make sure your quotes match.")

        if len(options) < 2:
            return await ctx.send("❌ A poll requires at least 2 options.")
        if len(options) > 10:
            return await ctx.send("❌ Maximum 10 options per poll.")

        poll_data = {
            "question": question,
            "options": options,
            "votes": {},
            "author_id": ctx.author.id,
            "author_name": ctx.author.display_name
        }

        embed = self._build_poll_embed(poll_data)
        view = PollView(options=options, author_id=ctx.author.id)

        msg = await ctx.send(embed=embed, view=view)

        # Save to disk
        polls_data = load_json(POLLS_PATH, {})
        polls_data[str(msg.id)] = poll_data
        save_json(POLLS_PATH, polls_data)


async def setup(bot: commands.Bot):
    await bot.add_cog(Polls(bot))
