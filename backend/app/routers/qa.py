"""模块一 API：联网问答（多轮对话）+ 会话历史。

POST /api/qa/ask 为 SSE 流（start → delta* → done，失败 error），其余为普通 JSON。
会话列表仅展示有消息的会话（空会话为失败残留，已由服务层清理，此处再兜底）。
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app import schemas
from app.db import get_conn, get_user_id
from app.services import qa_service

router = APIRouter(prefix="/api", tags=["qa"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 禁用代理缓冲，保证流式即时到达
}


@router.post("/qa/ask")
def ask(payload: schemas.QAAskRequest):
    """提问/追问（SSE 流）。

    事件：start（来源+引用的沉淀+conversation_id）→ delta（答案增量）→ done（完整答案+推荐标签）
    失败：error（detail 中文提示）
    """
    return StreamingResponse(
        qa_service.answer_stream(payload.question.strip(), payload.conversation_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/qa/conversations", response_model=list[schemas.QAConversationOut])
def list_conversations(q: str = Query("", description="关键词（匹配标题/问题/答案）")):
    """会话列表：关键词筛选，按最近活跃倒序。"""
    user_id = get_user_id()
    where = ""
    params: list = [user_id]
    if q.strip():
        where = """
            AND (c.title ILIKE %s
                 OR EXISTS (SELECT 1 FROM qa_messages m2
                            WHERE m2.conversation_id = c.id
                              AND (m2.question ILIKE %s OR m2.answer ILIKE %s)))
        """
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql = f"""
        SELECT c.id, c.title, COUNT(m.id), c.created_at, MAX(m.created_at)
        FROM qa_conversations c
        JOIN qa_messages m ON m.conversation_id = c.id
        WHERE c.user_id = %s{where}
        GROUP BY c.id
        ORDER BY MAX(m.created_at) DESC
        LIMIT 100
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [
            schemas.QAConversationOut(
                id=r[0], title=r[1], message_count=r[2], created_at=r[3], last_active=r[4]
            )
            for r in cur.fetchall()
        ]


@router.get("/qa/conversations/{cid}", response_model=schemas.QAConversationDetail)
def get_conversation(cid: int):
    """会话详情：多轮消息按时间顺序；引用沉淀补全正文（笔记已删的跳过）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, created_at FROM qa_conversations WHERE id = %s AND user_id = %s",
            (cid, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        cur.execute(
            """
            SELECT id, question, COALESCE(answer, ''),
                   COALESCE(search_sources, '[]'::jsonb),
                   COALESCE(referenced_knowledge_ids, '{}'),
                   COALESCE(suggested_tags, '{}'),
                   created_at
            FROM qa_messages
            WHERE conversation_id = %s
            ORDER BY id
            """,
            (cid,),
        )
        messages = [
            {
                "id": r[0],
                "question": r[1],
                "answer": r[2],
                "search_sources": r[3] if isinstance(r[3], list) else [],
                "referenced_knowledge_ids": [int(x) for x in (r[4] or [])],
                "suggested_tags": list(r[5] or []),
                "created_at": r[6],
            }
            for r in cur.fetchall()
        ]
        # 引用沉淀补正文（笔记可能已被删，仅返回仍存在的）
        all_ids = {k for m in messages for k in m["referenced_knowledge_ids"]}
        content_map: dict[int, str] = {}
        if all_ids:
            cur.execute(
                "SELECT id, content FROM manual_knowledge WHERE user_id = %s AND id = ANY(%s)",
                (user_id, list(all_ids)),
            )
            content_map = {r2[0]: r2[1] for r2 in cur.fetchall()}
    return schemas.QAConversationDetail(
        id=row[0],
        title=row[1],
        created_at=row[2],
        messages=[
            schemas.QAMessageOut(
                id=m["id"],
                question=m["question"],
                answer=m["answer"],
                search_sources=m["search_sources"],
                referenced_knowledge=[
                    schemas.ReferencedKnowledgeOut(id=k, content=content_map[k])
                    for k in m["referenced_knowledge_ids"]
                    if k in content_map
                ],
                suggested_tags=m["suggested_tags"],
                created_at=m["created_at"],
            )
            for m in messages
        ],
    )


@router.delete("/qa/conversations/{cid}", status_code=204)
def delete_conversation(cid: int):
    """删除会话（级联删除全部轮次）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM qa_conversations WHERE id = %s AND user_id = %s", (cid, user_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="会话不存在")
