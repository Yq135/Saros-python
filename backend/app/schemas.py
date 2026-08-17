"""API 请求/响应模型（Pydantic v2）。"""
from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeCreate(BaseModel):
    content: str = Field(..., min_length=1, description="知识点内容（手打录入）")
    mastery_level: int = Field(0, ge=0, le=5, description="掌握程度 0-5")
    tags: list[str] = Field(default_factory=list, description="正式标签")


class KnowledgeUpdate(KnowledgeCreate):
    pass


class KnowledgeOut(BaseModel):
    id: int
    content: str
    mastery_level: int
    tags: list[str]
    created_at: datetime
    updated_at: datetime
