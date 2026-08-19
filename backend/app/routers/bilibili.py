"""模块三 API：B 站视频任务。

POST   /api/bilibili/tasks          提交任务（同 bvid 重复提交 409 + existing_id）
GET    /api/bilibili/tasks          任务列表（含视频标题/模式，前端轮询进度）
GET    /api/bilibili/tasks/{tid}    任务详情（大纲/题目/字幕段/媒体相对路径）
POST   /api/bilibili/tasks/{tid}/retry   失败重试（媒体文件跳过重下）
DELETE /api/bilibili/tasks/{tid}    删除（清理媒体文件，DB 级联删视频知识）

媒体文件经 /api/media/{bvid}/{filename} 静态服务访问（main.py 挂载，支持 Range 拖动）。
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import schemas
from app.db import get_conn, get_user_id
from app.services import task_queue, video_download, video_task

router = APIRouter(prefix="/api", tags=["bilibili"])

MEDIA_PREFIX = "/api/media/"


@router.post("/bilibili/tasks", status_code=201, response_model=schemas.BilibiliTaskListItem)
def create(payload: schemas.BilibiliTaskCreate):
    """提交任务：校验链接 → 建任务行 + 视频行 → 入队；同 bvid 已存在返回 409 + existing_id。"""
    url = payload.url.strip()
    try:
        bvid = video_download.parse_bvid(url)
    except video_download.VideoDownloadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM bilibili_tasks WHERE bvid = %s AND user_id = %s",
            (bvid, user_id),
        )
        row = cur.fetchone()
        if row is not None:
            return JSONResponse(
                status_code=409,
                content={"detail": "该视频已提交过任务", "existing_id": row[0]},
            )
        cur.execute(
            """
            INSERT INTO bilibili_tasks (user_id, bvid, url, status, step_desc)
            VALUES (%s, %s, %s, 'PENDING', '等待执行')
            RETURNING id, status, progress, step_desc, error_msg, created_at
            """,
            (user_id, bvid, url),
        )
        r = cur.fetchone()
        task_id = r[0]
        cur.execute(
            "INSERT INTO bilibili_videos (task_id, user_id, bvid) VALUES (%s, %s, %s)",
            (task_id, user_id, bvid),
        )
    task_queue.enqueue(task_id)
    return schemas.BilibiliTaskListItem(
        id=task_id,
        bvid=bvid,
        title="",
        mode=None,
        status=r[1],
        progress=r[2],
        step_desc=r[3],
        error_msg=r[4],
        created_at=r[5],
    )


@router.get("/bilibili/tasks", response_model=list[schemas.BilibiliTaskListItem])
def list_tasks():
    """任务列表：按提交时间倒序（前端轮询展示进度）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.bvid, t.status, t.progress, t.step_desc, t.error_msg, t.created_at,
                   COALESCE(v.title, ''), v.mode
            FROM bilibili_tasks t
            LEFT JOIN bilibili_videos v ON v.task_id = t.id
            WHERE t.user_id = %s
            ORDER BY t.id DESC
            LIMIT 100
            """,
            (user_id,),
        )
        return [
            schemas.BilibiliTaskListItem(
                id=r[0],
                bvid=r[1],
                status=r[2],
                progress=r[3],
                step_desc=r[4],
                error_msg=r[5],
                created_at=r[6],
                title=r[7],
                mode=r[8],
            )
            for r in cur.fetchall()
        ]


@router.get("/bilibili/tasks/{tid}", response_model=schemas.BilibiliTaskDetail)
def get_task(tid: int):
    """任务详情：任务状态 + 视频知识（大纲/题目/字幕段）+ 媒体相对路径（前端拼 /api/media/）。"""
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.bvid, t.url, t.status, t.progress, t.step_desc, t.error_msg, t.created_at,
                   COALESCE(v.title, ''), v.mode, v.outline,
                   v.local_subtitle_path, v.local_audio_path, v.local_video_path,
                   COALESCE(v.suggested_tags, '{{}}'), v.id AS video_id
            FROM bilibili_tasks t
            LEFT JOIN bilibili_videos v ON v.task_id = t.id
            WHERE t.id = %s AND t.user_id = %s
            """,
            (tid, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        video_id = row[15]
        # 在线播放器 page 参数：从原始链接还原分集号
        _, p = video_download.parse_video_ref(
            (row[2] or "") or f"https://www.bilibili.com/video/{row[1]}"
        )
        outline_raw = row[10] or "[]"
        try:
            outline = json.loads(outline_raw)
        except (json.JSONDecodeError, TypeError):
            outline = []
        segments, questions = [], []
        if video_id is not None:
            cur.execute(
                """
                SELECT id, start_ts, end_ts, content
                FROM video_segments WHERE video_id = %s ORDER BY start_ts, id
                """,
                (video_id,),
            )
            segments = [
                schemas.VideoSegmentOut(id=r2[0], start_ts=r2[1], end_ts=r2[2], content=r2[3])
                for r2 in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT id, question, COALESCE(reference_answer, ''), ts, sort_order
                FROM video_questions WHERE video_id = %s ORDER BY sort_order, id
                """,
                (video_id,),
            )
            questions = [
                schemas.VideoQuestionOut(
                    id=r2[0], question=r2[1], reference_answer=r2[2], ts=r2[3], sort_order=r2[4]
                )
                for r2 in cur.fetchall()
            ]

    def media_url(rel: str | None) -> str | None:
        return MEDIA_PREFIX + rel if rel else None

    return schemas.BilibiliTaskDetail(
        id=row[0],
        bvid=row[1],
        p=p,
        status=row[3],
        progress=row[4],
        step_desc=row[5],
        error_msg=row[6],
        created_at=row[7],
        title=row[8],
        mode=row[9],
        outline=[schemas.VideoOutlineItem(**o) for o in outline if isinstance(o, dict)],
        suggested_tags=list(row[14] or []),
        video_url=media_url(row[13]),
        audio_url=media_url(row[12]),
        subtitle_url=media_url(row[11]),
        segments=segments,
        questions=questions,
    )


@router.post("/bilibili/tasks/{tid}/retry", response_model=schemas.BilibiliTaskListItem)
def retry(tid: int):
    """失败任务重试：媒体文件跳过重下（断点续跑），重新解析/大纲/出题。"""
    try:
        video_task.retry_task(tid)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    user_id = get_user_id()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.bvid, t.status, t.progress, t.step_desc, t.error_msg, t.created_at,
                   COALESCE(v.title, ''), v.mode
            FROM bilibili_tasks t
            LEFT JOIN bilibili_videos v ON v.task_id = t.id
            WHERE t.id = %s AND t.user_id = %s
            """,
            (tid, user_id),
        )
        r = cur.fetchone()
        assert r is not None
        return schemas.BilibiliTaskListItem(
            id=r[0], bvid=r[1], status=r[2], progress=r[3], step_desc=r[4],
            error_msg=r[5], created_at=r[6], title=r[7], mode=r[8],
        )


@router.delete("/bilibili/tasks/{tid}", status_code=204)
def remove(tid: int):
    """删除任务：清理媒体文件 + DB 级联删除视频知识（大纲/题目/字幕段）。"""
    try:
        video_task.delete_task(tid)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
