"""Saros 后端入口：装配 FastAPI 应用。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import embeddings
from app.db import ensure_user
from app.routers import knowledge, qa

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 单用户默认用户就绪；预加载嵌入模型（下载/加载失败在启动期暴露，而非首次录入时）
    uid = ensure_user()
    logger.info("默认用户就绪 id=%s", uid)
    model = embeddings.get_model()
    logger.info("嵌入模型已加载（%s 维）", model.get_embedding_dimension())
    yield


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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}
