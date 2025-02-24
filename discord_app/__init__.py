import discord

from spnkr_app import get_match, get_match_history, get_profile
from database_app.database import (
    add_custom_player, add_custom_match, add_channel, get_player, update_channel,
    get_players, update_player, engine_start, get_all_channels,
    get_all_matches, add_match_to_players, add_players_in_match, get_log_channel
)
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
    match_history = await get_match_history("AapoKaapo",999,25,"custom")
    print(match_history)
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



class AddPlayerView(discord.ui.View):

    def __init__(self, profile, is_valid, timeout, disable_on_timeout=False):
        self.is_valid = is_valid
        self.profile = profile
        super().__init__(timeout=timeout, disable_on_timeout=disable_on_timeout)

    @discord.ui.button(
        label="Lisää Pelaaja",
        style=discord.ButtonStyle.grey,
    )
    async def add_player_button(self, button, interaction):
        try:
            player = await add_custom_player(self.profile, self.is_valid)
            await self.message.edit(content=f"Pelaaja {player.gamertag}-{player.xuid} lisätty tietokantaan", view=None)

        except IntegrityError:
            await self.message.edit(content=f"Pelaaja {self.profile.gamertag} on jo tietokannassa", view=None)

        self.clear_items()
        self.stop()


    @discord.ui.button(
        label="Älä Lisää Pelaajaa",
        style=discord.ButtonStyle.grey,
    )
    async def player_not_added(self, button, interaction):
        self.clear_items()
        await self.message.edit(content=f"Pelaajaa {self.profile.gamertag} ei lisätty tietokantaan", view=None)
        self.stop()


    async def on_timeout(self):
        self.clear_items()
        await self.message.edit(content=f"Et vastannut ajoissa. Pelaajaa {self.profile.gamertag} ei lisätty", view=None)


@bot.command(name="update_player", description="Update a player in the database")
@discord.default_permissions(administrator=True)
async def _update_player(ctx, gamertag: str, is_valid: bool):
    message = await ctx.respond(f"Yritetään päivittää pelaajan {gamertag} tilaa")
    profile = await get_profile(gamertag)
    if profile:
        player = await update_player(profile.gamertag, is_valid)
        if player:
            await message.edit(content=f"Päivitetty pelaajan {player.gamertag} tilaksi {player.is_valid}")
        else:
            await message.edit(
                content=f"Pelaajaa {profile.gamertag} ei löytynyt tietokannasta. Haluatko lisätä pelaajan?",
                view=AddPlayerView(profile, is_valid, timeout=30)
            )
    else:
        await message.edit(content=f"Pelaajaa {gamertag} ei löydy Microsoft Xbox -palvelusta. Kirjoititko nimen oikein?")
    
    
@bot.command(description="Get player info")
async def player_info(ctx, gamertag:str):
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data")
    profile = await get_profile(gamertag)
    player = await get_player(profile.gamertag)
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
    # if there are exactly 8 players in the game, who are is_valid -> valid
    # if the gamemode is one of the ranked modes -> valid
    # if the match finished either by_time or by_score-> valid
    #
    pass


class ValidatePlayerView(discord.ui.View):
    def __init__(self, custom_player, timeout):
        self.custom_player = custom_player
        super().__init__(timeout=timeout)

    @discord.ui.button(
        label="On 8s Suomi Custom-pelaaja",
        style=discord.ButtonStyle.grey,
    )
    async def validate_player_button(self, button, interaction):
        custom_player = await update_player(self.custom_player.gamertag, value=True)
        await self.message.edit(content=f"Päivitetty pelaajan {custom_player.gamertag} tilaksi {custom_player.is_valid}", view=None)
        self.stop()

    @discord.ui.button(
        label="Ei ole 8s Suomi Custom-pelaaja",
        style=discord.ButtonStyle.grey,
    )
    async def do_not_validate_player_button(self, button, interaction):
        await self.message.edit(content=f"Pelaajan {self.custom_player.gamertag} tila on {self.custom_player.is_valid}", view=None)
        self.stop()



async def find_all_custom_matches(player):
    start = 0
    index =0
    while True:
        print(start)
        match_history = await get_match_history(player.gamertag,start=start, match_type='custom')
        if len(match_history) == 0:
            return
        for match in match_history:
            index += 1
            custom_match = await get_match(match.match_id)
            await check_match_validity(custom_match)
            custom_players = await add_players_in_match(custom_match)
            for custom_player in custom_players:
                if not custom_player.is_valid and not custom_player.validation_message and (len(custom_player.custom_matches) > 3 or sum([custom_player.is_valid for custom_player in custom_players]) > len(custom_players)/2):
                    await update_player(custom_player.gamertag, custom_player.is_valid, validation_message=True)
                    guild = await get_log_channel()
                    channel = await bot.fetch_channel(guild.log_channel_id)
                    message = await channel.send(content=f"Hyväksytäänkö pelaaja {custom_player.gamertag}", view=ValidatePlayerView(custom_player, timeout=None))

            try:
                await add_custom_match(custom_match)
                await add_match_to_players(custom_match.match_stats.match_id, custom_match.players)
                yield player, index
            except IntegrityError:
                pass
        start += 25
       

@bot.command(description="Populate database with custom matches")
@discord.default_permissions(administrator=True)
async def populate_database(ctx):
    message = await ctx.respond("Yritetään kansoittaa tietokanta")
    channel_id = ctx.interaction.channel_id
    message = await message.original_response()
    message_id = message.id
    custom_players = await get_players()
    await message.edit(content=f"Löydettiin {len(custom_players)} vahvistettua pelaajaa. Aloitetaan pelien haku")
    if custom_players:
        for player in custom_players:
            async for player, index in find_all_custom_matches(player):
                channel = await bot.fetch_channel(channel_id)
                original_message = await channel.fetch_message(message_id)
                await original_message.edit(content=f"Haetaan pelaajan {player.gamertag} peliä #{index}")

    matches = await get_all_matches()
    await ctx.send(f"Valmis!\nTietokannassa on {len(matches)} custom-matsia")
    

