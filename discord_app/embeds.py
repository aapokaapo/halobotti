from email.policy import default

from discord import Embed, File
from spnkr.tools import OUTCOME_MAP, TEAM_MAP, MEDAL_NAME_MAP, unwrap_xuid, LIFECYCLE_MAP, BOT_MAP
from aiohttp import ClientSession
from typing import List
from spnkr_app import Match
import uuid

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

import io
import matplotlib.pyplot as plt

from collections import defaultdict


async def create_discord_table_image(data: List[str|int|float], columns: List[str]):
    """Generates a Discord-styled table image with a dark theme and modern styling."""

    # Colors matching Discord's dark theme
    bg_color = "#2C2F33"  # Dark gray background
    text_color = "#FFFFFF"  # White text
    header_color = "#7289DA"  # Blurple for header
    alt_row_color = "#23272A"  # Slightly darker than bg
    border_color = "#99AAB5"  # Soft gray border

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 5), dpi=100, facecolor=bg_color)
    ax.set_facecolor(bg_color)
    ax.axis('tight')
    ax.axis('off')

    # Create table
    table = ax.table(cellText=data, colLabels=columns, cellLoc='center', loc='center')

    # Style table
    table.auto_set_font_size(False)
    table.set_fontsize(12)  # Slightly larger font
    table.auto_set_column_width([i for i in range(len(columns))])


    for i, key in table._cells.items():
        cell = table._cells[i]
        cell.set_edgecolor(border_color)  # Subtle thin border
        cell.set_linewidth(0.7)  # Thin border
        cell.set_height(0.15)  # Increase row height

        # Header styling
        if i[0] == 0:  
            cell.set_facecolor(header_color)
            cell.set_text_props(color=text_color, weight='bold')
        else:  # Data row styling
            cell.set_facecolor(bg_color if i[0] % 2 == 0 else alt_row_color)
            cell.set_text_props(color=text_color)


    # Save to BytesIO
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', bbox_inches='tight', transparent=True, facecolor=bg_color)
    img_buf.seek(0)
    plt.close()

    return img_buf  # Return BytesIO object for Discord upload



async def create_match_description(matches: list[Match]) -> str:
    """Generates a description string for a Discord embed based on multiple matches."""
    
    descriptions = []
    
    for match in matches:
        match_map = match.match_map.public_name
        match_mode = match.match_gamemode.public_name
        descriptions.append(f"**Map:** {match_map} | **Mode:** {match_mode}")

    description = "**Match History**\n" + "\n".join(descriptions)

    return description
    

async def create_aggregated_match_table(matches: list[Match]):
    header = ['Gamertag', 'Team', 'Score', 'Kills', 'Deaths', 'Assists', 'Damage Dealt', 'Damage Taken', 'Shots Hit', 'Shots Fired', 'Accuracy']
    player_totals = defaultdict(lambda: [0] * (len(header) - 2))  # Dict with default list for stats

    for match in matches:
        for match_player in match.match_stats.players:
            for team in match_player.player_team_stats:
                if match_player.is_human:
                    gamertag = next(player.gamertag for player in match.players if player.xuid == unwrap_xuid(match_player.player_id))
                else:
                    gamertag = BOT_MAP[match_player.player_id]
                core_stats = team.stats.core_stats
                try:
                    team_name = TEAM_MAP[team.team_id]
                except KeyError:
                    team_name = "Invalid Team"

                # Aggregate stats per player
                if gamertag not in player_totals:
                    player_totals[gamertag] = [team_name, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # Init with team name

                stats = player_totals[gamertag]
                stats[1] += core_stats.personal_score
                stats[2] += core_stats.kills
                stats[3] += core_stats.deaths
                stats[4] += core_stats.assists
                stats[5] += core_stats.damage_dealt
                stats[6] += core_stats.damage_taken
                stats[7] += core_stats.shots_hit
                stats[8] += core_stats.shots_fired

    # Calculate accuracy after summing up
    values = []
    for gamertag, stats in player_totals.items():
        shots_hit, shots_fired = stats[7], stats[8]
        accuracy = (shots_hit / shots_fired) * 100 if shots_fired > 0 else 0
        values.append([gamertag] + stats[:9] + [f"{accuracy:.2f}"])

    values.sort(key=lambda value: value[1])  # Sort by team

    img_buf = await create_discord_table_image(values, header)
    
    return img_buf
    
    
async def create_match_table(match: Match):
    header = ['Gamertag', 'Team', 'Score', 'Kills', 'Deaths', 'Assists', 'Damage Dealt', 'Damage Taken','Shots Hit', 'Shots Fired', 'Accuracy', 'Outcome']
    values=[]
    for match_player in match.match_stats.players:
        for team in match_player.player_team_stats:
            if match_player.is_human:
                gamertag = next(player.gamertag for player in match.players if player.xuid == unwrap_xuid(match_player.player_id))
            else:
                gamertag = BOT_MAP[match_player.player_id]
            core_stats = team.stats.core_stats
            player_stats = [gamertag, f"{TEAM_MAP[team.team_id]}", core_stats.personal_score, core_stats.kills, core_stats.deaths, core_stats.assists, core_stats.damage_dealt, core_stats.damage_taken, core_stats.shots_hit, core_stats.shots_fired, core_stats.accuracy, f"{OUTCOME_MAP[match_player.outcome]}"]
            values.append(player_stats)

    values.sort(key=lambda value: value[1])
            
    img_buf = await create_discord_table_image(values, header)
        
    return img_buf


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
        if player.is_human:
            gamertag = [profile.gamertag for profile in match.players if profile.xuid == unwrap_xuid(player.player_id)][0]
        else:
            gamertag = BOT_MAP[player.player_id]
        try:
            teams[player.last_team_id].append((gamertag, player))
        except KeyError:
            teams[player.last_team_id] = []
            teams[player.last_team_id].append((gamertag, player))
    for team_id, team in teams.items():
        match_embed.add_field(name=f"{TEAM_MAP[team_id]}", value="\n".join([player[0] for player in team]))

    match_embed.set_thumbnail(url=await get_map_image(match.match_map))
    match_embed.set_author(name="HaloBotti 2.0")
    match_embed.set_footer(text="HaloBotti 2.0 by AapoKaapo", icon_url="https://halofin.land/HaloFinland.png")
    
    image = await create_match_table(match)
    match_embed.set_image(url=f"attachment://{match.match_stats.match_id}.png")
    files = [
        File(image, f"{match.match_stats.match_id}.png")
    ]
    
    return match_embed, files


async def create_series_info(match_history: List[Match]):
    title = "Series"
    
    description = await create_match_description(match_history)
    
    series_embed = Embed(title=title, description=description)
    image = await create_aggregated_match_table(match_history)
    random_uuid = uuid.uuid4()
    series_embed.set_image(url=f"attachment://{random_uuid}.png")
    files = [
        File(image, f"{random_uuid}.png")
    ]
    
    return series_embed, files
