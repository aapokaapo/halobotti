from sqlmodel import SQLModel, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession as Session
from sqlalchemy.ext.asyncio import create_async_engine
from .models import *

from sqlalchemy.exc import IntegrityError, InvalidRequestError


sqlite_file_name = "database.db"
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"
connect_args = {"check_same_thread": False}

engine = create_async_engine(sqlite_url, future=True, echo=False, connect_args=connect_args)


async def engine_start() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_player(gamertag):
    async with Session(engine) as session:
        statement = select(CustomPlayer).where(CustomPlayer.gamertag == gamertag)
        results = await session.exec(statement)
        player = results.unique().first()

        return player


async def add_custom_player(profile, is_valid=False):
    player = CustomPlayer(gamertag=profile.gamertag, xuid=profile.xuid, is_valid=is_valid, custom_matches=[])
    async with Session(engine, expire_on_commit=False) as session:
        try:
            session.add(player)
            await session.commit()
            await session.refresh(player)
            return player
            
        except IntegrityError as e:
            await session.rollback()
            raise e


async def get_player_by_xuid(xuid):
    async with Session(engine) as session:
        statement = select(CustomPlayer).where(CustomPlayer.xuid == xuid)
        results = await session.exec(statement)
        player = results.unique().first()
    
        return player


async def update_player(gamertag, value, validation_message=False):
    async with Session(engine, expire_on_commit=False) as session:
        player = await get_player(gamertag)
        if player:
            player.is_valid = value
            if validation_message:
                player.validation_message = validation_message
            session.add(player)
            await session.commit()
            await session.refresh(player)
        
            return player
        

async def get_players():
    async with Session(engine) as session:
        statement = select(CustomPlayer).where(CustomPlayer.is_valid == True)
        results = await session.exec(statement)
        players = results.unique().all()
        return players


async def add_players_in_match(match):
    custom_players = []
    for player in match.players:
        if not player.xuid.startswith("bid"):
            try:
                await add_custom_player(player)
            except IntegrityError:
                pass
            custom_player = await get_player(player.gamertag)
            custom_players.append(custom_player)
    return custom_players


async def add_custom_match(match, is_valid: bool = False):

    async with Session(engine, expire_on_commit=False) as session:
        custom_match = CustomMatch(
            match_id=match.match_stats.match_id,
            players=[],
            is_vslid=is_valid
        )
        try:
            session.add(custom_match)
            await session.commit()
            await session.refresh(custom_match)
            return custom_match
            
        except IntegrityError as e:
            await session.rollback()
            raise e


async def get_match(match_id):
    async with Session(engine) as session:
        statement = select(CustomMatch).where(CustomMatch.match_id == match_id)
        results = await session.exec(statement)
        match = results.unique().one()
    
        return match


async def update_match(match_id, value):
    async with Session(engine) as session:
        match = await get_match(match_id)
        match.is_valid = value
        session.add(match)
        await session.commit()
        await session.refresh(match)
        
        return match


async def get_all_matches():
    async with Session(engine) as session:
        statement = select(CustomMatch)
        results = await session.exec(statement)
        matches = results.unique().all()

        return matches


async def add_match_to_players(custom_match_id, players):
    async with Session(engine) as session:
        match = await get_match(custom_match_id)
        for player in players:
            custom_player = await get_player(player.gamertag)
            if custom_player:
                custom_player.custom_matches.append(match)
                session.add(custom_player)
                await session.commit()


async def add_channel(guild_id):
    channel = Channel(guild_id=guild_id)
    async with Session(engine) as session:
        try:
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            return channel
        
        except IntegrityError as e:
            await session.rollback()
            raise e
        

async def update_channel(guild_id, log_channel=None, leaderboard_channel=None):
    async with Session(engine) as session:
        statement = select(Channel).where(Channel.guild_id == guild_id)
        results = await session.exec(statement)
        channel = results.one()
        
        if log_channel:
            channel.log_channel_id = log_channel
        if leaderboard_channel:
            channel.leaderboard_channel_id = leaderboard_channel
        
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        
        return channel


async def get_log_channel():
    async with Session(engine) as session:
        statement = select(Channel)
        results = await session.exec(statement)
        channel = results.first()

        return channel


async def get_all_channels():
    async with Session(engine) as session:
        statement = select(Channel)
        results = await session.exec(statement)
        channels = results.all()

        return channels
