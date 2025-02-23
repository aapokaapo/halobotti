from email.policy import default
from typing import List, Optional
import uuid

from sqlmodel import Field, Relationship, SQLModel


class LinkTable(SQLModel, table=True):
    custom_match_id: int|None = Field(default=None, foreign_key="custommatch.id", primary_key=True)
    custom_player_id: int|None = Field(default=None, foreign_key="customplayer.id", primary_key=True)



class CustomMatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    match_id: uuid.UUID = Field(unique=True, default_factory=uuid.uuid4)
    players: List["CustomPlayer"] = Relationship(back_populates="custom_matches", link_model=LinkTable, sa_relationship_kwargs={"lazy": "joined"},)
    is_valid: bool = False


class CustomPlayer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    gamertag: str
    xuid: str = Field(unique=True)
    custom_matches: List[CustomMatch] = Relationship(back_populates="players", link_model=LinkTable, sa_relationship_kwargs={"lazy": "joined"},)
    is_valid: bool = False
    validation_message: bool = False


class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    guild_id: int = Field(unique=True)
    log_channel_id: Optional[int] = Field(default=None)
    leaderboard_channel_id: Optional[int] = Field(default=None)
    
