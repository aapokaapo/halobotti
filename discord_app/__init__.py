import discord
from app.tokens import BOT_TOKEN
from spnkr_app import get_match

bot = discord.Bot()

# we need to limit the guilds for testing purposes
# so other users wouldn't see the command that we're testing


@bot.listen('on_ready', once=True)
async def startup():
    print(f"Bot is up and running: {bot.user.name} - {bot.user.id}")


@bot.command(description="Sends the bot's latency.") # this decorator makes a slash command
async def ping(ctx): # a slash command will be created with the name "ping"
    match = await get_match(match_id="d3f1f6e4-44b9-4f0e-b43c-fe475daf4060")
    await ctx.respond(f"Pong! Latency is {bot.latency}")


