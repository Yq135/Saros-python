# Saros 后端镜像：FastAPI + 本地 CPU 嵌入 + ffmpeg（B 站下载/ASR 音频切片必需）
# 注意：本镜像只能在 Linux 上构建（torch CPU 索引没有 macOS wheel，见 docs/部署说明.md）
FROM python:3.10-slim

# Python 3.10 必须：yt-dlp 被 pin 在 2026.7.4（最后一个支持 3.10 的版本）
# ffmpeg：系统级依赖（asr.py 音频切片、视频合并）；tzdata：slim 无时区数据，TZ 需要
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只拷 requirements.txt：依赖层可缓存，代码改动不用重装依赖
COPY requirements.txt .
# torch 单独装 CPU 版（PyPI 默认 CUDA 版，约 10 倍体积）；装完再装其余依赖，
# sentence-transformers 发现 torch 已满足，不会拉 CUDA 版
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 非 root 运行（UID 1000 为 Linux 服务器首个用户惯例；不一致时见部署文档）
RUN useradd -m -u 1000 saros
USER saros

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# 固定单 worker：任务队列是进程内单 worker，多实例会互相打架，禁止 scale
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 健康检查用 Python 标准库，不额外装 curl；
# lifespan 完成前（嵌入模型加载 10~30s）uvicorn 不响应请求，start_period 要留足
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)"]
