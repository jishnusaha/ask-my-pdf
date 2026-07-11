from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class PageData(BaseModel):
    # Reject any extra arguments at runtime
    model_config = ConfigDict(extra="forbid")

    page_number: int
    text: str


class ChunkData(BaseModel):
    # Reject any extra arguments at runtime
    model_config = ConfigDict(extra="forbid")

    page_number: int
    chunk_index: int
    text: str
    content_hash: str = ""
    embedding: Optional[List[float]] = None
