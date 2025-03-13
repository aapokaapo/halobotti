from spnkr.models.stats import MatchStats

from . import get_gamemode_asset

import datetime


async def check_if_teams_enabled(
        match_stats: MatchStats
):
    """
    Check whether the teams are enabled. If not then it is FFA and thus not eligible for ranking

    :param match_stats:
    :return bool:
    """
    return match_stats.match_info.teams_enabled


async def check_match_date(
        match_stats: MatchStats,
        date: datetime.datetime
):
    """
    Check if match was played after 1st of Jan 2024 and thus is eligible to be added to database

    :param date:
    :param match_stats:
    :return bool:
    """
    if match_stats.match_info.start_time >= date:
        return True
    else:
        return False


async def check_match_validity(
        match,
        date: datetime.date = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
):
    """
    Determines the validity of a match for be used in ranking as in was it ended by the host or was it played
    till either scorelimit or timelimit was reached. For match to be valid, there has to also be 8 players in
    the lobby at the end of the match and the gamemode has to be one of listed in scorelimit/timelimit

    :param date:
    :param match_stats:
    :return bool:
    """
    # "<Gamemode name>": [score needed to win, rounds needed to win]
    scorelimit = {
        "Ranked:King of the Hill": [4, 1],
        "Ranked:Strongholds": [250, 1],
        "Ranked:Oddball": [200, 2],
        "Ranked:CTF 3 Captures": [3, 1],
        "Ranked:CTF 5 Captures": [5, 1],
        "Ranked:Slayer": [50, 1],
        "Ranked:Extraction": [4, 1],
        "Ranked:CTF": [5,1],
        "Assault:Neutral Bomb Ranked": [1,1]
    }

    timelimit = {
        "Ranked:King of the Hill": datetime.timedelta(minutes=5),
        "Ranked:Strongholds": datetime.timedelta(minutes=9000),
        "Ranked:Oddball": datetime.timedelta(minutes=2 * 5),
        "Ranked:CTF 3 Captures": datetime.timedelta(minutes=12),
        "Ranked:CTF 5 Captures": datetime.timedelta(minutes=12),
        "Ranked:Slayer": datetime.timedelta(minutes=12),
        "Ranked:Extraction": datetime.timedelta(minutes=12),
        "Ranked:CTF": datetime.timedelta(minutes=12),
        "Assault:Neutral Bomb Ranked": datetime.timedelta(minutes=12)
    }
    match_stats = match.match_stats

    date_passes = await check_match_date(match_stats, date)
    if not date_passes:
        return False

    teams_enabled = await check_if_teams_enabled(match_stats)
    if not teams_enabled:
        return False

    
    if match.match_gamemode.public_name not in scorelimit.keys():
        return False

    # if timelimit for the gamemode has run out and there are 8 players present at the end of the match in lobby,
    # it's a valid match
    if (
            match_stats.match_info.playable_duration >= timelimit[match.match_gamemode.public_name]
            and
            len([player for player in match_stats.players if player.participation_info.present_at_completion]) == 8
            and
            [
                team for team in match_stats.teams
                if (
                    team.stats.core_stats.rounds_won == scorelimit[match.match_gamemode.public_name][1]
                )
            ]

    ):
        return True
    # if scorelimit for the gamemode has been reached and there are 8 players present at the end of the match in lobby,
    # it's a valid match
    elif (
            [
                team for team in match_stats.teams
                if (
                    team.stats.core_stats.score >= scorelimit[match.match_gamemode.public_name][0]
                    and
                    team.stats.core_stats.rounds_won == scorelimit[match.match_gamemode.public_name][1]
                )
            ]
            and
            len([player for player in match_stats.players if player.participation_info.present_at_completion]) == 8
    ):
        return True
    else:
        return False
