"""本地嵌入：sentence-transformers + BAAI/bge-small-zh-v1.5（512 维，CPU 推理）。

BGE 检索建议：查询侧加前缀、文档侧不加；向量做 L2 归一化（配合余弦检索）。
"""
import threading

from sentence_transformers import SentenceTransformer

from app.config import settings

# BGE 中文检索官方推荐前缀（查询侧专用）
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_lock = threading.Lock()
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """惰性加载嵌入模型（线程安全，首次调用较慢）。"""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = SentenceTransformer(settings.embedding_model_path())
    return _model


def encode_text(text: str) -> list[float]:
    """文档侧编码（知识点内容，不加前缀）。"""
    return get_model().encode([text], normalize_embeddings=True)[0].tolist()


def encode_query(query: str) -> list[float]:
    """查询侧编码（加 BGE 检索前缀），模块一问答检索用。"""
    return get_model().encode([QUERY_PREFIX + query], normalize_embeddings=True)[0].tolist()
