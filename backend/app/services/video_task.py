"""模块三任务编排：B 站视频「下载字幕（无则音频）→ 文本段 → 大纲 → 出题 → 入库」。

由任务队列（task_queue.py）单 worker 串行调用 run_task(task_id)；
yt-dlp / ffmpeg / LLM 均为同步阻塞调用，worker 侧用 asyncio.to_thread 执行。

流程决议（v0.10）：
- 不下载视频；先下字幕（CC→AI）；无字幕走音频模式：下载音频流 → 约 5 分钟切片
  → 自建 ASR（mlx-qwen3-asr）转写带时间戳文本 → 与字幕模式共用大纲/出题流程。
步骤与进度（%）：解析链接 2 → 下载字幕 5-30 / 音频模式 5-45+切片转写 45-80
→ 解析字幕 40 → 大纲 80/90 → 出题 90/95 → 入库 100。
断点续跑：字幕/音频文件已存在时跳过下载；LLM 阶段重试后重新生成。
"""
import json
import logging
import shutil
from pathlib import Path

from app import llm, prompts
from app.config import settings
from app.db import get_conn
from app.services import asr, subtitle_parser, video_download

logger = logging.getLogger("uvicorn.error")

SUBTITLE_MAX_CHARS = 20000  # 字幕超长截断（「全文一次喂」的极端保护，约 2 万字）
# 推理模型（deepseek-v4）的 reasoning 也计入 max_tokens：实测十分钟视频大纲的思考
# 占约 2600+ token（且每次随机增长），8192 仍偶发 finish_reason=length 截断 JSON，
# 预算给足（用户拍板 16384），配合解析失败自动重试兜底
OUTLINE_MAX_TOKENS = 16384  # 大纲 JSON 输出上限
QUESTION_MAX_TOKENS = 16384  # 出题 JSON 输出上限
TAG_MAX_TOKENS = 1024  # 标签输出预算（256 时思考耗完导致空输出）

STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


class VideoTaskError(Exception):
    """任务失败：detail 为中文提示，原样写入 error_msg。"""


# ---------------------------------------------------------------
# 任务状态更新
# ---------------------------------------------------------------

def _update(
    task_id: int,
    *,
    progress: int | None = None,
    step: str | None = None,
    status: str | None = None,
    error: str | None = None,
) -> None:
    sets, params = [], []
    if progress is not None:
        sets.append("progress = %s")
        params.append(progress)
    if step is not None:
        sets.append("step_desc = %s")
        params.append(step)
    if status is not None:
        sets.append("status = %s")
        params.append(status)
    if error is not None:
        sets.append("error_msg = %s")
        params.append(error)
    params.append(task_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE bilibili_tasks SET {', '.join(sets)} WHERE id = %s", params)


def _download_progress(task_id: int, lo: int, hi: int):
    """yt-dlp 进度回调：把单文件下载进度映射到 [lo, hi] 区间（仅上报更大的进度）。"""
    state = {"last": lo}

    def hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        if not total:
            return
        pct = round(lo + (hi - lo) * done / total)
        if pct > state["last"]:
            state["last"] = pct
            _update(task_id, progress=pct)

    return hook


def _get_task(task_id: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT bvid, url FROM bilibili_tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise VideoTaskError(f"任务 {task_id} 不存在")
        return {"bvid": row[0], "url": row[1] or f"https://www.bilibili.com/video/{row[0]}"}


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------

def run_task(task_id: int) -> None:
    """执行任务主流程（同步，worker 线程内运行）；任何异常 → FAILED。"""
    try:
        _run(task_id)
    except VideoTaskError as e:
        _update(task_id, status=STATUS_FAILED, error=str(e))
        logger.warning("任务 %s 失败：%s", task_id, e)
    except Exception as e:  # noqa: BLE001 — 未知异常兜底，任务必须落到终态
        logger.exception("任务 %s 内部异常", task_id)
        _update(task_id, status=STATUS_FAILED, error=f"内部错误：{e}")


def _run(task_id: int) -> None:
    task = _get_task(task_id)
    url = task["url"]
    _update(task_id, status=STATUS_PROCESSING, progress=2, step="解析链接")
    bvid, p = video_download.parse_video_ref(url)

    # 1. 下载字幕（CC → AI）；无字幕或开启「跳过字幕」开关 → 音频模式（下载音频 → ASR 转写）
    audio_path = None
    if settings.skip_subtitle:
        # 跳过字幕：ASR 转写带断句标点，大纲/出题质量更高（更耗时），用户可配
        _update(task_id, step="已开启「跳过字幕」开关，直接音频模式")
        _, _, info = video_download.fetch_video_info(url)
        title = (info or {}).get("title") or ""
        segments, audio_path = _audio_mode(task_id, url)
        mode = "AUDIO"
        subtitle_path = None
        _update(task_id, progress=80)
    else:
        _update(task_id, step="下载字幕（官方 CC / AI 字幕）")
        _, _, title, mode, subtitle_path = video_download.download_subtitle(url)
        if subtitle_path is None:
            segments, audio_path = _audio_mode(task_id, url)
            mode = "AUDIO"
            _update(task_id, progress=80)
        else:
            _update(task_id, progress=30, step="解析字幕")
            segments = subtitle_parser.parse_subtitle(subtitle_path)
            if not segments:
                raise VideoTaskError("字幕解析失败：字幕文件内容为空或格式异常，可重试")
            _update(task_id, progress=40)

    # 2. 大纲（字幕/ASR 转写文本全文一次喂）
    _update(task_id, step="生成大纲（LLM 提炼）")
    outline = _generate_outline(title, segments)
    _update(task_id, progress=90)

    # 3. 出题（基于字幕/转写全文）+ 推荐标签（基于大纲）
    _update(task_id, step="生成题目与推荐标签（LLM）")
    questions, tags = _generate_questions(title, segments, outline)
    _update(task_id, progress=95)

    # 4. 汇总入库
    _update(task_id, step="写入知识库")
    _save_video(
        task_id, bvid, title, mode, subtitle_path, audio_path, None,
        outline, segments, questions, tags,
    )
    _update(task_id, status=STATUS_SUCCESS, progress=100, step="完成")
    logger.info("任务 %s 完成（%s 模式，%d 小节，%d 题）", task_id, mode, len(outline), len(questions))


def _audio_mode(task_id: int, url: str) -> tuple[list[dict], Path]:
    """音频模式：下载音频流 → ffmpeg 约 5 分钟切片 → ASR 逐片转写 → 带时间戳文本段。

    返回 (segments, audio_path)。ASR 输出带精确时间戳，后续与字幕模式共用流程。
    """
    _update(task_id, step="下载音频（audio-only 流）")
    audio_path = video_download.download_audio(url, _download_progress(task_id, 5, 45))
    _update(task_id, progress=45, step="音频切片并语音转写")
    segments = asr.transcribe_audio(
        audio_path,
        audio_path.parent / f"{audio_path.stem}_segs",
        progress_cb=lambda done, total: _update(
            task_id,
            step=f"语音转写（{done}/{total} 片）",
            progress=45 + round(35 * done / total),
        ),
    )
    if not segments:
        raise VideoTaskError("语音转写失败：转写结果为空，请重试")
    return segments, audio_path


# ---------------------------------------------------------------
# LLM 阶段
# ---------------------------------------------------------------

def _fmt_ts(sec: float) -> str:
    """秒 → [MM:SS]（超一小时分钟可超过 60，保持 MM:SS 格式）。"""
    total = int(round(sec))
    return f"{total // 60:02d}:{total % 60:02d}"


def _segments_to_text(segments: list[dict]) -> str:
    lines = [f"[{_fmt_ts(s['start_sec'])}] {s['text']}" for s in segments]
    text = "\n".join(lines)
    if len(text) > SUBTITLE_MAX_CHARS:
        text = text[:SUBTITLE_MAX_CHARS] + "\n…（字幕过长已截断，剩余部分不参与大纲生成）"
    return text


def _generate_outline(title: str, segments: list[dict]) -> list[dict]:
    """大纲生成：解析失败自动重试一次（推理模型思考量随机，偶发超预算截断 JSON）。"""
    messages = prompts.build_video_outline_messages(
        title=title, subtitle_text=_segments_to_text(segments)
    )
    for _ in range(2):
        raw = llm.chat(messages, temperature=0.3, max_tokens=OUTLINE_MAX_TOKENS, timeout=300.0)
        outline = prompts.parse_video_outline(raw)
        if outline:
            return outline
        logger.warning("大纲 JSON 解析失败，重试一次")
    raise VideoTaskError("大纲生成失败：模型输出无法解析（已自动重试），请点击重试再试一次")


def _generate_questions(
    title: str, segments: list[dict], outline: list[dict]
) -> tuple[list[dict], list[str]]:
    """出题基于字幕全文（与网页出题一致：基于原文出题+答案）；推荐标签基于大纲（已提炼更凝练）。

    解析失败自动重试一次（同大纲：推理模型偶发截断）。
    """
    messages = prompts.build_video_question_messages(
        title=title, subtitle_text=_segments_to_text(segments)
    )
    questions: list[dict] = []
    for _ in range(2):
        raw = llm.chat(
            messages, temperature=0.5, max_tokens=QUESTION_MAX_TOKENS, timeout=300.0
        )
        questions = prompts.parse_video_questions(raw)
        if questions:
            break
        logger.warning("题目 JSON 解析失败，重试一次")
    if not questions:
        raise VideoTaskError("题目生成失败：模型输出无法解析（已自动重试），请点击重试再试一次")
    outline_text = "\n".join(
        f"- [{_fmt_ts(o['time_sec'])}] {o['title']}：{o['summary']}" for o in outline
    )
    tag_raw = llm.chat(
        prompts.build_video_tag_messages(title=title, outline_text=outline_text),
        max_tokens=TAG_MAX_TOKENS,
        timeout=120.0,
    )
    return questions, prompts.parse_tags(tag_raw)


# ---------------------------------------------------------------
# 入库 / 重试 / 删除 / 重启恢复
# ---------------------------------------------------------------

def _save_video(
    task_id: int,
    bvid: str,
    title: str,
    mode: str,
    subtitle_path: Path | None,
    audio_path: Path | None,
    video_path: Path | None,
    outline: list[dict],
    segments: list[dict],
    questions: list[dict],
    tags: list[str],
) -> None:
    def rel(p: Path | None) -> str | None:
        """绝对路径 → 相对媒体根目录的路径（{bvid}/{filename}），前端拼 /api/media/ 访问。"""
        if p is None:
            return None
        try:
            return str(p.relative_to(video_download.MEDIA_ROOT))
        except ValueError:
            return str(p)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM bilibili_videos WHERE task_id = %s", (task_id,))
        row = cur.fetchone()
        if row is None:  # 防御：任务创建时已初始化视频行
            raise VideoTaskError("任务关联的视频行缺失，请删除任务后重新提交")
        video_id = row[0]
        cur.execute(
            """
            UPDATE bilibili_videos
            SET title = %s, mode = %s, outline = %s,
                local_subtitle_path = %s, local_audio_path = %s, local_video_path = %s,
                suggested_tags = %s
            WHERE id = %s
            """,
            (
                title or "",
                mode,
                json.dumps(outline, ensure_ascii=False),
                rel(subtitle_path),
                rel(audio_path),
                rel(video_path),
                tags,
                video_id,
            ),
        )
        # 幂等：重试时清空重写子表
        cur.execute("DELETE FROM video_segments WHERE video_id = %s", (video_id,))
        for s in segments:
            cur.execute(
                "INSERT INTO video_segments (video_id, start_ts, end_ts, content) VALUES (%s, %s, %s, %s)",
                (video_id, int(s["start_sec"]), int(s["end_sec"]), s["text"]),
            )
        cur.execute("DELETE FROM video_questions WHERE video_id = %s", (video_id,))
        for i, q in enumerate(questions):
            cur.execute(
                "INSERT INTO video_questions (video_id, question, reference_answer, ts, sort_order) VALUES (%s, %s, %s, %s, %s)",
                (video_id, q["question"], q["answer"], int(q["time_sec"] or 0), i),
            )


def retry_task(task_id: int) -> None:
    """重置失败任务并重新入队：媒体文件保留（断点续跑），大纲/题目/字幕段清空后重生成。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM bilibili_tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise LookupError("任务不存在")
        if row[0] == STATUS_PROCESSING:
            raise RuntimeError("任务正在处理中，无法重试")
        cur.execute(
            "UPDATE bilibili_tasks SET status = %s, progress = 0, step_desc = '等待执行', error_msg = NULL WHERE id = %s",
            (STATUS_PENDING, task_id),
        )
        cur.execute("SELECT id FROM bilibili_videos WHERE task_id = %s", (task_id,))
        vrow = cur.fetchone()
        if vrow is not None:
            cur.execute(
                "UPDATE bilibili_videos SET outline = NULL, suggested_tags = NULL WHERE id = %s",
                (vrow[0],),
            )
            cur.execute("DELETE FROM video_segments WHERE video_id = %s", (vrow[0],))
            cur.execute("DELETE FROM video_questions WHERE video_id = %s", (vrow[0],))
    from app.services import task_queue  # 延迟导入避免循环依赖

    task_queue.enqueue(task_id)


def delete_task(task_id: int) -> None:
    """删除任务：DB 级联删除视频知识（videos → segments/questions），并清理媒体目录。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT bvid, status FROM bilibili_tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise LookupError("任务不存在")
        if row[1] == STATUS_PROCESSING:
            raise RuntimeError("任务正在处理中，请稍后再删")
        cur.execute("DELETE FROM bilibili_tasks WHERE id = %s", (task_id,))
    # 同一 bvid 唯一任务（UNIQUE(user_id, bvid)），整个媒体目录可安全清理
    shutil.rmtree(video_download.media_dir(row[0]), ignore_errors=True)


def recover_interrupted() -> int:
    """服务重启后：PENDING/PROCESSING 任务标记为失败（可重试续跑）。返回恢复数量。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE bilibili_tasks
            SET status = 'FAILED',
                error_msg = '服务重启导致任务中断，可点击重试继续（已下载文件将跳过）',
                progress = 0,
                step_desc = NULL
            WHERE status IN ('PENDING', 'PROCESSING')
            """
        )
        return cur.rowcount
