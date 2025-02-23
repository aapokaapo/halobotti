import discord
from spnkr_app import get_match, get_match_history, get_profile
from database_app.database import add_custom_player, add_custom_match, add_channel, get_player, update_channel, get_players, update_player, engine_start, get_all_channels, get_all_matches, add_match_to_players
import time

from sqlalchemy.exc import IntegrityError

from typing import Optional

bot = discord.Bot()


async def add_channels_to_database():
    async for guild in bot.fetch_guilds():
        try:
            await add_channel(guild.id)
        except IntegrityError:
            pass

        channels = await get_all_channels()
        print(f"Found {len(channels)} channels")


@bot.listen('on_ready', once=True)
async def startup():
    print(f"Bot is up and running: {bot.user.name} - {bot.user.id}")
    await engine_start()
    await add_channels_to_database()


@bot.command(description="Sends the bot's latency.") # this decorator makes a slash command
async def ping(ctx):  # a slash command will be created with the name "ping"
    await ctx.respond(f"Pong! Latency is {bot.latency}")
    start = time.time()
    match = await get_match(match_id="d3f1f6e4-44b9-4f0e-b43c-fe475daf4060")
    end = time.time()
    
    print("Took %f ms" % ((end - start) * 1000.0))
    


@bot.command(description="Adds a player to the database")
@discord.default_permissions(administrator=True)
async def add_player(ctx, gamertag:str, is_valid: Optional[bool]):
    
    message = await ctx.respond(f"Yritetään lisätä pelaaja {gamertag} tietokantaan...")
    profile = await get_profile(gamertag)
    try:
        player = await add_custom_player(profile, is_valid)
        await message.edit(content=f"Pelaaja {player.gamertag}-{player.xuid} lisätty tietokantaan")
        
    except IntegrityError:
        await message.edit(content=f"Pelaaja {gamertag} on jo tietokannassa")
    

@bot.command(name="update_player", description="Update a player in the database")
@discord.default_permissions(administrator=True)
async def _update_player(ctx, gamertag: str, is_valid: bool):
    message = await ctx.respond(f"Yritetään päivittää pelaajan {gamertag} tilaa")
    profile = await get_profile(gamertag)
    player = await update_player(profile.gamertag, is_valid)
    
    # tähän vois kehittää koodin, joka ehdottaa pelaajan lisäämistä jos ei löydy tietokannasta. Koodi voisi kutsua add_player funktiota yllä.
    
    await message.edit(content=f"Päivitetty pelaajan {player.gamertag} tilaksi {player.is_valid}")
    
    
@bot.command(description="Get player info")
async def player_info(ctx, gamertag:str):
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data")
    profile = await get_profile(gamertag)
    player = await get_player(profile.gamertag)
    print(player.custom_matches)
    player_data = f"{player.gamertag}-{player.xuid} Pelatut pelit:{len(player.custom_matches)}"
    await message.edit(content=player_data)
    
    
@bot.command(description="Sets the text channel as log channel")
@discord.default_permissions(administrator=True)
async def set_log_channel(ctx, channel_id: Optional[int]=None):
    if not channel_id:
        channel_id = ctx.channel.id
    message = await ctx.respond(f"Yritetään asettaa kanava {channel_id} loki-kanavaksi")
    
    guild_id = ctx.guild.id
    channel = await update_channel(guild_id, log_channel=channel_id)

    await message.edit(content=f"Kanava {channel.log_channel_id} asetettu loki-kanavaksi")


async def check_match_validity(custom_match):
    pass


async def find_all_custom_matches(player):
    match_history = await get_match_history(player.gamertag, match_type='custom')
    custom_matches = []
    for match in match_history:
        custom_match = await get_match(match.match_id)
        await check_match_validity(custom_match)
        try:
            await add_custom_match(custom_match)
            await add_match_to_players(custom_match.match_stats.match_id, custom_match.players)
            print("added new match to db")
        except IntegrityError:
            print("match already in db")
       

@bot.command(description="Populate database with custom matches")
@discord.default_permissions(administrator=True)
async def populate_database(ctx):
    message = await ctx.respond("Yritetään kansoittaa tietokanta")
    custom_players = await get_players()
    await message.edit(content=f"Löydettiin {len(custom_players)} vahvistettua pelaajaa. Aloitetaan pelien haku")
    if custom_players:
        for player in custom_players:
            await find_all_custom_matches(player)

    matches = await get_all_matches()
    await ctx.send(f"Valmis!\nTietokannassa on {len(matches)} custom-matsia")
    

