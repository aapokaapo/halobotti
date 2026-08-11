import io
import uuid
from collections import defaultdict
from typing import List, Optional

import matplotlib.pyplot as plt
from aiohttp import ClientSession
from discord import Embed, File
from spnkr.models.skill import Counterfactual
from spnkr.tools import BOT_MAP, LIFECYCLE_MAP, OUTCOME_MAP, TEAM_MAP, unwrap_xuid
from spnkr.xuid import wrap_xuid

from spnkr_app import Match
from spnkr_app.tools import estimate_tier

# Discord dark-theme colour palette used for matplotlib graphs
DISCORD_COLORS = {
    'bg': '#2C2F33',
    'text': '#DCDDDE',
    'header': '#7289DA',
    'alt_row': '#23272A',
    'border': '#99AAB5',
}

MATPLOTLIB_FIGSIZE_GRAPH = (10, 5)
MATPLOTLIB_FONT_SIZE = 12
MATPLOTLIB_TITLE_SIZE = 14
# x-position (in axis-fraction coordinates) for rank labels: just past the right edge
RANK_LABEL_X_POSITION = 1.01


def _configure_matplotlib_style() -> None:
    """Apply Discord dark-theme styling to matplotlib."""
    plt.rcParams.update({
        'figure.facecolor': DISCORD_COLORS['bg'],
        'axes.facecolor': DISCORD_COLORS['bg'],
        'text.color': DISCORD_COLORS['text'],
        'axes.labelcolor': DISCORD_COLORS['text'],
        'xtick.color': DISCORD_COLORS['text'],
        'ytick.color': DISCORD_COLORS['text'],
    })


def _save_figure_to_buffer() -> io.BytesIO:
    """Save the current matplotlib figure to a BytesIO buffer and close it."""
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', transparent=True)
    buf.seek(0)
    plt.close()
    return buf


async def get_map_image(map_asset) -> str:
    async with ClientSession() as session:
        map_image_url = map_asset.files.prefix + "images/thumbnail.jpg"
        response = await session.get(map_image_url)

        if response.status == 404:
            map_image_url = map_asset.files.prefix + "images/thumbnail.png"
            response = await session.get(map_image_url)

            if response.status == 404:
                map_image_url = "https://img.freepik.com/premium-vector/default-image-icon-vector-missing-picture-page-website-design-mobile-app-no-photo-available_87543-11093.jpg"

    return map_image_url

ranks = {
    'onyx': {'csr': 1500, 'color': '#800080'},  # Purple
    'diamond 6': {'csr': 1450, 'color': '#00BFFF'},  # Sky Blue
    'diamond 5': {'csr': 1400, 'color': '#00BFFF'},
    'diamond 4': {'csr': 1350, 'color': '#00BFFF'},
    'diamond 3': {'csr': 1300, 'color': '#00BFFF'},
    'diamond 2': {'csr': 1250, 'color': '#00BFFF'},
    'diamond 1': {'csr': 1200, 'color': '#00BFFF'},
    'platinum 6': {'csr': 1150, 'color': '#C0C0C0'},  # Silver
    'platinum 5': {'csr': 1100, 'color': '#C0C0C0'},
    'platinum 4': {'csr': 1050, 'color': '#C0C0C0'},
    'platinum 3': {'csr': 1000, 'color': '#C0C0C0'},
    'platinum 2': {'csr': 950, 'color': '#C0C0C0'},
    'platinum 1': {'csr': 900, 'color': '#C0C0C0'},
    'gold 6': {'csr': 850, 'color': '#FFD700'},  # Gold
    'gold 5': {'csr': 800, 'color': '#FFD700'},
    'gold 4': {'csr': 750, 'color': '#FFD700'},
    'gold 3': {'csr': 700, 'color': '#FFD700'},
    'gold 2': {'csr': 650, 'color': '#FFD700'},
    'gold 1': {'csr': 600, 'color': '#FFD700'},
    'silver 6': {'csr': 550, 'color': '#B0C4DE'},  # Light Steel Blue
    'silver 5': {'csr': 500, 'color': '#B0C4DE'},
    'silver 4': {'csr': 450, 'color': '#B0C4DE'},
    'silver 3': {'csr': 400, 'color': '#B0C4DE'},
    'silver 2': {'csr': 350, 'color': '#B0C4DE'},
    'silver 1': {'csr': 300, 'color': '#B0C4DE'},
    'bronze 6': {'csr': 250, 'color': '#CD7F32'},  # Bronze
    'bronze 5': {'csr': 200, 'color': '#CD7F32'},
    'bronze 4': {'csr': 150, 'color': '#CD7F32'},
    'bronze 3': {'csr': 100, 'color': '#CD7F32'},
    'bronze 2': {'csr': 50, 'color': '#CD7F32'},
    'bronze 1': {'csr': 0, 'color': '#CD7F32'}
}


async def generate_csr_graph(player, match_skills) -> io.BytesIO:
    """Generate CSR progression line chart with rank thresholds."""
    # Extract CSR values from match_skills
    csr_values = [
        player_skill.result.rank_recap.post_match_csr.value
        for match_skill in match_skills
        for player_skill in match_skill.value
        if player_skill.id == wrap_xuid(player.xuid)
    ]

    if not csr_values:
        return None

    num_matches = len(csr_values)
    matches = list(range(num_matches, 0, -1))

    # Matplotlib styling to match Discord theme
    _configure_matplotlib_style()
    fig, ax = plt.subplots(figsize=MATPLOTLIB_FIGSIZE_GRAPH)

    # Plot the CSR progression
    ax.plot(
        matches,
        csr_values,
        marker='o',
        linestyle='-',
        color=DISCORD_COLORS['header'],
        markersize=6,
        label="CSR"
    )
    ax.fill_between(
        matches,
        csr_values,
        min(csr_values) - 10,
        color=DISCORD_COLORS['header'],
        alpha=0.2
    )

    ax.legend(facecolor=DISCORD_COLORS['bg'], edgecolor=DISCORD_COLORS['text'], fontsize=10)

    # Grid styling
    ax.grid(color='#555', linestyle='dashed', linewidth=0.5, alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(DISCORD_COLORS['text'])
    ax.spines['bottom'].set_color(DISCORD_COLORS['text'])
    ax.tick_params(axis='both', colors=DISCORD_COLORS['text'])

    # Set x-axis increments to 2
    ax.set_xticks(range(num_matches, 0, -2))

    # Determine visible y-range
    y_min, y_max = ax.get_ylim()

    # Add horizontal dotted lines for ranks within range
    for rank, info in ranks.items():
        csr = info['csr']
        color = info['color']
        if y_min <= csr <= y_max:
            ax.axhline(y=csr, linestyle='dotted', color=color, linewidth=1)
            ax.text(
                RANK_LABEL_X_POSITION, csr, rank.title(),
                color=color,
                fontsize=10,
                verticalalignment='center',
                transform=ax.get_yaxis_transform(),
                clip_on=False,
            )

    return _save_figure_to_buffer()

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

    for cell_key in table._cells:
        cell = table._cells[cell_key]
        cell.set_edgecolor(border_color)  # Subtle thin border
        cell.set_linewidth(0.7)  # Thin border
        cell.set_height(0.15)  # Increase row height

        # Header styling
        if cell_key[0] == 0:
            cell.set_facecolor(header_color)
            cell.set_text_props(color=text_color, weight='bold')
        else:  # Data row styling
            cell.set_facecolor(bg_color if cell_key[0] % 2 == 0 else alt_row_color)
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
    header = ['Gamertag', 'Team', 'Score', 'Kills', 'Deaths', 'K/D', 'Assists', 'Damage Dealt', 'Damage Taken', 'Damage Diff', 'Shots Hit', 'Shots Fired', 'Accuracy', 'Outcome']
    values = []
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
        match_embed.add_field(name="Players", value="\n".join([player.gamertag for player in match.players]))

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
            existing_team = next((t for t in match_data if set(t["players"]) == team_players_set), None)
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


async def create_match_skill_embed(profiles, match_skill):
    match_embed = Embed(title="Match Skill Breakdown")
    match_embed.set_footer(
        text="HaloBotti 2.0 by AapoKaapo",
        icon_url="https://halofin.land/HaloFinland.png",
    )

    current_team_id = None
    for player in sorted(match_skill.value, key=lambda p: p.result.team_id):
        team_id = player.result.team_id

        if team_id != current_team_id:
            current_team_id = team_id
            team_label = TEAM_MAP.get(team_id, f"Team {team_id}")
            match_embed.add_field(name=f"── {team_label} ──", value="", inline=False)

        profile = next((item for item in profiles if wrap_xuid(item.xuid) == player.id), None)
        gamertag = profile.gamertag if profile else str(player.id)

        self_counterfactuals = player.result.counterfactuals.self_counterfactuals
        tier_counterfactuals = player.result.counterfactuals.tier_counterfactuals
        rank_recap = player.result.rank_recap

        actual_kills = player.result.stat_performances.kills.count
        actual_deaths = player.result.stat_performances.deaths.count
        kd_ratio = actual_kills / actual_deaths if actual_deaths > 0 else float(actual_kills)

        exp_kills = round(self_counterfactuals.kills, 1)
        exp_deaths = round(self_counterfactuals.deaths, 1)

        estimated_tier = await estimate_tier(self_counterfactuals, tier_counterfactuals)
        performance_tier = await estimate_tier(
            Counterfactual(kills=actual_kills, deaths=actual_deaths), tier_counterfactuals
        )
        exp_kills_rank, exp_deaths_rank = await find_closest_rank(
            self_counterfactuals, tier_counterfactuals
        )

        current_csr = rank_recap.pre_match_csr.value

        value_lines = [
            f"K/D: {actual_kills}/{actual_deaths} ({kd_ratio:.2f}) | exp K/D: {exp_kills}/{exp_deaths}",
            f"K-rank: {exp_kills_rank} | D-rank: {exp_deaths_rank}",
            f"CSR: {current_csr} | MMR: {estimated_tier} | perf: {performance_tier}",
        ]
        match_embed.add_field(name=gamertag, value="\n".join(value_lines), inline=False)

    return match_embed


async def create_rank_embed(player, match_skills):
    wrapped_xuid = wrap_xuid(player.xuid)

    # Collect per-match data for the player
    csr_values = []
    outcomes = []
    total_kills = 0
    total_deaths = 0
    match_count = 0

    for match_skill in match_skills:
        for player_skill in match_skill.value:
            if player_skill.id != wrapped_xuid:
                continue
            recap = player_skill.result.rank_recap
            csr_values.append(recap.post_match_csr.value)
            # Determine outcome from CSR delta
            delta = recap.post_match_csr.value - recap.pre_match_csr.value
            if delta > 0:
                outcomes.append("WIN")
            elif delta < 0:
                outcomes.append("LOSS")
            else:
                outcomes.append("TIE")
            total_kills += player_skill.result.stat_performances.kills.count
            total_deaths += player_skill.result.stat_performances.deaths.count
            match_count += 1

    wins = outcomes.count("WIN")
    losses = outcomes.count("LOSS")
    ties = outcomes.count("TIE")

    current_csr = csr_values[0] if csr_values else None
    oldest_csr = csr_values[-1] if csr_values else None
    csr_trend = (current_csr - oldest_csr) if (current_csr is not None and oldest_csr is not None) else None

    avg_kills = total_kills / match_count if match_count else 0
    avg_deaths = total_deaths / match_count if match_count else 0
    avg_kd = total_kills / total_deaths if total_deaths > 0 else float(total_kills)

    # Estimate hidden MMR from the most recent match skill entry
    hidden_mmr = None
    for match_skill in match_skills:
        for player_skill in match_skill.value:
            if player_skill.id == wrapped_xuid:
                self_cf = player_skill.result.counterfactuals.self_counterfactuals
                tier_cf = player_skill.result.counterfactuals.tier_counterfactuals
                hidden_mmr = await estimate_tier(self_cf, tier_cf)
                break
        if hidden_mmr is not None:
            break

    trend_str = ""
    if csr_trend is not None:
        trend_str = f"+{csr_trend}" if csr_trend >= 0 else str(csr_trend)

    rank_embed = Embed(title=f"Ranked Progression — {player.gamertag}")

    if current_csr is not None:
        rank_embed.add_field(
            name="Current CSR",
            value=f"**{current_csr}** ({trend_str} over {match_count} matches)",
            inline=False,
        )

    rank_embed.add_field(
        name="W / L / T",
        value=f"**{wins}** / **{losses}** / **{ties}**",
        inline=True,
    )
    rank_embed.add_field(
        name="Avg K/D",
        value=f"**{avg_kd:.2f}** ({avg_kills:.1f} kills / {avg_deaths:.1f} deaths)",
        inline=True,
    )

    if hidden_mmr is not None:
        rank_embed.add_field(
            name="Hidden MMR (est.)",
            value=f"**{hidden_mmr}**",
            inline=True,
        )

    rank_embed.set_footer(
        text="HaloBotti 2.0 by AapoKaapo",
        icon_url="https://halofin.land/HaloFinland.png",
    )

    image = await generate_csr_graph(player, match_skills)

    random_uuid = uuid.uuid4()
    rank_embed.set_image(url=f"attachment://{random_uuid}.png")
    files = [
        File(image, f"{random_uuid}.png")
    ]

    return rank_embed, files
