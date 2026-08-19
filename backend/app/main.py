"""Saros 后端入口：装配 FastAPI 应用。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import embeddings
from app.db import ensure_user
from app.routers import bilibili, knowledge, qa, settings as settings_router, webpages
from app.services import task_queue, video_download, video_task

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 单用户默认用户就绪；预加载嵌入模型（下载/加载失败在启动期暴露，而非首次录入时）
    uid = ensure_user()
    logger.info("默认用户就绪 id=%s", uid)
    model = embeddings.get_model()
    logger.info("嵌入模型已加载（%s 维）", model.get_embedding_dimension())
    # 视频任务：重启后中间态任务标记失败（可重试），启动单 worker 串行队列
    recovered = video_task.recover_interrupted()
    if recovered:
        logger.info("服务重启：%d 个中断的视频任务已标记失败（可重试）", recovered)
    video_download.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    task_queue.start()
    yield
    await task_queue.stop()


app = FastAPI(title="Saros", version="0.2.0", lifespan=lifespan)

# 本地单用户 Web 应用：仅放行 Vite dev server 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge.router)
app.include_router(qa.router)
app.include_router(webpages.router)
app.include_router(bilibili.router)
app.include_router(settings_router.router)

# 本地媒体静态服务（视频/音频/字幕）：StaticFiles 支持 Range 请求，播放器可拖动
app.mount("/api/media", StaticFiles(directory=str(video_download.MEDIA_ROOT)), name="media")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}
