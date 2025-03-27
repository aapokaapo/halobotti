from asyncio import timeout
from re import match
import asyncio

import discord
from discord import Interaction
from discord.ext.pages import Page, Paginator, PaginatorButton
from discord.ext import tasks
from spnkr.tools import LIFECYCLE_MAP

from discord_app.embeds import create_aggregated_match_table, create_series_info, create_match_info, create_rank_embed
from spnkr_app import fetch_player_match_data, get_xbl_profiles, get_client, fetch_player_match_skills
from database_app.database import (
    add_custom_player, add_custom_match, add_channel, get_player, update_channel,
    get_players, update_player, engine_start, get_all_channels,
    get_all_matches, add_match_to_players, add_players_in_match, get_log_channel
)
import time
import discord_app.embeds
from sqlalchemy.exc import IntegrityError
from typing import Optional, Literal
from spnkr_app.match_validity import check_match_validity
import datetime
from aiohttp import ClientSession, ClientResponseError
from discord.errors import HTTPException


bot = discord.Bot()


class PublishView(discord.ui.View):
    def __init__(self):
        self.paginator = None
        super().__init__()

    def add_paginator(self, paginator):
        self.paginator = paginator

    @discord.ui.button(label="Publish")
    async def callback(self, button, interaction):
        if self.paginator:
            self.paginator.custom_view = None
            await self.paginator.respond(interaction)

        elif self.message:
            await interaction.response.edit_message(view=None)
            await self.message.channel.send(embeds=self.message.embeds)


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
    start = time.time()
    await ctx.respond(f"Pong! Latency is {bot.latency}")
    match_data = await fetch_player_match_data("AapoKaapo")
    match_embed, files = await embeds.create_match_info(match_data[0])
    await ctx.channel.send(embed=match_embed, files=files)

    series_embed, files = await embeds.create_series_info(match_data[0:5])
    await ctx.channel.send(embed=series_embed, files=files,)
    
    end = time.time()
    
    print("Took %f ms" % ((end - start) * 1000.0))



@bot.command(description="Adds a player to the database")
@discord.default_permissions(administrator=True)
async def add_player(ctx, gamertag:str, is_valid: Optional[bool]):
    
    message = await ctx.respond(f"Yritetään lisätä pelaaja {gamertag} tietokantaan...")
    async for client in get_client():
        profile = await get_xbl_profiles(client, gamertag)
        if profile:
            try:
                player = await add_custom_player(profile[0], is_valid)
                await message.edit(content=f"Pelaaja {player.gamertag}-{player.xuid} lisätty tietokantaan")

            except IntegrityError:
                await message.edit(content=f"Pelaaja {gamertag} on jo tietokannassa")
        else:
            await message.edit(content="something went wrong")



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
    async for client in get_client():
        profile = await get_xbl_profiles(client, gamertag)
        if profile:
            player = await update_player(profile[0].gamertag, is_valid)
            if player:
                await message.edit(content=f"Päivitetty pelaajan {player.gamertag} tilaksi {player.is_valid}")
            else:
                await message.edit(
                    content=f"Pelaajaa {gamertag} ei löytynyt tietokannasta. Haluatko lisätä pelaajan?",
                    view=AddPlayerView(gamertag, is_valid, timeout=30))
    
    
@bot.command(description="Get player info")
async def player_info(ctx, gamertag:str):
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data")
    async for client in get_client():
        profile = await get_xbl_profiles(client, gamertag)
        if profile:
            player = await get_player(profile[0].gamertag)
            player_data = f"{player.gamertag}-{player.xuid} Pelatut pelit:{len(player.custom_matches)}"
            await message.edit(content=player_data)
    
    
@bot.command(description="Get data of player's ranked performance")
async def rank(ctx, gamertag: str):
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data", ephemeral=True)
    async for client in get_client():
        profile = await get_xbl_profiles(client, gamertag)
        if profile:
            start_time = time.time()
            match_skills = await fetch_player_match_skills(profile[0].gamertag)
            end_time = time.time()
            print("match_skills took %f ms" % ((end_time - start_time) * 1000.0))
            
            # match_data = await fetch_player_match_data(profile[0].gamertag, match_type="ranked")
            
            embed, files = await create_rank_embed(profile[0], match_skills)
            await message.edit_original_response(content="", embed=embed, files=files, view=PublishView())
    
    
@bot.command(description="Sets the text channel as log channel")
@discord.default_permissions(administrator=True)
async def set_log_channel(ctx, channel_id: Optional[int]=None):
    if not channel_id:
        channel_id = ctx.channel.id
    message = await ctx.respond(f"Yritetään asettaa kanava {channel_id} loki-kanavaksi")
    
    guild_id = ctx.guild.id
    channel = await update_channel(guild_id, log_channel=channel_id)

    await message.edit(content=f"Kanava {channel.log_channel_id} asetettu loki-kanavaksi")


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


async def validate_players(custom_players):
    for custom_player in custom_players:
        if not custom_player.is_valid and not custom_player.validation_message and (len(custom_player.custom_matches) > 3 or sum([custom_player.is_valid for custom_player in custom_players]) > len(custom_players) / 2):
            await update_player(custom_player.gamertag, custom_player.is_valid, validation_message=True)
            guild = await get_log_channel()
            channel = await bot.fetch_channel(guild.log_channel_id)
            message = await channel.send(content=f"Hyväksytäänkö pelaaja {custom_player.gamertag}",
                                         view=ValidatePlayerView(custom_player, timeout=None))


async def match_data_handler(custom_match, date):
    is_valid = await check_match_validity(custom_match, date)
    custom_players = await add_players_in_match(custom_match)
    await validate_players(custom_players)

    try:
        await add_custom_match(custom_match, is_valid)
        await add_match_to_players(custom_match.match_stats.match_id, custom_match.players)
    except IntegrityError:
        pass


async def find_all_custom_matches(player, date):
    start = 0
    index =0
    while True:

        match_history = await fetch_player_match_data(player.gamertag,start=start, match_type='custom')
        if len(match_history) == 0:
            return

        match_tasks = []
        for custom_match in match_history:
            index += 1
            yield player, index
            task = asyncio.create_task(match_data_handler(custom_match, date))
            match_tasks.append(task)
            
        await asyncio.gather(*match_tasks)
            
        start += 25


@bot.command(description="Populate database with custom matches")
@discord.default_permissions(administrator=True)
async def populate_database(ctx, year:int=2024, month:int=1, day:int=1):
    guild = await get_log_channel()
    if not guild.log_channel_id:
        await update_channel(guild.guild_id, ctx.interaction.channel_id)

    date = datetime.datetime(year, month, day, tzinfo=datetime.UTC)
    message = await ctx.respond("Yritetään kansoittaa tietokanta")
    channel_id = ctx.interaction.channel_id
    message = await message.original_response()
    message_id = message.id
    custom_players = await get_players()
    await message.edit(content=f"Löydettiin {len(custom_players)} vahvistettua pelaajaa. Aloitetaan pelien haku")
    player_index = 0
    while player_index < len(await get_players()):

        players = await get_players()
        custom_player = players[player_index]
        player_index += 1

        async for current_player, index in find_all_custom_matches(custom_player, date):
            channel = await bot.fetch_channel(channel_id)
            original_message = await channel.fetch_message(message_id)
            await original_message.edit(content=f"Haetaan pelaajan {current_player.gamertag} peliä #{index}")

    matches = await get_all_matches()
    await ctx.send(f"Valmis!\nTietokannassa on {len(matches)} custom-matsia")


class SeriesPaginator(Paginator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    async def on_timeout(self):
        await self.goto_page(0)
        await super().on_timeout()
        


class MatchSelect(discord.ui.Select):
    def __init__(self, match_history):
        self.match_history = match_history
        options = []
        for custom_match in match_history:
            option = discord.SelectOption(
                label=f"{LIFECYCLE_MAP[custom_match.match_stats.match_info.lifecycle_mode]}: {custom_match.match_gamemode.public_name} - {custom_match.match_map.public_name}",
                value=f"{custom_match.match_stats.match_id}",
                emoji=None
            )
            options.append(option)
        super().__init__(placeholder="Select A Match", max_values=len(options), options=options)

    async def callback(self, interaction: Interaction):
        await interaction.response.defer()
        pages = []
        files = []
        match_ids = self.values
        selected_matches = [match for match in self.match_history if f"{match.match_stats.match_id}" in match_ids]
        embed, file = await create_series_info(selected_matches)
        series_page = Page(embeds=[embed], files=file)
        pages.append(series_page)
        files.append(file)
        for match in selected_matches:
            match_embed, file = await create_match_info(match)
            page = Page(embeds=[match_embed], files=file)
            pages.append(page)
            files.append(file)
        custom_view = PublishView()
        paginator = SeriesPaginator(pages=pages)
        custom_view.add_paginator(paginator)
        paginator.custom_view = custom_view
        await paginator.respond(interaction, ephemeral=True)


class SeriesView(discord.ui.View):
    def __init__(self, *args):
        super().__init__(*args)

    async def on_timeout(self):
        try:
            await self.parent.edit_original_response(delete_after=0)
        except AttributeError:
            await self.message.edit(delete_after=0)


@bot.command(description="Create a summary of played matches")
async def make_series(ctx, gamertag: str, count: Optional[int] = 25, start: Optional[int] = 0, match_type = "all"):
    msg = await ctx.respond(content="Haetaan matseja", ephemeral=True)
    match_history = await fetch_player_match_data(gamertag, start=start, count=count, match_type=match_type)
    index = 0
    await msg.edit_original_response(content=f"Haetaan matseja... ({index}/{count})")
            
    select = MatchSelect(match_history)
    await msg.edit_original_response(content="", view=SeriesView(select))


@tasks.loop(minutes=3)
async def fetch_new_matches():
    guild = await get_log_channel()
    channel = await bot.fetch_channel(guild.log_channel_id)
    custom_players = await get_players()
    for player in custom_players:
        match_history = await fetch_player_match_data(player.gamertag, count=25)
        for custom_match in match_history:
            is_valid = await check_match_validity(custom_match)
            custom_players = await add_players_in_match(custom_match)
            await validate_players(custom_players)

            try:
                await add_custom_match(custom_match, is_valid)
                await add_match_to_players(custom_match.match_stats.match_id, custom_match.players)
                embed, files = await create_match_info(custom_match)
                await channel.send(embed=embed, files=files)
            except IntegrityError:
                pass
        
        
@bot.command()
async def start_tracking(ctx):
    try:
        fetch_new_matches.start()
        await ctx.respond(content="Started match tracking", ephemeral=True)
    except HTTPException:
        await ctx.respond(content="Something went wrong", ephemeral=True)
    
    
@bot.command()
async def stop_tracking(ctx):
    try:
        fetch_new_matches.stop()
        await ctx.respond(content="Stopped match tracking", ephemeral=True)
    except HTTPException:
        await ctx.respond(content="Something went wrong", ephemeral=True)
    
