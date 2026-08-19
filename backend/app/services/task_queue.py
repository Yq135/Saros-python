"""视频任务队列：单 worker 串行执行（NFR-5：视频任务串行）。

worker 在 FastAPI lifespan 启动：主循环从 asyncio.Queue 取任务 id，
用 asyncio.to_thread 跑同步的 run_task（yt-dlp/ffmpeg/LLM 均为阻塞调用，
不阻塞事件循环，同时天然保证同一时刻只有一个视频任务在跑）。
"""
import asyncio
import logging

from app.services import video_task

logger = logging.getLogger("uvicorn.error")

queue: asyncio.Queue[int] | None = None
_worker_task: asyncio.Task | None = None


def start() -> None:
    """启动 worker（幂等）。"""
    global queue, _worker_task
    if queue is not None:
        return
    queue = asyncio.Queue()
    _worker_task = asyncio.create_task(_worker_loop(), name="video-task-worker")
    logger.info("视频任务 worker 已启动（单 worker 串行）")


async def stop() -> None:
    """停止 worker（lifespan 关闭时调用）。"""
    global queue, _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    queue = None


def enqueue(task_id: int) -> None:
    """提交任务 id 到队列（未启动时报错；正常由 lifespan 保证已启动）。"""
    if queue is None:
        raise RuntimeError("任务队列未启动")
    queue.put_nowait(task_id)


async def _worker_loop() -> None:
    assert queue is not None
    while True:
        task_id = await queue.get()
        try:
            await asyncio.to_thread(video_task.run_task, task_id)
        except Exception as e:  # noqa: BLE001 — run_task 内部已兜底，此处仅防御
            logger.exception("任务 %s 执行异常", task_id)
        finally:
            queue.task_done()
