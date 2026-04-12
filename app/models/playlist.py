"""SQLModel database models for Halo Infinite playlist wait time tracking."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class PlaylistWaitTimeRecord(SQLModel, table=True):
    """Historical wait time record for a single playlist."""

    id: Optional[int] = Field(default=None, primary_key=True)
    asset_id: str = Field(index=True)
    version_id: str
    playlist_name: Optional[str] = Field(default=None)
    wait_time_ms: int
    recorded_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class PlaylistInfo(SQLModel, table=True):
    """Cached metadata for a playlist (name resolved from the discovery API)."""

    asset_id: str = Field(primary_key=True)
    version_id: str
    playlist_name: Optional[str] = Field(default=None)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
