"""模块二服务：网页正文抽取 + 出题 + 入库（SSE 流编排）。

POST /api/webpages 执行链：
1. 路由层查重（url 唯一，重复返回 409 + existing_id）
2. 正文抽取：trafilatura 优先 → Jina Reader 兜底（均失败报错）
3. 文章先入库（出题失败时正文仍保留）→ LLM 生成题目+推荐标签 → 题目入库
4. SSE 事件：step（正在提取正文/正在生成题目）→ done（含题目与标签）；失败 error

POST /api/webpages/{id}/regenerate：仅重新出题（覆盖旧题目与标签），不动正文。
"""
import json
import logging
import re
from typing import Iterable

import httpx
import trafilatura

from app import llm, prompts
from app.db import get_conn, get_user_id

logger = logging.getLogger("uvicorn.error")

CONTENT_MAX_CHARS = 15000  # 正文超长截断（DeepSeek 上下文余量，约 1.5 万字）
MIN_CONTENT_CHARS = 200  # 正文过短视为抽取失败
EXTRACT_TIMEOUT = 30.0
# trafilatura 默认 UA 会被部分站点（如维基）拒绝，用浏览器 UA 抓取
EXTRACT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
JINA_URL = "https://r.jina.ai/"  # 抽取兜底（仅 trafilatura 失败时 URL 才外发）
JINA_TIMEOUT = 60.0


class WebpageAbort(Exception):
    """业务性失败：向 SSE 流发送 error 事件（detail 为中文提示）。"""

    def __init__(self, detail: str):
        self.detail = detail


# ---------------------------------------------------------------
# 正文抽取（trafilatura → Jina 兜底）
# ---------------------------------------------------------------

def clean_text(text: str) -> str:
    """压缩连续空行与行尾空白（trafilatura 输出常见噪声）。"""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append(ln)
    return "\n".join(out).strip()


def extract_content(url: str) -> tuple[str, str]:
    """返回 (标题, 正文)。trafilatura 优先，失败兜底 Jina Reader；均失败抛 WebpageAbort。"""
    title, content = _extract_trafilatura(url)
    if content:
        return title, content
    title, content = _extract_jina(url)
    if content:
        return title, content
    raise WebpageAbort("正文抽取失败：trafilatura 与 Jina Reader 均未能提取正文，请确认链接可访问")


def _extract_trafilatura(url: str) -> tuple[str, str]:
    """本地抽取：httpx 抓取（浏览器 UA 防反爬）→ trafilatura 提取正文与标题；任何失败返回空（交兜底）。"""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": EXTRACT_UA},
            timeout=EXTRACT_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        content = trafilatura.extract(resp.text, include_comments=False)
        if not content or len(content.strip()) < MIN_CONTENT_CHARS:
            return "", ""
        title = ""
        try:
            md = trafilatura.extract_metadata(resp.text)
            title = (md.title or "") if md else ""
        except Exception:  # noqa: BLE001 — 元数据失败不影响正文
            pass
        return title.strip(), clean_text(content)
    except Exception as e:  # noqa: BLE001 — 网络/解析异常交兜底
        logger.warning("trafilatura 抽取失败 %s: %s", url, e)
        return "", ""


def _extract_jina(url: str) -> tuple[str, str]:
    """Jina Reader 兜底：返回 markdown 化正文；任何失败返回空。"""
    try:
        resp = httpx.get(JINA_URL + url, timeout=JINA_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text or ""
    except Exception as e:  # noqa: BLE001 — 兜底失败由上层统一报错
        logger.warning("Jina Reader 兜底失败 %s: %s", url, e)
        return "", ""
    # 输出 markdown：首部有 Title:/Published Time:/URL Source: 元信息行，正文以 "Markdown Content:" 标记
    title = ""
    m = re.match(r"^Title:\s*(.+)$", text.strip(), re.MULTILINE)
    if m:
        title = m.group(1).strip()
    body = text
    for pattern in (
        r"^Title:.*$",
        r"^Published Time:.*$",
        r"^URL Source:.*$",
        r"^Markdown Content:.*$",
    ):
        body = re.sub(pattern, "", body, flags=re.MULTILINE)
    body = clean_text(body)
    if len(body) < MIN_CONTENT_CHARS:
        return "", ""
    return title, body


# ---------------------------------------------------------------
# 出题（LLM 一次生成题目 + 推荐标签）
# ---------------------------------------------------------------

def generate_questions(title: str, content: str) -> tuple[list[dict], list[str]]:
    """LLM 出题：流式收集完整输出后解析。

    返回 (题目列表, 标签列表)；失败返回 ([], []) —— 文章保留、题目可后补。
    """
    truncated = content[:CONTENT_MAX_CHARS]
    if len(content) > CONTENT_MAX_CHARS:
        truncated += "\n\n（正文较长已截断，请基于以上内容出题。）"
    messages = prompts.build_question_messages(title=title, content=truncated)
    try:
        parts = list(llm.stream_chat(messages, temperature=0.3, max_tokens=2048))
        text = "".join(parts)
        questions, tags = prompts.parse_questions(text)
        if not questions:
            logger.warning("出题结果为空或解析失败，原文前 300 字：%s", text[:300])
        return questions, tags
    except Exception as e:  # noqa: BLE001 — 出题失败不阻断正文入库
        logger.warning("题目生成失败: %s", e)
        return [], []


# ---------------------------------------------------------------
# 入库
# ---------------------------------------------------------------

def find_existing(url: str, user_id: int) -> int | None:
    """URL 查重（url 列 UNIQUE）：已收录返回文章 id，否则 None。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM web_articles WHERE user_id = %s AND url = %s", (user_id, url)
        )
        row = cur.fetchone()
        return row[0] if row else None


def save_article(*, url: str, title: str, content: str, user_id: int) -> int:
    """文章入库（题目稍后生成；出题失败时正文仍保留）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO web_articles (user_id, url, title, content)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, url, title or None, content),
        )
        return cur.fetchone()[0]


def save_questions_and_tags(article_id: int, questions: list[dict], tags: list[str]) -> int:
    """题目 + 推荐标签入库，返回题目数。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE web_articles SET suggested_tags = %s WHERE id = %s",
            (tags or None, article_id),
        )
        for i, q in enumerate(questions):
            cur.execute(
                """
                INSERT INTO webpage_questions (article_id, question, reference_answer, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (article_id, q["question"], q["reference_answer"], i),
            )
    return len(questions)


def load_article(article_id: int, user_id: int) -> tuple[str, str]:
    """读取文章 (标题, 正文)；不存在抛 WebpageAbort。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(title, ''), content FROM web_articles WHERE id = %s AND user_id = %s",
            (article_id, user_id),
        )
        row = cur.fetchone()
    if row is None:
        raise WebpageAbort("文章不存在或已删除")
    return row[0], row[1]


def replace_questions(article_id: int, questions: list[dict], tags: list[str]) -> int:
    """覆盖式重出题：删旧题 → 插新题 + 更新标签（同一事务），返回题目数。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM webpage_questions WHERE article_id = %s", (article_id,))
        cur.execute(
            "UPDATE web_articles SET suggested_tags = %s WHERE id = %s",
            (tags or None, article_id),
        )
        for i, q in enumerate(questions):
            cur.execute(
                """
                INSERT INTO webpage_questions (article_id, question, reference_answer, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (article_id, q["question"], q["reference_answer"], i),
            )
    return len(questions)


# ---------------------------------------------------------------
# SSE 流编排
# ---------------------------------------------------------------

def create_stream(url: str) -> Iterable[str]:
    """POST /api/webpages 的 SSE 流生成器（同步，线程池执行）。

    事件：step（正在提取正文/正在生成题目）→ done（文章+题目+标签）
    失败：error（detail 中文提示）
    """
    user_id = get_user_id()
    try:
        yield _sse("step", {"step": "extracting", "desc": "正在提取正文…"})
        title, content = extract_content(url)

        # 文章先入库：出题失败时正文仍保留（题目可后补）
        article_id = save_article(url=url, title=title, content=content, user_id=user_id)

        yield _sse("step", {"step": "generating", "desc": "正在生成题目…"})
        questions, tags = generate_questions(title, content)
        count = save_questions_and_tags(article_id, questions, tags)
        yield _sse(
            "done",
            {
                "id": article_id,
                "title": title,
                "url": url,
                "suggested_tags": tags,
                "question_count": count,
                "questions_failed": count == 0,
            },
        )
    except WebpageAbort as e:
        yield _sse("error", {"detail": e.detail})
    except Exception as e:  # noqa: BLE001 — 兜底：任何异常都以 error 事件返回
        logger.exception("网页出题流异常")
        yield _sse("error", {"detail": f"服务异常：{e}"})


def regenerate_stream(article_id: int) -> Iterable[str]:
    """POST /api/webpages/{id}/regenerate 的 SSE 流生成器：仅重新出题，不动正文。"""
    user_id = get_user_id()
    try:
        title, content = load_article(article_id, user_id)
        yield _sse("step", {"step": "generating", "desc": "正在生成题目…"})
        questions, tags = generate_questions(title, content)
        count = replace_questions(article_id, questions, tags)
        yield _sse(
            "done",
            {
                "id": article_id,
                "title": title,
                "suggested_tags": tags,
                "question_count": count,
                "questions_failed": count == 0,
            },
        )
    except WebpageAbort as e:
        yield _sse("error", {"detail": e.detail})
    except Exception as e:  # noqa: BLE001
        logger.exception("重新出题流异常")
        yield _sse("error", {"detail": f"服务异常：{e}"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
