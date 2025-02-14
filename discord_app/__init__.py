import discord
from spnkr_app import get_match

bot = discord.Bot()


@bot.listen('on_ready', once=True)
async def startup():
    print(f"Bot is up and running: {bot.user.name} - {bot.user.id}")


@bot.command(description="Sends the bot's latency.") # this decorator makes a slash command
async def ping(ctx):  # a slash command will be created with the name "ping"
    await ctx.respond(f"Pong! Latency is {bot.latency}")
    match = await get_match(match_id="d3f1f6e4-44b9-4f0e-b43c-fe475daf4060")
    response = (
        f"{match.map.public_name}"
        f" {match.gamemode.public_name}"
        f" {[player.gamertag for player in match.players]}"
        f" {match.match_stats.match_id}"
    )
    channel = ctx.channel
    await channel.send(response)



