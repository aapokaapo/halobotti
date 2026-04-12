import time
from typing import Optional

import discord
from discord import Interaction
from discord.ext.pages import Page, Paginator
from spnkr.tools import LIFECYCLE_MAP

from database_app.database import engine_start
from discord_app.embeds import (
    create_match_info,
    create_match_skill_embed,
    create_rank_embed,
    create_series_info,
)
from discord_app.lobby import fetch_playlist_wait_times
from spnkr_app import fetch_player_match_data, fetch_player_match_skills, get_client, get_xbl_profiles

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


@bot.command(description="Hae pelaajan ranked-suoritus")
async def rank(ctx, gamertag: str) -> None:
    """Näytä pelaajan CSR-eteneminen ja viimeiset ranked-matsit."""
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data", ephemeral=True)
    try:
        async for client in get_client():
            profile = await get_xbl_profiles(client, gamertag)
            if not profile:
                await message.edit_original_response(content=f"Virhe: Pelaajaa '{gamertag}' ei löydy")
                return
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
    except Exception as e:
        print(f"Virhe rank-komennossa: {e}")
        await message.edit_original_response(content="Virhe: Ranked-datan hakeminen epäonnistui")


@bot.command(description="Hae Ranked Arena -pelilistauksen odotusaika")
async def wait_time(ctx) -> None:
    """Näytä Ranked Arena -pelilistauksen arvioitu odotusaika."""
    message = await ctx.respond("Haetaan Ranked Arena odotusaikaa...", ephemeral=True)
    wait_times = await fetch_playlist_wait_times()

    if not wait_times:
        await message.edit_original_response(content="Virhe: Odotusaikoja ei voitu hakea")
        return

    ranked_name = next(
        (name for name in wait_times if "ranked arena" in name.lower()), None
    )

    if ranked_name is None:
        await message.edit_original_response(content="Virhe: Ranked Arena -pelilistaa ei löydy")
        return

    wait_ms = wait_times[ranked_name]
    wait_seconds = wait_ms / 1000
    minutes = int(wait_seconds // 60)
    seconds = int(wait_seconds % 60)

    embed = discord.Embed(
        title="Ranked Arena Odotusaika",
        description=f"**{minutes}m {seconds}s**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Pelilistaus", value=ranked_name)
    # The Bond PlaylistResponse schema only exposes WaitTime and asset identifiers;
    # the isEnabled / online-status field is not present in the lobby WebSocket data.
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


@bot.command(description="Luo yhteenveto pelatuista matseista")
async def make_series(ctx, gamertag: str, count: Optional[int] = 25, start: Optional[int] = 0, match_type: str = "all") -> None:
    """Hae pelaajan matsit ja luo niistä sarjayhteenveto valintanäkymällä."""
    msg = await ctx.respond(content="Haetaan matseja...", ephemeral=True)
    try:
        match_history = await fetch_player_match_data(gamertag, start=start, count=count, match_type=match_type)
        if not match_history:
            await msg.edit_original_response(content="Virhe: Matseja ei löydy annetuilla hakuehdoilla")
            return
        await msg.edit_original_response(content=f"Haetaan matseja... ({len(match_history)}/{count})")
        select = MatchSelect(match_history)
        await msg.edit_original_response(content="", view=SeriesView(select))
    except Exception as e:
        print(f"Virhe make_series-komennossa: {e}")
        await msg.edit_original_response(content="Virhe: Matsien hakeminen epäonnistui")
