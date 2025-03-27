from email.policy import default

from discord import Embed, File
from pydantic import BaseModel
from spnkr.tools import OUTCOME_MAP, TEAM_MAP, MEDAL_NAME_MAP, unwrap_xuid, LIFECYCLE_MAP, BOT_MAP
from aiohttp import ClientSession
from typing import List

from spnkr.xuid import wrap_xuid

from spnkr_app import Match
import uuid
import math

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


async def generate_csr_graph(player, match_skills):
    # Extract CSR values from match_skills
    csr_values = []
    for match_skill in match_skills:
        for player_skill in match_skill.value:
            if player_skill.id == wrap_xuid(player.xuid):
                csr_values.append(player_skill.result.rank_recap.post_match_csr.value)
    num_matches = len(csr_values)

    # Reverse match indices
    matches = list(range(num_matches, 0, -1))

    # Matplotlib styling to match Discord theme
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(8, 4))

    # Plot the CSR progression
    ax.plot(matches, csr_values, marker='o', linestyle='-', color='#7289DA', markersize=6, label="CSR")
    ax.fill_between(matches, csr_values, min(csr_values)-10, color='#7289DA', alpha=0.2)

    # Labels and title
    ax.set_xlabel("Match Number", fontsize=12, color='white')
    ax.set_ylabel("CSR", fontsize=12, color='white')
    ax.set_title("CSR Progression Over Matches", fontsize=14, color='white')
    ax.legend(facecolor='#2C2F33', edgecolor='white', fontsize=10)

    # Grid styling
    ax.grid(color='#555', linestyle='dashed', linewidth=0.5, alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('white')
    ax.spines['bottom'].set_color('white')
    ax.tick_params(axis='both', colors='white')

    # Save the figure
    # Save to BytesIO
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', bbox_inches='tight', transparent=True)
    img_buf.seek(0)
    plt.close()

    return img_buf  # Return BytesIO object for Discord upload



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
    header = ['Gamertag', 'Team', 'Score', 'Kills', 'Deaths', 'K/D', 'Assists', 'Damage Dealt', 'Damage Taken', 'Damage Diff', 'Shots Hit', 'Shots Fired', 'Accuracy']
    player_totals = defaultdict(lambda: [0] * (len(header) - 2))  # Dict with default list for stats

    for match in matches:
        for match_player in match.match_stats.players:
            for team in match_player.player_team_stats:
                if match_player.is_human:
                    gamertag = next(player.gamertag for player in match.players if player.xuid == unwrap_xuid(match_player.player_id))
                else:
                    gamertag = BOT_MAP[match_player.player_id]
                core_stats = team.stats.core_stats
                team_name = f"{TEAM_MAP[team.team_id]}" if match.match_stats.match_info.teams_enabled else "FFA"
                
                # Aggregate stats per player
                if gamertag not in player_totals:
                    player_totals[gamertag] = [team_name, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # Init with team name

                stats = player_totals[gamertag]
                stats[1] += core_stats.personal_score
                stats[2] += core_stats.kills
                stats[3] += core_stats.deaths
                stats[5] += core_stats.assists
                stats[6] += core_stats.damage_dealt
                stats[7] += core_stats.damage_taken
                stats[9] += core_stats.shots_hit
                stats[10] += core_stats.shots_fired

    # Calculate accuracy after summing up
    values = []
    for gamertag, stats in player_totals.items():
        kills, deaths = stats[2], stats[3]
        kd = kills / deaths if deaths > 0 else kills
        shots_hit, shots_fired = stats[9], stats[10]
        accuracy = (shots_hit / shots_fired) * 100 if shots_fired > 0 else 0
        dmg_dealt, dmg_taken = stats[6], stats[7]
        total_damage = dmg_dealt - dmg_taken
        values.append([gamertag] + stats[:4] + [f"{kd:.2f}"] + stats[5:8] + [total_damage] + stats[9:11] + [f"{accuracy:.2f}"])

    values.sort(key=lambda value: value[1])  # Sort by team

    img_buf = await create_discord_table_image(values, header)
    
    return img_buf
    
    
async def create_match_table(match: Match):
    header = ['Gamertag', 'Team', 'Score', 'Kills', 'Deaths', 'K/D', 'Assists', 'Damage Dealt', 'Damage Taken', 'Damage Diff','Shots Hit', 'Shots Fired', 'Accuracy', 'Outcome']
    values=[]
    for match_player in match.match_stats.players:
        for team in match_player.player_team_stats:
            if match_player.is_human:
                gamertag = next(player.gamertag for player in match.players if player.xuid == unwrap_xuid(match_player.player_id))
            else:
                gamertag = BOT_MAP[match_player.player_id]
            core_stats = team.stats.core_stats
            team_name = f"{TEAM_MAP[team.team_id]}" if match.match_stats.match_info.teams_enabled else "FFA"
            player_stats = [
                gamertag, 
                f"{team_name}", 
                core_stats.personal_score, 
                core_stats.kills, 
                core_stats.deaths, 
                f"{core_stats.kills / core_stats.deaths if core_stats.deaths > 0 else core_stats.kills:.02f}",
                core_stats.assists, 
                core_stats.damage_dealt, 
                core_stats.damage_taken, 
                core_stats.damage_dealt - core_stats.damage_taken,
                core_stats.shots_hit, 
                core_stats.shots_fired, 
                core_stats.accuracy, 
                f"{OUTCOME_MAP[match_player.outcome]}"
                ]
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
    if match.match_stats.match_info.teams_enabled:
        for team_id, team in teams.items():
            match_embed.add_field(name=f"{TEAM_MAP[team_id]}", value="\n".join([player[0] for player in team]))
    else:
        match_embed.add_field(name="Players", value="\n".join([player.gamertag for player in match.players ]))
    

    match_embed.set_thumbnail(url=await get_map_image(match.match_map))
    match_embed.set_author(name="HaloBotti 2.0")
    match_embed.set_footer(text="HaloBotti 2.0 by AapoKaapo", icon_url="https://halofin.land/HaloFinland.png")
    
    image = await create_match_table(match)
    match_embed.set_image(url=f"attachment://{match.match_stats.match_id}.png")
    files = [
        File(image, f"{match.match_stats.match_id}.png")
    ]
    
    return match_embed, files
    

async def determine_team_outcomes(match_history: List[Match]):
    match_data = []
    for match in match_history:
        team_info = defaultdict(lambda: {"players": [], "outcomes": []})
        for player in match.match_stats.players:
            if player.is_human:
                gamertag = [profile.gamertag for profile in match.players if profile.xuid == unwrap_xuid(player.player_id)][0]
            else:
                gamertag = BOT_MAP[player.player_id]
            for player_team_stats in player.player_team_stats:
                team_info[player_team_stats.team_id]["players"].append(gamertag)
                team_info[player_team_stats.team_id]["outcomes"].append(OUTCOME_MAP[player.outcome])
        for team_id, team in team_info.items():
            final_outcome = "UNDETERMINED"
            unique_outcomes = set(team["outcomes"])  # Get unique outcome types
            for outcome in unique_outcomes:
                count = sum(1 for o in team["outcomes"] if o == outcome)  # Manually count occurrences
                if count / len(team["players"]) >= 0.5:
                    final_outcome = outcome

            # Extract player gamertags as a set for comparison
            team_players_set = {existing_player for existing_player in team["players"]}

            # Check if a team with the same players already exists
            existing_team = next((t for t in match_data if set(t["players"]) == team_players_set), None
            )
            if existing_team:
                existing_team["outcomes"].append(outcome)
            else:
                match_data.append({
                    "players": team["players"],
                    "outcomes": [outcome]
                })
                
    return match_data


async def create_series_info(match_history: List[Match]):
    title = "Series"
    
    description = await create_match_description(match_history)
    
    series_embed = Embed(title=title, description=description)
    
    teams_and_outcomes = await determine_team_outcomes(match_history)
    
    index = 0
    for team in teams_and_outcomes:
        index += 1
        players = "\n".join(team["players"])
        outcomes = "-".join(team["outcomes"])
        win_sum = team["outcomes"].count("WIN")
        tie_sum = team["outcomes"].count("TIE")
        loss_sum = team["outcomes"].count("LOSS")
        series_embed.add_field(name=f"Team #{index}- W:{win_sum}/T:{tie_sum}/L:{loss_sum}", value=f"{players}\n**Maps**:\n{outcomes}")
    
    image = await create_aggregated_match_table(match_history)
    random_uuid = uuid.uuid4()
    series_embed.set_image(url=f"attachment://{random_uuid}.png")
    files = [
        File(image, f"{random_uuid}.png")
    ]
    
    return series_embed, files


async def find_closest_rank(counterfactuals, tier_counterfactuals):
    def closest_by_stat(stat: str):
        return min(
            tier_counterfactuals,
            key=lambda rank: abs(getattr(counterfactuals, stat) - getattr(tier_counterfactuals[rank], stat))
        )

    closest_kills = closest_by_stat("kills")
    closest_deaths = closest_by_stat("deaths")

    return closest_kills, closest_deaths


from spnkr_app.tools import estimate_tier
from spnkr.models.skill import Counterfactual

async def create_match_skill_embed(profiles, match_skill):
    match_embed = Embed(title="Match Skill Embed")
    for player in match_skill.value:
        profile = next((item for item in profiles if wrap_xuid(item.xuid) == player.id), None)
        self_counterfactuals = player.result.counterfactuals.self_counterfactuals
        tier_counterfactuals = player.result.counterfactuals.tier_counterfactuals
        
        expected_kills, expected_deaths = await find_closest_rank(self_counterfactuals, tier_counterfactuals)
        actual_kills, actual_deaths = player.result.stat_performances.kills.count, player.result.stat_performances.deaths.count
        estimated_tier = await estimate_tier(self_counterfactuals, tier_counterfactuals)
        performance_tier = await estimate_tier(Counterfactual(kills=actual_kills, deaths=actual_deaths), tier_counterfactuals)
        match_embed.add_field(name=f"{profile.gamertag}", value=f"Kills:{actual_kills}, Deaths:{actual_deaths}\nEstimated Rank: {estimated_tier}\nPerformance Rank: {performance_tier}", inline=False)

    return match_embed


async def create_rank_embed(player, match_skills):
    rank_embed = Embed(title=f"Ranked progression of {player.gamertag}")
    image = await generate_csr_graph(player, match_skills)

    random_uuid = uuid.uuid4()
    rank_embed.set_image(url=f"attachment://{random_uuid}.png")
    files = [
        File(image, f"{random_uuid}.png")
    ]

    return rank_embed, files

