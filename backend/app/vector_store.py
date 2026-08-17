"""pgvector 封装：embeddings 表的写入/删除/KNN 检索。

当前仅嵌入手打笔记（source_type='MANUAL'，source_id=manual_knowledge.id）。
写操作由调用方传入连接（与业务写入同事务）；只读检索自建连接。
"""
import psycopg

from app.db import get_conn

SOURCE_MANUAL = "MANUAL"


def upsert_embedding(
    conn: psycopg.Connection,
    *,
    user_id: int,
    source_id: int,
    content: str,
    vector: list[float],
) -> None:
    """覆盖写入一条笔记的向量（编辑时先删旧行再插新行）。调用方管理事务。"""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM embeddings WHERE user_id = %s AND source_type = %s AND source_id = %s",
            (user_id, SOURCE_MANUAL, source_id),
        )
        cur.execute(
            "INSERT INTO embeddings (user_id, source_type, source_id, chunk_content, embedding) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, SOURCE_MANUAL, source_id, content, vector),
        )


def delete_embeddings(conn: psycopg.Connection, *, user_id: int, source_id: int) -> None:
    """删除一条笔记的全部向量行（删除笔记时同事务调用）。"""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM embeddings WHERE user_id = %s AND source_type = %s AND source_id = %s",
            (user_id, SOURCE_MANUAL, source_id),
        )


def search(*, vector: list[float], user_id: int, top_k: int = 10) -> list[dict]:
    """余弦相似度 KNN：返回 [{source_id, chunk_content, similarity}]，按相似度降序。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_id, chunk_content, 1 - (embedding <=> %s::vector) AS similarity
            FROM embeddings
            WHERE user_id = %s AND source_type = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, user_id, SOURCE_MANUAL, vector, top_k),
        )
        return [
            {"source_id": r[0], "chunk_content": r[1], "similarity": float(r[2])}
            for r in cur.fetchall()
        ]
