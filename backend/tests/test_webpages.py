"""模块二测试：出题解析单测 + 网页出题集成（FakeExtract/FakeLLM，真实 PG）。

集成测试对正文抽取/LLM 做 monkeypatch（外部网络与模型不可控），入库与删除走真实链路；
测试数据自建自清（cleanup_article 删除级联删题）。
"""
import json

import pytest

from app import prompts
from app.services import webpage_service


# client 为 conftest.py 共享的会话级夹具（全测试会话一次 lifespan）


# ---------------------------------------------------------------
# 单测：出题结果解析（无 DB、无网络）
# ---------------------------------------------------------------

class TestParseQuestions:
    def test_normal_json(self):
        text = json.dumps(
            {
                "questions": [
                    {"question": "文章讲了什么？", "reference_answer": "讲了装饰器。"},
                    {"question": "什么是语法糖？", "reference_answer": "简化写法的机制。"},
                ],
                "tags": ["Python", "装饰器"],
            },
            ensure_ascii=False,
        )
        questions, tags = prompts.parse_questions(text)
        assert len(questions) == 2
        assert questions[0]["question"] == "文章讲了什么？"
        assert tags == ["Python", "装饰器"]

    def test_code_fence_wrapped(self):
        text = '```json\n{"questions": [{"question": "要点？", "reference_answer": "答"}], "tags": ["要点"]}\n```'
        questions, tags = prompts.parse_questions(text)
        assert len(questions) == 1 and tags == ["要点"]

    def test_invalid_returns_empty(self):
        assert prompts.parse_questions("") == ([], [])
        assert prompts.parse_questions("抱歉，我无法生成题目") == ([], [])
        questions, _ = prompts.parse_questions('{"questions": [{"question": "  "}]}')
        assert questions == []  # 空题干过滤

    def test_question_count_capped_at_5(self):
        qs = [{"question": f"问题{i}", "reference_answer": "答"} for i in range(8)]
        questions, _ = prompts.parse_questions(json.dumps({"questions": qs}, ensure_ascii=False))
        assert len(questions) == 5


class TestCleanText:
    def test_collapse_blank_lines(self):
        assert webpage_service.clean_text("第一行\n\n\n\n第二行  \n") == "第一行\n\n第二行"


class TestGenerateQuestionsTruncate:
    def test_long_content_truncated(self, monkeypatch):
        captured: dict = {}

        def fake_stream(messages, **kwargs):
            captured["messages"] = messages
            yield '{"questions": [], "tags": []}'

        monkeypatch.setattr(webpage_service.llm, "stream_chat", fake_stream)
        webpage_service.generate_questions("长文", "字" * 20000)
        content = captured["messages"][-1]["content"]
        assert "已截断" in content
        assert len(content) < 20000


# ---------------------------------------------------------------
# 集成：FakeExtract / FakeLLM + 真实 PG
# ---------------------------------------------------------------

FAKE_TITLE = "Python 装饰器详解"
FAKE_CONTENT = (
    "装饰器是 Python 的一种语法糖，本质是高阶函数，用于在不修改原函数的前提下扩展其行为。"
    "常见用途包括日志记录、权限校验、缓存与性能统计。"
    "闭包是装饰器的基础：内层函数捕获外层函数的变量，使得装饰器可以记住被装饰函数。"
)

QUESTION_JSON = json.dumps(
    {
        "questions": [
            {
                "question": "装饰器的本质是什么？",
                "reference_answer": "本质是高阶函数，在不修改原函数的前提下扩展行为。",
            },
            {"question": "闭包在装饰器中起什么作用？", "reference_answer": "使装饰器记住被装饰函数。"},
        ],
        "tags": ["Python", "装饰器"],
    },
    ensure_ascii=False,
)


def _fake_extract(url):
    return FAKE_TITLE, FAKE_CONTENT


def _fake_stream_questions(messages, **kwargs):
    captured["messages"] = messages
    yield QUESTION_JSON


def _fake_stream_broken(messages, **kwargs):
    yield "抱歉，我无法生成题目。"


captured: dict = {}


def _read_sse(resp) -> list[tuple[str, dict]]:
    """解析响应体中的 SSE 事件为 [(event, data)]。"""
    events = []
    for block in resp.text.split("\n\n"):
        ev, data = None, None
        for line in block.strip().split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if ev and data is not None:
            events.append((ev, data))
    return events


@pytest.fixture()
def cleanup_article(client):
    created: list[int] = []
    yield created
    for aid in created:
        client.delete(f"/api/webpages/{aid}")


def test_create_flow(client, cleanup_article, monkeypatch):
    """核心闭环：提交 URL → step/done → 列表/筛选/详情 → 删除级联删题。"""
    monkeypatch.setattr(webpage_service, "extract_content", _fake_extract)
    monkeypatch.setattr(webpage_service.llm, "stream_chat", _fake_stream_questions)
    url = "https://example.com/decorator-article.html"

    resp = client.post("/api/webpages", json={"url": url})
    assert resp.status_code == 200, resp.text
    events = _read_sse(resp)
    kinds = [e for e, _ in events]
    assert kinds[0] == "step" and kinds[-1] == "done", kinds
    assert events[0][1]["step"] == "extracting"
    assert events[1][1]["step"] == "generating"
    done = events[-1][1]
    assert done["question_count"] == 2
    assert done["questions_failed"] is False
    assert done["suggested_tags"] == ["Python", "装饰器"]
    aid = done["id"]
    cleanup_article.append(aid)

    # 出题 Prompt 应包含标题与正文
    assert FAKE_TITLE in captured["messages"][-1]["content"]
    assert "语法糖" in captured["messages"][-1]["content"]

    # 列表：正文为截断预览，含题数与标签
    articles = client.get("/api/webpages").json()
    mine = next(a for a in articles if a["id"] == aid)
    assert mine["title"] == FAKE_TITLE
    assert mine["question_count"] == 2
    assert mine["suggested_tags"] == ["Python", "装饰器"]
    assert mine["content_preview"].startswith(FAKE_CONTENT[:20])

    # 关键词筛选（标题/URL）
    assert any(a["id"] == aid for a in client.get("/api/webpages", params={"q": "装饰器"}).json())
    assert any(
        a["id"] == aid
        for a in client.get("/api/webpages", params={"q": "decorator-article"}).json()
    )
    assert all(
        a["id"] != aid for a in client.get("/api/webpages", params={"q": "不存在的"}).json()
    )

    # 详情：全文 + 题目按序
    detail = client.get(f"/api/webpages/{aid}").json()
    assert detail["content"] == FAKE_CONTENT
    assert len(detail["questions"]) == 2
    assert detail["questions"][0]["question"].startswith("装饰器的本质")

    # 删除：级联删题
    assert client.delete(f"/api/webpages/{aid}").status_code == 204
    assert client.get(f"/api/webpages/{aid}").status_code == 404
    cleanup_article.remove(aid)


def test_duplicate_url(client, cleanup_article, monkeypatch):
    """URL 已收录：返回 409 + existing_id，前端可跳转已有条目。"""
    monkeypatch.setattr(webpage_service, "extract_content", _fake_extract)
    monkeypatch.setattr(webpage_service.llm, "stream_chat", _fake_stream_questions)
    url = "https://example.com/dup-article.html"

    first = _read_sse(client.post("/api/webpages", json={"url": url}))
    aid = first[-1][1]["id"]
    cleanup_article.append(aid)

    resp = client.post("/api/webpages", json={"url": url})
    assert resp.status_code == 409
    body = resp.json()
    assert "已收录" in body["detail"]
    assert body["existing_id"] == aid


def test_question_failure_then_regenerate(client, cleanup_article, monkeypatch):
    """出题失败降级：文章保留、题空（questions_failed）→ 重新生成后题目出现。"""
    monkeypatch.setattr(webpage_service, "extract_content", _fake_extract)
    monkeypatch.setattr(webpage_service.llm, "stream_chat", _fake_stream_broken)
    url = "https://example.com/retry-article.html"

    events = _read_sse(client.post("/api/webpages", json={"url": url}))
    done = events[-1][1]
    assert done["questions_failed"] is True
    assert done["question_count"] == 0
    aid = done["id"]
    cleanup_article.append(aid)

    detail = client.get(f"/api/webpages/{aid}").json()
    assert detail["content"] == FAKE_CONTENT  # 正文仍保留
    assert detail["questions"] == []

    # 重新生成：题目与标签覆盖入库
    monkeypatch.setattr(webpage_service.llm, "stream_chat", _fake_stream_questions)
    events = _read_sse(client.post(f"/api/webpages/{aid}/regenerate"))
    done = events[-1][1]
    assert done["questions_failed"] is False and done["question_count"] == 2
    detail = client.get(f"/api/webpages/{aid}").json()
    assert len(detail["questions"]) == 2
    assert detail["suggested_tags"] == ["Python", "装饰器"]

    # regenerate 不存在的文章 → error 事件
    resp = client.post("/api/webpages/999999/regenerate")
    events = _read_sse(resp)
    assert events[0][0] == "error" and "不存在" in events[0][1]["detail"]


def test_extract_fallback_chain(monkeypatch):
    """抽取兜底链：trafilatura 失败 → Jina Reader 被调用；均失败抛 WebpageAbort。"""
    calls: dict = {"jina": 0}

    def no_trafilatura(url):
        return "", ""

    def fake_jina(url):
        calls["jina"] += 1
        return "来自 Jina 的文章", "这是 Jina 兜底抽取的正文。" * 20

    monkeypatch.setattr(webpage_service, "_extract_trafilatura", no_trafilatura)
    monkeypatch.setattr(webpage_service, "_extract_jina", fake_jina)
    title, content = webpage_service.extract_content("https://example.com/x.html")
    assert title == "来自 Jina 的文章" and calls["jina"] == 1

    def no_jina(url):
        calls["jina"] += 1
        return "", ""

    monkeypatch.setattr(webpage_service, "_extract_jina", no_jina)
    with pytest.raises(webpage_service.WebpageAbort, match="正文抽取失败"):
        webpage_service.extract_content("https://example.com/y.html")


def test_extract_failure_stream(client, monkeypatch):
    """抽取全失败：SSE error 事件，且不产生文章记录。"""
    def raise_abort(url):
        raise webpage_service.WebpageAbort("正文抽取失败，请确认链接可访问")

    monkeypatch.setattr(webpage_service, "extract_content", raise_abort)
    url = "https://example.com/404-page.html"
    resp = client.post("/api/webpages", json={"url": url})
    events = _read_sse(resp)
    assert events[0][0] == "step" and events[-1][0] == "error"
    assert "正文抽取失败" in events[-1][1]["detail"]
    # 失败不产生文章记录（真实库中可能有其他文章，只断言该链接不存在）
    assert all(a["url"] != url for a in client.get("/api/webpages").json())
