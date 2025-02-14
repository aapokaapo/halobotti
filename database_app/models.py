from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class CustomMatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    match_id: str
    is_valid: bool
