import asyncio
import time

import discord
from discord import Interaction
from discord.ext.pages import Page, Paginator
from spnkr.tools import LIFECYCLE_MAP

from discord_app.embeds import create_series_info, create_match_info, create_rank_embed, create_match_skill_embed
from spnkr_app import fetch_player_match_data, get_xbl_profiles, get_client, fetch_player_match_skills
from database_app.database import engine_start
from typing import Optional
from aiohttp import ClientSession


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


@bot.listen('on_ready', once=True)
async def startup():
    print(f"Bot is up and running: {bot.user.name} - {bot.user.id}")
    await engine_start()


@bot.command(description="Get data of player's ranked performance")
async def rank(ctx, gamertag: str):
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data", ephemeral=True)
    async for client in get_client():
        profile = await get_xbl_profiles(client, gamertag)
        if profile:
            start_time = time.time()
            match_skills = await fetch_player_match_skills(profile[0].gamertag, count=20)
            end_time = time.time()
            print("match_skills took %f ms" % ((end_time - start_time) * 1000.0))
            pages = []
            xuids = []
            embed, files = await create_rank_embed(profile[0], match_skills)
            summary_page = Page(embeds=[embed], files=files)
            pages.append(summary_page)
            for match_skill in match_skills:
                for value in match_skill.value:
                    xuids.append(value.id)
            profiles = await get_xbl_profiles(client, xuids)
            for match_skill in match_skills:
                page = Page(embeds=[await create_match_skill_embed(profiles, match_skill)])
                pages.append(page)
            custom_view = PublishView()

            paginator = SeriesPaginator(pages=pages)
            custom_view.add_paginator(paginator)
            paginator.custom_view = custom_view
            await paginator.respond(message, ephemeral=True)


async def fetch_playlist_wait_times():
    url = "https://halostats.343industries.com/api/v1/playlist-info"
    try:
        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
    except Exception as e:
        print(f"Virhe odotusaikojen haussa: {e}")
        return None


@bot.command(description="Hae Ranked Arena -pelilistauksen odotusaika")
async def wait_time(ctx):
    message = await ctx.respond("Haetaan Ranked Arena odotusaikaa...", ephemeral=True)
    data = await fetch_playlist_wait_times()

    if data is None:
        await message.edit_original_response(content="Virhe: Odotusaikoja ei voitu hakea")
        return

    ranked_arena = None
    for playlist in data.get("playlists", []):
        if "ranked arena" in playlist.get("name", "").lower():
            ranked_arena = playlist
            break

    if ranked_arena is None:
        await message.edit_original_response(content="Virhe: Ranked Arena -pelilistaa ei löydy")
        return

    wait_seconds = ranked_arena.get("averageWaitTime", 0) / 1000
    minutes = int(wait_seconds // 60)
    seconds = int(wait_seconds % 60)

    embed = discord.Embed(
        title="Ranked Arena Odotusaika",
        description=f"**{minutes}m {seconds}s**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Pelilistaus", value=ranked_arena.get("name", "Ranked Arena"))
    embed.add_field(name="Tila", value="🟢 Online" if ranked_arena.get("isEnabled") else "🔴 Offline")
    embed.set_footer(text="HaloBotti 2.0 by AapoKaapo", icon_url="https://halofin.land/HaloFinland.png")

    await message.edit_original_response(content="", embed=embed)


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
async def make_series(ctx, gamertag: str, count: Optional[int] = 25, start: Optional[int] = 0, match_type="all"):
    msg = await ctx.respond(content="Haetaan matseja", ephemeral=True)
    match_history = await fetch_player_match_data(gamertag, start=start, count=count, match_type=match_type)
    index = 0
    await msg.edit_original_response(content=f"Haetaan matseja... ({index}/{count})")

    select = MatchSelect(match_history)
    await msg.edit_original_response(content="", view=SeriesView(select))
