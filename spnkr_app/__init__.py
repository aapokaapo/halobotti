import asyncio

from app.tokens import AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI, AZURE_REFRESH_TOKEN
from aiohttp import ClientSession
from spnkr import HaloInfiniteClient, AzureApp, refresh_player_tokens, authenticate_player
from spnkr.models.stats import MatchStats
from spnkr.models.discovery_ugc import Asset, Map, UgcGameVariant
from spnkr.models.profile import User
from typing import List, Dict, Literal
from pydantic import BaseModel


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
    players: List[User]
    map: Asset
    gamemode: Asset


async def get_profiles(client: HaloInfiniteClient, match_stats: MatchStats) -> List[User]:
    resp = await client.profile.get_users_by_id([player.player_id for player in match_stats.players])
    profiles = await resp.parse()

    return profiles


async def get_match_stats(client: HaloInfiniteClient, match_id) -> MatchStats:
    resp = await client.stats.get_match_stats(match_id)
    match_stats = await resp.parse()

    return match_stats


async def get_gamemode_asset(client: HaloInfiniteClient, match_stats: MatchStats) -> UgcGameVariant:
    resp = await client.discovery_ugc.get_ugc_game_variant(
        match_stats.match_info.ugc_game_variant.asset_id,
        match_stats.match_info.ugc_game_variant.version_id
    )
    gamemode_asset = await resp.parse()

    return gamemode_asset


async def get_map_asset(client: HaloInfiniteClient, match_stats: MatchStats) -> Map:
    resp = await client.discovery_ugc.get_map(
        match_stats.match_info.map_variant.asset_id,
        match_stats.match_info.map_variant.version_id
    )
    map_asset = await resp.parse()

    return map_asset


async def get_match(match_id):
    async for client in get_client():
        match_stats = await get_match_stats(client, match_id)
        
        map_asset = await get_map_asset(client, match_stats)
        gamemode_asset = await get_gamemode_asset(client, match_stats)
        players = await get_profiles(client, match_stats)

        match = Match(
            match_stats=match_stats,
            map=map_asset,
            gamemode=gamemode_asset,
            players=players
        )

        return match


async def get_match_history(player: str|int, start: int=0, count: int=25, match_type="all"):

    async for client in get_client():
        if match_type != "ranked":
            response = await client.stats.get_match_history(player, start, count, match_type)
            match_history = await response.parse()

            return match_history.results

        else:
            results = []
            match_type = "all"
            while len(results) < count and start < 101:
                response = await client.stats.get_match_history(player, start, count, match_type)
                match_history = await response.parse()
                for match in match_history.results:
                    if match.match_info.playlist.asset_id == RANKED_PLAYLIST:
                        results.append(match)

                start += 25

            return results



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
