"""模块一问答服务：混合检索 + 多轮上下文 + 流式编排 + 入库。

POST /api/qa/ask 执行链（每轮独立执行）：
1. 校验/新建会话（新会话标题取首问截断）
2. 联网搜索（全挂降级）+ 沉淀混合检索（KNN 候选 → 加权打分 → 阈值过滤）
3. 追问轮加载最近 N 轮历史上下文 → 组装 Prompt → LLM 流式输出
4. 仅会话首轮生成推荐标签；答案+来源+引用沉淀 id 入库

对话内容只存 qa_messages 历史，绝不写入沉淀（沉淀入口仅模块四）。
"""
import json
import logging
from dataclasses import asdict
from typing import Iterable

import jieba

from app import llm, prompts
from app.config import settings
from app.db import get_conn, get_user_id
from app.embeddings import encode_query
from app.search import SearchResult, search_web
from app.vector_store import search as knn_search

logger = logging.getLogger("uvicorn.error")

# 混合打分权重与阈值（ROADMAP §4.3：0.6/0.3/0.15，阈值 0.35 取 top 5）
W_COSINE = 0.6
W_LEX = 0.3
W_TAG = 0.15
SCORE_THRESHOLD = 0.35
KNN_CANDIDATES = 50
KNOWLEDGE_TOP = 5

# 多轮上下文窗口：最近 N 轮、每轮答案截断字数（DeepSeek 64K 安全余量）
CONTEXT_MAX_ROUNDS = 6
ANSWER_TRUNCATE = 1000

TITLE_MAX_LEN = 30  # 会话标题：首问截断


class QAAbort(Exception):
    """业务性失败：向 SSE 流发送 error 事件（detail 为中文提示）。"""

    def __init__(self, detail: str):
        self.detail = detail


# ---------------------------------------------------------------
# 混合检索打分（纯函数，便于单测）
# ---------------------------------------------------------------

def question_tokens(question: str) -> set[str]:
    """jieba 分词，保留长度 >=2 的词（单字噪声大）。"""
    return {w for w in jieba.lcut(question) if len(w.strip()) >= 2}


def lex_overlap(tokens: set[str], text: str) -> float:
    """关键词重叠：问题词在文本中出现的比例（0-1）。"""
    if not tokens:
        return 0.0
    text_tokens = {w for w in jieba.lcut(text) if len(w.strip()) >= 2}
    return len(tokens & text_tokens) / len(tokens)


def tag_hit(tokens: set[str], tag_names: list[str]) -> float:
    """标签命中：问题词在笔记标签名中出现的比例（0-1）。"""
    if not tokens or not tag_names:
        return 0.0
    tag_tokens = {w for w in jieba.lcut(" ".join(tag_names)) if len(w.strip()) >= 2}
    return len(tokens & tag_tokens) / len(tokens)


def hybrid_score(*, similarity: float, tokens: set[str], content: str, tags: list[str]) -> float:
    """混合打分：0.6*cosine + 0.3*lex_overlap + 0.15*tag_hit。"""
    return (
        W_COSINE * similarity
        + W_LEX * lex_overlap(tokens, content)
        + W_TAG * tag_hit(tokens, tags)
    )


# ---------------------------------------------------------------
# 沉淀检索（仅手打笔记）
# ---------------------------------------------------------------

def retrieve_knowledge(question: str, user_id: int, top_n: int = KNOWLEDGE_TOP) -> list[dict]:
    """混合检索沉淀知识：KNN 候选 → 标签命中 + 关键词重叠加权打分 → 阈值过滤取 top N。

    返回 [{id, content, similarity, score, tags}]，按 score 降序；全低于阈值返回 []。
    """
    vector = encode_query(question)
    candidates = knn_search(vector=vector, user_id=user_id, top_k=KNN_CANDIDATES)
    if not candidates:
        return []
    tokens = question_tokens(question)
    ids = [c["source_id"] for c in candidates]
    content_map, tags_map = _load_note_meta(ids, user_id)
    scored: list[dict] = []
    for c in candidates:
        kid = c["source_id"]
        content = content_map.get(kid, c["chunk_content"])
        tags = tags_map.get(kid, [])
        score = hybrid_score(
            similarity=c["similarity"], tokens=tokens, content=content, tags=tags
        )
        if score >= SCORE_THRESHOLD:
            scored.append(
                {
                    "id": kid,
                    "content": content,
                    "similarity": round(c["similarity"], 4),
                    "score": round(score, 4),
                    "tags": tags,
                }
            )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def _load_note_meta(ids: list[int], user_id: int) -> tuple[dict[int, str], dict[int, list[str]]]:
    """候选笔记的正文与标签（笔记可能已被删，仅返回仍存在的）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, content FROM manual_knowledge WHERE user_id = %s AND id = ANY(%s)",
            (user_id, ids),
        )
        content_map = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            """
            SELECT t.manual_knowledge_id, t.name
            FROM tags t
            JOIN manual_knowledge mk ON mk.id = t.manual_knowledge_id
            WHERE mk.user_id = %s AND t.manual_knowledge_id = ANY(%s)
            ORDER BY t.id
            """,
            (user_id, ids),
        )
        tags_map: dict[int, list[str]] = {}
        for kid, name in cur.fetchall():
            tags_map.setdefault(kid, []).append(name)
    return content_map, tags_map


# ---------------------------------------------------------------
# 会话与轮次
# ---------------------------------------------------------------

def ensure_conversation(
    question: str, conversation_id: int | None, user_id: int
) -> tuple[int, bool]:
    """返回 (conversation_id, is_new)。新会话即建（标题=首问截断）；追问校验会话存在。"""
    if conversation_id is None:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO qa_conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                (user_id, question[:TITLE_MAX_LEN]),
            )
            return cur.fetchone()[0], True
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM qa_conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        if cur.fetchone() is None:
            raise QAAbort("会话不存在或已删除")
    return conversation_id, False


def delete_conversation_if_empty(conversation_id: int, user_id: int) -> None:
    """清理：无任何消息的会话直接删除（流式失败/客户端中断时留下的空会话）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM qa_conversations
            WHERE id = %s AND user_id = %s
              AND NOT EXISTS (SELECT 1 FROM qa_messages WHERE conversation_id = %s)
            """,
            (conversation_id, user_id, conversation_id),
        )


def load_history(conversation_id: int, user_id: int) -> str:
    """会话最近 N 轮对话文本（问题全文 + 答案截断），时间正序。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT question, COALESCE(answer, '') FROM (
                SELECT question, answer, created_at
                FROM qa_messages
                WHERE conversation_id = %s AND user_id = %s
                ORDER BY id DESC
                LIMIT %s
            ) recent
            ORDER BY created_at
            """,
            (conversation_id, user_id, CONTEXT_MAX_ROUNDS),
        )
        rows = cur.fetchall()
    lines: list[str] = []
    for q, a in rows:
        lines.append(f"用户：{q}")
        lines.append(f"助手：{a[:ANSWER_TRUNCATE]}")
    return "\n".join(lines)


def save_message(
    *,
    conversation_id: int,
    user_id: int,
    question: str,
    answer: str,
    sources: list[dict],
    knowledge_ids: list[int],
    tags: list[str],
) -> int:
    """答案完整生成后入库（jsonb 用 Jsonb 适配），返回消息 id。"""
    from psycopg.types.json import Jsonb

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO qa_messages
                (conversation_id, user_id, question, answer, search_sources,
                 referenced_knowledge_ids, suggested_tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                conversation_id,
                user_id,
                question,
                answer,
                Jsonb(sources),
                knowledge_ids or None,
                tags or None,
            ),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------
# 推荐标签（仅会话首轮）
# ---------------------------------------------------------------

def generate_tags(question: str, answer: str) -> list[str]:
    """轻量 LLM 调用生成 3-5 个中文推荐标签；失败返回空列表（不阻断回答）。"""
    try:
        text = llm.chat(prompts.build_tag_messages(question=question, answer=answer[:2000]))
        return prompts.parse_tags(text)
    except Exception as e:  # noqa: BLE001 — 标签失败不影响主流程
        logger.warning("推荐标签生成失败: %s", e)
        return []


# ---------------------------------------------------------------
# SSE 流编排
# ---------------------------------------------------------------

def answer_stream(question: str, conversation_id: int | None) -> Iterable[str]:
    """POST /api/qa/ask 的 SSE 流生成器（同步，线程池执行）。

    事件：start（来源+沉淀引用+会话）→ delta*（答案增量）→ done（完整答案+标签）
    失败：error（detail 中文提示；新会话若未留下任何消息则自动清理）。
    """
    user_id = get_user_id()
    cid: int | None = None
    is_new = False
    try:
        # 1. 会话：新会话即建（失败自动清理）；追问校验
        cid, is_new = ensure_conversation(question, conversation_id, user_id)

        # 2. 联网搜索（FR-1.7：全挂降级，仅影响回答，不阻断）
        raw_sources: list[SearchResult] = []
        try:
            raw_sources = search_web(question)
        except Exception as e:  # noqa: BLE001 — 编排层兜底，保证任何搜索异常都不中断
            logger.warning("联网搜索失败: %s", e)
        sources = [asdict(s) for s in raw_sources]

        # 3. 沉淀混合检索
        knowledge = retrieve_knowledge(question, user_id)
        if not sources and not knowledge:
            raise QAAbort("联网搜索与沉淀知识均不可用，请稍后重试")

        # 4. 追问上下文 + Prompt
        history = "" if is_new else load_history(cid, user_id)
        messages = prompts.build_answer_messages(
            question=question, sources=sources, knowledge=knowledge, history=history
        )

        yield _sse(
            "start",
            {
                "conversation_id": cid,
                "is_new": is_new,
                "sources": sources,
                "knowledge": [
                    {
                        "id": k["id"],
                        "content": k["content"],
                        "similarity": k["similarity"],
                        "tags": k["tags"],
                    }
                    for k in knowledge
                ],
            },
        )

        # 5. 流式答案
        answer_parts: list[str] = []
        for delta in llm.stream_chat(messages):
            answer_parts.append(delta)
            yield _sse("delta", {"text": delta})
        answer = "".join(answer_parts)
        if not answer.strip():
            raise QAAbort("模型未返回内容，请重试")

        # 6. 推荐标签（仅首轮）
        tags = generate_tags(question, answer) if is_new else []

        # 7. 入库
        message_id = save_message(
            conversation_id=cid,
            user_id=user_id,
            question=question,
            answer=answer,
            sources=sources,
            knowledge_ids=[k["id"] for k in knowledge],
            tags=tags,
        )
        yield _sse(
            "done",
            {
                "id": message_id,
                "conversation_id": cid,
                "answer": answer,
                "suggested_tags": tags,
            },
        )
    except QAAbort as e:
        yield _sse("error", {"detail": e.detail})
    except Exception as e:  # noqa: BLE001 — 兜底：任何异常都以 error 事件返回
        logger.exception("问答流异常")
        yield _sse("error", {"detail": f"服务异常：{e}"})
    finally:
        # 空会话清理：覆盖失败与客户端中断（停止生成时 GeneratorExit 也会经过 finally）
        if is_new and cid is not None:
            delete_conversation_if_empty(cid, user_id)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
