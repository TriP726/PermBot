import os
import sys
import asyncio
import logging
import discord
from discord.ext import commands
from utils.helpers import log, load_token

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    case_insensitive=True
)


async def load_extensions():
    cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
    if not os.path.exists(cogs_dir):
        os.makedirs(cogs_dir, exist_ok=True)
        return

    loaded, failed = 0, 0
    for filename in sorted(os.listdir(cogs_dir)):
        if filename.endswith(".py") and not filename.startswith("__"):
            cog_name = filename[:-3]
            try:
                await bot.load_extension(f"cogs.{cog_name}")
                log.info(f"Loaded cog: cogs.{cog_name}")
                loaded += 1
            except Exception as e:
                log.error(f"Failed to load cogs.{cog_name}: {e}")
                failed += 1

    log.info(f"Cog loading complete: {loaded} loaded, {failed} failed.")


@bot.event
async def on_ready():
    total_members = sum(g.member_count or len(g.members) for g in bot.guilds)
    total_guilds = len(bot.guilds)

    banner = f"""
============================================================
              ⚡ PERMBOT IS ONLINE & READY ⚡
============================================================
 Bot Account : {bot.user} (ID: {bot.user.id})
 Discord.py  : v{discord.__version__} (Python {sys.version.split()[0]})
 Prefix      : !  |  Slash: /
 Guilds      : {total_guilds} server(s) connected
 Members     : {total_members:,} total users
 Status      : Listening to !help | !play
 Loaded Cogs : {len(bot.cogs)} active modules
============================================================
"""
    print(banner)

    activity = discord.Activity(type=discord.ActivityType.listening, name="!help | !play")
    await bot.change_presence(activity=activity, status=discord.Status.online)

    log.info("Syncing application command tree...")
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} application commands successfully.")
    except Exception as e:
        log.error(f"Failed to sync application command tree: {e}")


async def main():
    async with bot:
        await load_extensions()
        token = load_token()
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[INFO] Bot shutdown initiated. Cleaning up session...")
    except Exception as e:
        log.critical(f"Fatal error encountered in main loop: {e}")
        sys.exit(1)
