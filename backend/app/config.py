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

    # 音频模式多模态模型（通义 Qwen3.5-Omni，仅无 CC/AI 字幕时启用）
    audio_llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    audio_llm_api_key: str = ""
    audio_llm_model: str = "qwen3.5-omni-flash"

    # 嵌入（本地 CPU）
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    hf_endpoint: str = "https://hf-mirror.com"

    # 搜索源（免费；ddgs 主力，bing/baidu 兜底）
    search_providers: str = "ddgs,bing,baidu"

    # B 站 cookie 路径（相对路径基于 backend/）与清晰度上限
    cookie_path: str = "data/cookies.txt"
    max_video_height: int = 720

    def cookie_file(self) -> Path:
        """cookie 文件绝对路径（相对路径基于 backend/ 目录）。"""
        p = Path(self.cookie_path)
        return p if p.is_absolute() else BACKEND_DIR / p


settings = Settings()
