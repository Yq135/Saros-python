"""全局配置：从 backend/.env 读取（pydantic-settings）。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL + pgvector（本地 Linux 上的 PG 服务，连接参数走 .env）
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "saros"

    # 主 LLM（OpenAI 兼容协议，DeepSeek 等国产模型）
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # 音频模式 ASR 转写（自建 mlx-qwen3-asr，OpenAI 兼容接口；仅无 CC/AI 字幕时启用）
    asr_base_url: str = "http://100.100.61.45:9001/v1"
    asr_api_key: str = ""
    asr_model: str = "mlx-community/Qwen3-ASR-1.7B-bf16"

    # 嵌入（本地 CPU）；支持 HF 模型名或本地目录（相对路径基于 backend/）
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    hf_endpoint: str = "https://hf-mirror.com"

    # 搜索源（免费；ddgs 主力，bing/baidu 兜底）
    search_providers: str = "ddgs,bing,baidu"

    # B 站 cookie 路径（相对路径基于 backend/）与清晰度上限
    cookie_path: str = "data/.cookies.txt"
    max_video_height: int = 720
    # 跳过字幕下载：开启后不下载 CC/AI 字幕，直接走音频模式（ASR 转写带断句标点，
    # 大纲/出题质量更高，但更耗时）；默认关
    skip_subtitle: bool = False

    def cookie_file(self) -> Path:
        """cookie 文件绝对路径（相对路径基于 backend/ 目录）。"""
        p = Path(self.cookie_path)
        return p if p.is_absolute() else BACKEND_DIR / p

    def embedding_model_path(self) -> str:
        """嵌入模型路径：绝对路径原样返回；相对路径优先按 backend/ 解析，不存在则视为 HF 模型名。"""
        p = Path(self.embedding_model)
        if p.is_absolute():
            return str(p)
        candidate = BACKEND_DIR / p
        return str(candidate) if candidate.exists() else self.embedding_model


settings = Settings()
