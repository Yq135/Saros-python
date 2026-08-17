"""模块四 API：手打知识 CRUD + 标签自动补全。

写入流程：先嵌入（本地 CPU，失败不产生脏数据），再单事务写知识+标签+向量。
"""
import psycopg
from fastapi import APIRouter, HTTPException

from app import schemas
from app.db import get_conn, get_user_id
from app.embeddings import encode_text
from app.vector_store import delete_embeddings, upsert_embedding

router = APIRouter(prefix="/api", tags=["knowledge"])

MAX_TAG_LEN = 100  # 与 tags.name VARCHAR(100) 一致


def _clean_tags(tags: list[str]) -> list[str]:
    """标签清洗：去空白、去重、截断，保持输入顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        t = (t or "").strip()[:MAX_TAG_LEN]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _fetch_row(kid: int, user_id: int) -> dict | None:
    """查询单条笔记（含标签），不存在返回 None。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT mk.id, mk.content, mk.mastery_level, mk.created_at, mk.updated_at,
                   COALESCE(array_agg(t.name ORDER BY t.id) FILTER (WHERE t.name IS NOT NULL), '{}')
            FROM manual_knowledge mk
            LEFT JOIN tags t ON t.manual_knowledge_id = mk.id
            WHERE mk.id = %s AND mk.user_id = %s
            GROUP BY mk.id
            """,
            (kid, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "content": row[1],
            "mastery_level": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "tags": list(row[5]),
        }


def _replace_tags(conn: psycopg.Connection, kid: int, tags: list[str]) -> None:
    """全量替换笔记标签（编辑时先删后插）。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tags WHERE manual_knowledge_id = %s", (kid,))
        for t in tags:
            cur.execute(
                "INSERT INTO tags (manual_knowledge_id, name) VALUES (%s, %s)", (kid, t)
            )


@router.post("/knowledge", response_model=schemas.KnowledgeOut, status_code=201)
def create_knowledge(payload: schemas.KnowledgeCreate):
    user_id = get_user_id()
    tags = _clean_tags(payload.tags)
    vector = encode_text(payload.content)  # 先嵌入，失败则不写任何数据
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manual_knowledge (user_id, content, mastery_level) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user_id, payload.content, payload.mastery_level),
            )
            kid = cur.fetchone()[0]
        _replace_tags(conn, kid, tags)
        upsert_embedding(conn, user_id=user_id, source_id=kid, content=payload.content, vector=vector)
    return _fetch_row(kid, user_id)


@router.get("/knowledge", response_model=list[schemas.KnowledgeOut])
def list_knowledge(q: str = "", tag: str = ""):
    """列表：关键词（ILIKE 内容）+ 标签（精确）筛选，按更新时间倒序。"""
    user_id = get_user_id()
    where = ["mk.user_id = %s"]
    params: list = [user_id]
    if q:
        where.append("mk.content ILIKE %s")
        params.append(f"%{q}%")
    if tag:
        where.append("EXISTS (SELECT 1 FROM tags t2 WHERE t2.manual_knowledge_id = mk.id AND t2.name = %s)")
        params.append(tag)
    sql = f"""
        SELECT mk.id, mk.content, mk.mastery_level, mk.created_at, mk.updated_at,
               COALESCE(array_agg(t.name ORDER BY t.id) FILTER (WHERE t.name IS NOT NULL), '{{}}')
        FROM manual_knowledge mk
        LEFT JOIN tags t ON t.manual_knowledge_id = mk.id
        WHERE {' AND '.join(where)}
        GROUP BY mk.id
        ORDER BY mk.updated_at DESC
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [
            schemas.KnowledgeOut(
                id=r[0], content=r[1], mastery_level=r[2],
                created_at=r[3], updated_at=r[4], tags=list(r[5]),
            )
            for r in cur.fetchall()
        ]


@router.get("/knowledge/{kid}", response_model=schemas.KnowledgeOut)
def get_knowledge(kid: int):
    row = _fetch_row(kid, get_user_id())
    if row is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    return row


@router.put("/knowledge/{kid}", response_model=schemas.KnowledgeOut)
def update_knowledge(kid: int, payload: schemas.KnowledgeUpdate):
    user_id = get_user_id()
    tags = _clean_tags(payload.tags)
    vector = encode_text(payload.content)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE manual_knowledge SET content = %s, mastery_level = %s "
                "WHERE id = %s AND user_id = %s RETURNING id",
                (payload.content, payload.mastery_level, kid, user_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="知识点不存在")
        _replace_tags(conn, kid, tags)
        upsert_embedding(conn, user_id=user_id, source_id=kid, content=payload.content, vector=vector)
    return _fetch_row(kid, user_id)


@router.delete("/knowledge/{kid}", status_code=204)
def delete_knowledge(kid: int):
    user_id = get_user_id()
    with get_conn() as conn:
        delete_embeddings(conn, user_id=user_id, source_id=kid)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM manual_knowledge WHERE id = %s AND user_id = %s",
                (kid, user_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="知识点不存在")


@router.get("/tags", response_model=list[str])
def suggest_tags(q: str = ""):
    """标签自动补全：手打笔记标签库去重，前缀/包含匹配，最多 20 个。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT t.name
            FROM tags t
            JOIN manual_knowledge mk ON mk.id = t.manual_knowledge_id
            WHERE mk.user_id = %s AND t.name ILIKE %s
            ORDER BY t.name
            LIMIT 20
            """,
            (user_id, f"%{q}%"),
        )
        return [r[0] for r in cur.fetchall()]
