from sqlmodel import SQLModel, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession as Session
from .models import *

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

sqlite_file_name = "database.db"
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"

engine = create_async_engine(sqlite_url)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    

async def engine_start():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def add_custom_player(profile):
    player = CustomPlayer(gamertag=profile.gamertag, xuid=profile.xuid, custom_matches=[])
    async with Session(engine) as session:
        try:
            session.add(player)
            await session.commit()
            await session.refresh(player)
            return player
            
        except IntegrityError as e:
            raise e


async def get_player(gamertag):
    async with Session(engine) as session:
        statement = select(CustomPlayer).where(CustomPlayer.gamertag == gamertag)
        results = await session.exec(statement)
        player = results.first()
    
        return player


async def update_player(gamertag, value):
    async with Session(engine) as session:
        player = await get_player(gamertag)
        if player:
            player.is_valid = value
            session.add(player)
            await session.commit()
            await session.refresh(player)
        
            return player
        

async def get_players():
    async with Session(engine) as session:
        statement = select(CustomPlayer).where(CustomPlayer.is_valid == True)
        results = await session.exec(statement)
        players = results.all()
        return players


async def add_custom_match(match):
    custom_players = []
    for player in match.players:
        # get the custom_player from db by gamertag
        custom_player = await get_player(player.gamertag)
        # if custom_player doesn't exist in db, create it
        if not custom_player:
            custom_player = await add_custom_player(player)
            custom_players.append(custom_player)
    
    async with Session(engine) as session:
        custom_match = CustomMatch(
            match_id=match.match_stats.match_id,
            players=custom_players
        )
        try:
            session.add(custom_match)
            await session.commit()
            await session.refresh(custom_match)
            return custom_match
            
        except IntegrityError as e:
            raise e


async def get_match(match_id):
    async with Session(engine) as session:
        statement = select(CustomMatch).where(CustomMatch.match_id == match_id)
        results = await session.exec(statement)
        match = results.one()
    
        return match


async def update_match(match_id, value):
    async with Session(engine) as session:
        match = await get_match(match_id)
        match.is_valid = value
        session.add(match)
        await session.commit()
        await session.refresh(match)
        
        return match


async def add_channel(guild_id):
    channel = Channel(guild_id=guild_id)
    async with Session(engine) as session:
        try:
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            return channel
        
        except IntegrityError as e:
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
