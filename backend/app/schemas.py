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


# ---------- 模块一：联网问答（多轮对话） ----------

class QAAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    conversation_id: int | None = Field(None, description="会话ID；不传则新建会话")


class SearchSourceOut(BaseModel):
    title: str
    url: str
    snippet: str = ""


class ReferencedKnowledgeOut(BaseModel):
    id: int
    content: str
    tags: list[str] = Field(default_factory=list)


class QAMessageOut(BaseModel):
    id: int
    question: str
    answer: str | None
    search_sources: list[SearchSourceOut] = Field(default_factory=list)
    referenced_knowledge: list[ReferencedKnowledgeOut] = Field(default_factory=list)
    suggested_tags: list[str] = Field(default_factory=list)
    created_at: datetime


class QAConversationOut(BaseModel):
    id: int
    title: str
    message_count: int
    created_at: datetime
    last_active: datetime | None


class QAConversationDetail(BaseModel):
    id: int
    title: str
    created_at: datetime
    messages: list[QAMessageOut]
