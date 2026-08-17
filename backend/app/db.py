"""数据库访问：psycopg 连接 + 单用户约定。

本地单用户、低频访问，采用每请求新建连接（无连接池依赖）。
"""
import psycopg

from app.config import settings

# 单用户约定：user_id 固定为该用户的 id（首次启动自动建）
DEFAULT_USERNAME = "saros"


def get_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        dbname=settings.pg_db,
    )


_user_id_cache: int | None = None


def get_user_id() -> int:
    """当前单用户的 id（惰性：首次调用时查库/建用户）。"""
    global _user_id_cache
    if _user_id_cache is None:
        _user_id_cache = ensure_user(DEFAULT_USERNAME)
    return _user_id_cache


def ensure_user(username: str = DEFAULT_USERNAME) -> int:
    """幂等创建默认用户并返回其 id。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username) VALUES (%s) "
            "ON CONFLICT (username) DO NOTHING",
            (username,),
        )
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        assert row is not None
        return row[0]
