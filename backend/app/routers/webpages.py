"""模块二 API：网页出题 + 文章管理。

POST /api/webpages 为 SSE 流（step → done，失败 error；URL 重复返回 409 + existing_id）。
POST /api/webpages/{aid}/regenerate 为 SSE 流（仅重新出题）。
其余为普通 JSON：列表（关键词筛选 + 正文截断预览）、详情（全文 + 题目）、删除（级联删题）。
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app import schemas
from app.db import get_conn, get_user_id
from app.services import webpage_service

router = APIRouter(prefix="/api", tags=["webpages"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 禁用代理缓冲，保证流式即时到达
}


@router.post("/webpages")
def create(payload: schemas.WebpageCreateRequest):
    """提交 URL：抽取正文 → 生成题目 → 入库（SSE 流）。

    事件：step（正在提取正文/正在生成题目）→ done（文章+题目+标签）
    失败：error（detail 中文提示）；URL 已收录：409 + existing_id（前端跳转已有条目）
    """
    url = payload.url.strip()
    user_id = get_user_id()
    existing_id = webpage_service.find_existing(url, user_id)
    if existing_id is not None:
        return JSONResponse(
            status_code=409,
            content={"detail": "该网页已收录过", "existing_id": existing_id},
        )
    return StreamingResponse(
        webpage_service.create_stream(url),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/webpages", response_model=list[schemas.WebArticleListItem])
def list_articles(q: str = Query("", description="关键词（匹配标题/URL）")):
    """文章列表：关键词筛选，按收录时间倒序；正文仅返回截断预览。"""
    user_id = get_user_id()
    where = ""
    params: list = [user_id]
    if q.strip():
        where = " AND (COALESCE(title, '') ILIKE %s OR url ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    sql = f"""
        SELECT a.id, a.url, COALESCE(a.title, ''), LEFT(a.content, 300),
               COALESCE(a.suggested_tags, '{{}}'),
               (SELECT COUNT(*) FROM webpage_questions q WHERE q.article_id = a.id),
               a.created_at
        FROM web_articles a
        WHERE a.user_id = %s{where}
        ORDER BY a.id DESC
        LIMIT 100
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [
            schemas.WebArticleListItem(
                id=r[0],
                url=r[1],
                title=r[2],
                content_preview=r[3],
                suggested_tags=list(r[4] or []),
                question_count=r[5],
                created_at=r[6],
            )
            for r in cur.fetchall()
        ]


@router.get("/webpages/{aid}", response_model=schemas.WebArticleDetail)
def get_article(aid: int):
    """文章详情：全文 + 题目（按 sort_order）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, COALESCE(title, ''), content,
                   COALESCE(suggested_tags, '{{}}'), created_at
            FROM web_articles
            WHERE id = %s AND user_id = %s
            """,
            (aid, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="文章不存在")
        cur.execute(
            """
            SELECT id, question, COALESCE(reference_answer, '')
            FROM webpage_questions
            WHERE article_id = %s
            ORDER BY sort_order, id
            """,
            (aid,),
        )
        questions = [
            schemas.WebpageQuestionOut(id=r2[0], question=r2[1], reference_answer=r2[2])
            for r2 in cur.fetchall()
        ]
    return schemas.WebArticleDetail(
        id=row[0],
        url=row[1],
        title=row[2],
        content=row[3],
        suggested_tags=list(row[4] or []),
        questions=questions,
        created_at=row[5],
    )


@router.delete("/webpages/{aid}", status_code=204)
def delete_article(aid: int):
    """删除文章（级联删除全部题目）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM web_articles WHERE id = %s AND user_id = %s", (aid, user_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="文章不存在")


@router.post("/webpages/{aid}/regenerate")
def regenerate(aid: int):
    """重新生成题目（SSE 流）：覆盖旧题目与标签，不动正文。

    事件：step → done；失败：error（detail 中文提示）
    """
    return StreamingResponse(
        webpage_service.regenerate_stream(aid),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
