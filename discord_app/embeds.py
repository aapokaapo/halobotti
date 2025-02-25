from discord import Embed
import plotly.express as px
from spnkr.tools import OUTCOME_MAP, TEAM_MAP, MEDAL_NAME_MAP, unwrap_xuid, LIFECYCLE_MAP
from aiohttp import ClientSession

async def get_map_image(map_asset):
    async with ClientSession() as session:
        map_image_url = map_asset.files.prefix + "images/thumbnail.jpg"
        response = await session.get(map_image_url)

        if response.status == 404:
            map_image_url = map_asset.files.prefix + "images/thumbnail.png"
            response = await session.get(map_image_url)

            if response.status == 404:
                map_image_url = "https://img.freepik.com/premium-vector/default-image-icon-vector-missing-picture-page-website-design-mobile-app-no-photo-available_87543-11093.jpg"

    return map_image_url


async def create_match_info(match):
    title = f"{LIFECYCLE_MAP[match.match_stats.match_info.lifecycle_mode]}: {match.match_gamemode.public_name} - {match.match_map.public_name}"
    team_stats = f" - ".join([f"{TEAM_MAP[team.team_id]} {team.stats.core_stats.score} {OUTCOME_MAP[team.outcome]}" for team in match.match_stats.teams])
    match_gamemode = f"{match.match_gamemode.public_name}"
    match_map = f"{match.match_map.public_name}"
    playtime = f"{str(match.match_stats.match_info.playable_duration)}"
    
    description = "\n".join([team_stats, match_gamemode, match_map, playtime])
    
    match_embed = Embed(title=title, description=description)
    
    teams = dict()
    for player in match.match_stats.players:
        profile = [profile for profile in match.players if profile.xuid == unwrap_xuid(player.player_id)][0]
        try:
            teams[player.last_team_id].append((profile.gamertag, player))
        except KeyError:
            teams[player.last_team_id] = []
            teams[player.last_team_id].append((profile.gamertag, player))
    for team_id, team in teams.items():
        match_embed.add_field(name=f"{TEAM_MAP[team_id]}", value="\n".join([player[0] for player in team]))

    match_embed.set_thumbnail(url=await get_map_image(match.match_map))
    match_embed.set_author(name="HaloBotti 2.0")
    match_embed.set_footer(text="HaloBotti 2.0 by AapoKaapo", icon_url="https://halofin.land/HaloFinland.png")
    
    return match_embed
