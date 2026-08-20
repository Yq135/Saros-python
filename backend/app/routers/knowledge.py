"""模块四 API：手打知识 CRUD + 标签自动补全 + 知识查询（分页/筛选/语义检索）。

写入流程：先嵌入（本地 CPU，失败不产生脏数据），再单事务写知识+标签+向量。
查询能力已拆分为独立「知识查询」模块：分页列表接口 + 语义查询（小RAG，纯检索）。
"""
import psycopg
from fastapi import APIRouter, HTTPException, Query

from app import schemas
from app.db import get_conn, get_user_id
from app.embeddings import encode_query, encode_text
from app.vector_store import delete_embeddings, search, upsert_embedding

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


def _fetch_rows_by_ids(ids: list[int], user_id: int) -> dict[int, dict]:
    """按 id 批量查询笔记（含标签），返回 {id: row}；语义检索回查补全字段用。"""
    if not ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT mk.id, mk.content, mk.mastery_level, mk.created_at, mk.updated_at,
                   COALESCE(array_agg(t.name ORDER BY t.id) FILTER (WHERE t.name IS NOT NULL), '{}')
            FROM manual_knowledge mk
            LEFT JOIN tags t ON t.manual_knowledge_id = mk.id
            WHERE mk.id = ANY(%s) AND mk.user_id = %s
            GROUP BY mk.id
            """,
            (ids, user_id),
        )
        return {
            row[0]: {
                "id": row[0],
                "content": row[1],
                "mastery_level": row[2],
                "created_at": row[3],
                "updated_at": row[4],
                "tags": list(row[5]),
            }
            for row in cur.fetchall()
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


def _build_filters(q: str, tag: str, mastery: int | None, user_id: int) -> tuple[list[str], list]:
    """构造列表/COUNT 共用的 WHERE 片段与参数，避免两处条件漂移。"""
    where = ["mk.user_id = %s"]
    params: list = [user_id]
    if q:
        where.append("mk.content ILIKE %s")
        params.append(f"%{q}%")
    if tag:
        where.append("EXISTS (SELECT 1 FROM tags t2 WHERE t2.manual_knowledge_id = mk.id AND t2.name = %s)")
        params.append(tag)
    if mastery is not None:
        where.append("mk.mastery_level = %s")
        params.append(mastery)
    return where, params


@router.get("/knowledge", response_model=schemas.KnowledgeListOut)
def list_knowledge(
    q: str = "",
    tag: str = "",
    mastery: int | None = Query(default=None, ge=0, le=5, description="掌握度等值筛选 0-5"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """知识查询·分页列表：关键词（ILIKE）+ 标签（精确）+ 掌握度（等值）筛选，按更新时间倒序。"""
    user_id = get_user_id()
    where, params = _build_filters(q, tag, mastery, user_id)
    where_sql = " AND ".join(where)
    with get_conn() as conn, conn.cursor() as cur:
        # COUNT 不 JOIN tags：标签筛选用 EXISTS，不膨胀行数
        cur.execute(f"SELECT COUNT(*) FROM manual_knowledge mk WHERE {where_sql}", params)
        total = cur.fetchone()[0]
        # 列表沿用 LEFT JOIN tags + array_agg 形状；id 二级排序保证同时间戳分页稳定
        cur.execute(
            f"""
            SELECT mk.id, mk.content, mk.mastery_level, mk.created_at, mk.updated_at,
                   COALESCE(array_agg(t.name ORDER BY t.id) FILTER (WHERE t.name IS NOT NULL), '{{}}')
            FROM manual_knowledge mk
            LEFT JOIN tags t ON t.manual_knowledge_id = mk.id
            WHERE {where_sql}
            GROUP BY mk.id
            ORDER BY mk.updated_at DESC, mk.id DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, (page - 1) * page_size],
        )
        items = [
            schemas.KnowledgeOut(
                id=r[0], content=r[1], mastery_level=r[2],
                created_at=r[3], updated_at=r[4], tags=list(r[5]),
            )
            for r in cur.fetchall()
        ]
    return schemas.KnowledgeListOut(items=items, total=total, page=page, page_size=page_size)


@router.post("/knowledge/search", response_model=schemas.KnowledgeSearchOut)
def search_knowledge(payload: schemas.KnowledgeSearchRequest):
    """语义查询（小RAG）：BGE 编码 → pgvector KNN → 回查笔记全文；纯检索不生成。"""
    user_id = get_user_id()
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="查询内容不能为空")
    hits = search(vector=encode_query(query), user_id=user_id, top_k=payload.top_k)
    if not hits:
        return schemas.KnowledgeSearchOut(items=[])
    rows = _fetch_rows_by_ids([h["source_id"] for h in hits], user_id)
    items = [
        schemas.KnowledgeHit(**rows[h["source_id"]], similarity=h["similarity"])
        for h in hits
        if h["source_id"] in rows  # 容错：向量行指向已删笔记则跳过
    ]
    return schemas.KnowledgeSearchOut(items=items)


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
