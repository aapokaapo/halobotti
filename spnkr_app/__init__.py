import asyncio

from app.tokens import AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI, AZURE_REFRESH_TOKEN
from aiohttp import ClientSession
from spnkr import HaloInfiniteClient, AzureApp, refresh_player_tokens, authenticate_player


async def main():
    app = AzureApp(AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, REDIRECT_URI)

    async with ClientSession() as session:
        #refresh_token = await authenticate_player(session, app)
        #print(refresh_token)
        player = await refresh_player_tokens(session, app, AZURE_REFRESH_TOKEN)
        client = HaloInfiniteClient(
            session=session,
            spartan_token=f"{player.spartan_token}",
            clearance_token=f"{player.clearance_token}",
            # Optional, default rate is 5.
            requests_per_second=5,
        )
        print(f"Spartan token: {player.spartan_token.token}")  # Valid for 4 hours.
        print(f"Clearance token: {player.clearance_token.token}")
        print(f"Xbox Live player ID (XUID): {player.player_id}")
        print(f"Xbox Live gamertag: {player.gamertag}")
        print(f"Xbox Live authorization: {player.xbl_authorization_header_value}")
        print(player.is_valid)

        yield client


def get_client():
    awaitable = anext(main())
    return awaitable


async def get_match(match_id, *args):
    awaitable = get_client()
    client = await awaitable
    resp = await client.stats.get_match_stats(match_id)
    match_stats = await resp.parse()
    print(match_stats.match_id)



