import asyncio

from spnkr.models.skill import MatchSkill
from spnkr.xuid import wrap_xuid
from sqlalchemy.exc import IntegrityError

from app.tokens import AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI, AZURE_REFRESH_TOKEN
from aiohttp import ClientSession, ClientResponseError
from spnkr import HaloInfiniteClient, AzureApp, refresh_player_tokens, authenticate_player
from spnkr.models.stats import MatchStats
from spnkr.models.discovery_ugc import Asset, Map, UgcGameVariant
from spnkr.models.profile import User
from typing import List, Dict, Literal, Optional
from pydantic import BaseModel
import time
from database_app.database import get_player_by_xuid, add_custom_player
from database_app.models import CustomPlayer
from spnkr.tools import unwrap_xuid, BOT_MAP

RANKED_PLAYLIST = "edfef3ac-9cbe-4fa2-b949-8f29deafd483"


player_cache = None  # Global cache for the player instance

async def get_client():
    global player_cache
    app = AzureApp(AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI)

    async with ClientSession() as session:
        if player_cache is None or not player_cache.is_valid:
            player_cache = await refresh_player_tokens(session, app, AZURE_REFRESH_TOKEN)
            
        client = HaloInfiniteClient(
            session=session,
            spartan_token=f"{player_cache.spartan_token.token}",
            clearance_token=f"{player_cache.clearance_token.token}",
            # Optional, default rate is 5.
            requests_per_second=5,
        )

        yield client


class Match(BaseModel):
    match_stats: MatchStats
    players: List[User|CustomPlayer]
    match_map: Optional[Asset]
    match_gamemode: Optional[Asset]


async def add_xbl_profile_to_db(profile):
    try:
        db_player = await add_custom_player(profile)
    except IntegrityError:
        db_player = await get_player_by_xuid(profile.xuid)

    return db_player


async def get_xbl_profiles(client, xuids):
    profiles = []
    count = 0
    for i in range(0, len(xuids), 100):
        batch = xuids[i:i + 100]
        if count == 10:
            seconds = 300
            print(f"Burst limit reached. Sleeping for {seconds} seconds")
            await asyncio.sleep(seconds)
            count = 0
        try:
            resp = await client.profile.get_users_by_id(batch)
            batch_profiles = await resp.parse()
        except ClientResponseError as e:
            if e.status == 429:
                seconds = 300
                print(f"Burst limit reached. Sleeping for {seconds} seconds")
                await asyncio.sleep(seconds)
                resp = await client.profile.get_users_by_id(batch)
                batch_profiles = await resp.parse()
        except TypeError:
            resp = await client.profile.get_user_by_gamertag(batch)
            batch_profiles = [await resp.parse()]


        profiles += batch_profiles
        count += 1
        await asyncio.sleep(1)

    return profiles


async def get_match_stats(client: HaloInfiniteClient, match_id) -> MatchStats:
    start = time.time()
    resp = await client.stats.get_match_stats(match_id)
    end = time.time()
    print("Response Took %f ms" % ((end - start) * 1000.0))


    start = time.time()
    match_stats = await resp.parse()
    end = time.time()
    print("Pydantic Took %f ms" % ((end - start) * 1000.0))

    return match_stats


async def get_map_asset(client: HaloInfiniteClient, asset) -> Map:
    try:
        resp = await client.discovery_ugc.get_map(
            asset.asset_id,
            asset.version_id
        )
        asset = await resp.parse()
    except ClientResponseError as e:
        if e.status == 404:
            asset = None

    return asset


async def get_gamemode_asset(client: HaloInfiniteClient, asset) -> UgcGameVariant:
    try:
        resp = await client.discovery_ugc.get_ugc_game_variant(
            asset.asset_id,
            asset.version_id
        )
        asset = await resp.parse()
    except ClientResponseError as e:
        if e.status == 404:
            asset = None

    return asset


async def get_playlist_asset(client: HaloInfiniteClient, asset) -> Asset:
    try:
        resp = await client.discovery_ugc.get_playlist(
            asset.asset_id,
            asset.version_id
        )
        asset = await resp.parse()
    except ClientResponseError as e:
        if e.status == 404:
            asset = None

    return asset


async def get_ranked_match_result(client, match):
    if match.match_info.playlist:
        playlist = await get_playlist_asset(client, match.match_info.playlist)
        if playlist and playlist.public_name == "Ranked Arena":
            return match

    return None


async def get_match_history(client, player: str|int, start: int=0, count: int=25, match_type="all"):
    looking_for_ranked = False
    if match_type == "ranked":
        # there is no way to look for just ranked type so we have to fetch all types and match the playlist id with ranked
        match_type = "all"
        looking_for_ranked = True
    results = []

    while len(results) < count:
        response = await client.stats.get_match_history(player, start, count, match_type)
        match_history = await response.parse()
        if len(match_history.results) == 0:
            return results

        if looking_for_ranked:
            async_tasks = []
            for match in match_history.results:
                task = get_ranked_match_result(client, match)
                async_tasks.append(task)
            ranked_matches = await asyncio.gather(*async_tasks)
            results += [item for item in ranked_matches if item is not None]

        else:
            results += match_history.results

        start += 25

    return results[:count]


class BotPlayer(BaseModel):
    gamertag: str
    xuid: str


class CustomMatch(BaseModel):
    match_stats: MatchStats
    match_gamemode: Optional[UgcGameVariant]
    match_map: Optional[Map]
    players: Optional[List[CustomPlayer|BotPlayer]]


async def create_custom_match(client, match_players, match_stats):
    profiles = [item for item in match_players if item.xuid in match_stats.xuids]
    bots = [BotPlayer(gamertag=BOT_MAP[player.player_id], xuid=player.player_id) for player in match_stats.players if player.player_id not in [wrap_xuid(profile.xuid) for profile in profiles]]
    gamemode_asset = await get_gamemode_asset(client, match_stats.match_info.ugc_game_variant)
    map_asset = await get_map_asset(client, match_stats.match_info.map_variant)
    custom_match = CustomMatch(match_stats=match_stats, match_gamemode=gamemode_asset, match_map=map_asset, players=profiles+bots)

    return custom_match


async def fetch_player_match_data(gamertag: str|int, start=0, count=25, match_type="all"):
    async for client in get_client():
        match_history = await get_match_history(client, gamertag, start, count, match_type)
        async_tasks =[]
        for match_history_result in match_history:
            match_stats = get_match_stats(client, match_history_result.match_id)
            async_tasks.append(match_stats)
        match_results = await asyncio.gather(*async_tasks)
        xuids = []
        for match_stats in match_results:
            xuids += match_stats.xuids
        xbl_profiles = await get_xbl_profiles(client, list(set(xuids)))
        async_tasks = []
        for profile in xbl_profiles:
            async_tasks.append(asyncio.create_task(add_xbl_profile_to_db(profile)))
        match_players = await asyncio.gather(*async_tasks)

        tasks = []
        for match_stats in match_results:
            task = create_custom_match(client, match_players, match_stats)
            tasks.append(task)
        custom_matches = await asyncio.gather(*tasks)

        return custom_matches


async def get_match_skills(client, match_id, xuids):
    try:
        resp = await client.skill.get_match_skill(match_id, xuids)
        match_skill = await resp.parse()
        return match_skill
    except ClientResponseError as e:
        if e.status == 404:
            return None
        else:
            raise e


async def fetch_player_match_skills(gamertag: str|int, start=0, count=25, match_type="ranked") -> List[MatchSkill]:
    async for client in get_client():
        start_time = time.time()
        match_history = await get_match_history(client, gamertag, start, count, match_type)
        end_time = time.time()
        print("match_history took %f ms" % ((end_time - start_time) * 1000.0))
        profile = await get_xbl_profiles(client, gamertag)
        skill_tasks = []
        custom_match_tasks = []
        for match_history_result in match_history:
            match_stats = await get_match_stats(client, match_history_result.match_id)
            match_skill = get_match_skills(client, match_history_result.match_id, match_stats.xuids)

            skill_tasks.append(match_skill)
        match_skills = await asyncio.gather(*skill_tasks)
        match_skills = [item for item in match_skills if item is not None]

        return match_skills



async def get_profile(gamertag: str|int):
    async for client in get_client():
        resp = await client.profile.get_user_by_gamertag(gamertag)
        profile = await resp.parse()

        return profile


async def generate_spartan_tokens(AZURE_REFRESH_TOKEN) -> None:
    app = AzureApp(AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI)

    async with ClientSession() as session:
        player = await refresh_player_tokens(session, app, AZURE_REFRESH_TOKEN)
        print(f"Spartan token: {player.spartan_token.token}")  # Valid for 4 hours.
        print(f"Clearance token: {player.clearance_token.token}")
        print(f"Xbox Live player ID (XUID): {player.player_id}")
        print(f"Xbox Live gamertag: {player.gamertag}")
        print(f"Xbox Live authorization: {player.xbl_authorization_header_value}")

        client = HaloInfiniteClient(
            session=session,
            spartan_token=f"{player.spartan_token.token}",
            clearance_token=f"{player.clearance_token.token}",
            # Optional, default rate is 5.
            requests_per_second=5,
        )
        PLAYER = "AapoKaapo"
        # Request the 25 most recent matches for the player.
        resp = await client.stats.get_match_history(PLAYER)
        # Parse the response JSON into a Pydantic model
        history = await resp.parse()

        # Get the most recent match played and print the start time.
        last_match_info = history.results[0].match_info
        print(f"Last match played on {last_match_info.start_time:%Y-%m-%d}")


async def generate_tokens() -> None:
    app = AzureApp(AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI)

    async with ClientSession() as session:
        AZURE_REFRESH_TOKEN = await authenticate_player(session, app)
        print(f"Your refresh token is:\n{AZURE_REFRESH_TOKEN}")
        await generate_spartan_tokens(AZURE_REFRESH_TOKEN)


if __name__ == "__main__":
    asyncio.run(generate_tokens())
