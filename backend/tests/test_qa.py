"""模块一测试：混合打分单测 + 问答集成（FakeSearch/FakeLLM，真实 PG + 真实嵌入）。

集成测试对搜索/LLM 做 monkeypatch（外部依赖不可控），检索与入库走真实链路；
测试数据自建自清（会话删除级联删轮次，笔记走 cleanup_knowledge）。
"""
import json
import os

import pytest

from app import prompts
from app.db import get_conn
from app.search import SearchResult
from app.services import qa_service


# client 为 conftest.py 共享的会话级夹具（全测试会话一次 lifespan）


# ---------------------------------------------------------------
# 单测：混合打分与标签解析（无 DB、无网络）
# ---------------------------------------------------------------

class TestHybridScore:
    def test_question_tokens(self):
        tokens = qa_service.question_tokens("Python 装饰器怎么用？")
        assert "装饰" in tokens  # jieba 将「装饰器」切为「装饰」+「器」
        assert "Python" in tokens
        assert "用" not in tokens  # 单字应被过滤

    def test_lex_overlap(self):
        tokens = {"装饰", "Python"}
        assert qa_service.lex_overlap(tokens, "Python 装饰器可以包装函数。") == 1.0
        assert qa_service.lex_overlap(tokens, "红烧肉做法：五花肉焯水慢炖。") == 0.0

    def test_tag_hit(self):
        tokens = {"装饰", "Python"}
        assert qa_service.tag_hit(tokens, ["Python", "语法糖"]) == 0.5

    def test_hybrid_score_threshold(self):
        tokens = {"装饰", "Python"}
        related = qa_service.hybrid_score(
            similarity=0.8, tokens=tokens, content="Python 装饰器可以包装函数。", tags=["Python"]
        )
        assert 0 <= related <= 1
        assert related > qa_service.SCORE_THRESHOLD
        unrelated = qa_service.hybrid_score(
            similarity=0.1, tokens=tokens, content="红烧肉做法", tags=["美食"]
        )
        assert unrelated < qa_service.SCORE_THRESHOLD


class TestParseTags:
    def test_json_array(self):
        assert prompts.parse_tags('["Python", "装饰器"]') == ["Python", "装饰器"]

    def test_fallback_quotes(self):
        assert prompts.parse_tags('推荐标签："编程"「Python」') == ["编程", "Python"]

    def test_invalid_empty(self):
        assert prompts.parse_tags("") == []
        assert prompts.parse_tags("没有标签") == []


# ---------------------------------------------------------------
# 集成：FakeSearch / FakeLLM + 真实 PG/嵌入/检索
# ---------------------------------------------------------------

FAKE_SOURCES = [
    SearchResult(
        title="Python 装饰器 - 廖雪峰",
        url="https://example.com/decorator",
        snippet="装饰器是 Python 的函数包装机制。",
    ),
]


def _fake_search(query):
    return list(FAKE_SOURCES)


def _fake_stream_chat(messages, **kwargs):
    captured["messages"] = messages
    yield "装饰器是"
    yield "一种包装函数的语法。"


def _fake_chat_tags(messages, **kwargs):
    return '["Python", "装饰器"]'


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
def cleanup_knowledge(client):
    created: list[int] = []
    yield created
    for kid in created:
        client.delete(f"/api/knowledge/{kid}")


@pytest.fixture()
def cleanup_conv(client):
    created: list[int] = []
    yield created
    for cid in created:
        client.delete(f"/api/qa/conversations/{cid}")


def test_ask_multi_turn_flow(client, cleanup_conv, monkeypatch):
    """核心闭环：提问建会话 → 追问带上下文 → 列表/筛选/详情/级联删除。"""
    monkeypatch.setattr(qa_service, "search_web", _fake_search)
    monkeypatch.setattr(qa_service.llm, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(qa_service.llm, "chat", _fake_chat_tags)

    # 首轮：新建会话
    resp = client.post("/api/qa/ask", json={"question": "Python 装饰器是什么？"})
    assert resp.status_code == 200, resp.text
    events = _read_sse(resp)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done", kinds
    start = events[0][1]
    assert start["is_new"] is True
    assert start["conversation_id"] > 0
    assert start["sources"][0]["url"] == "https://example.com/decorator"
    done = events[-1][1]
    assert "包装函数" in done["answer"]
    assert done["suggested_tags"] == ["Python", "装饰器"]  # 仅首轮生成
    cid = start["conversation_id"]
    cleanup_conv.append(cid)

    # 追问：同会话第二轮，历史上下文应注入 Prompt，且不生成标签
    monkeypatch.setattr(qa_service.llm, "chat", lambda messages, **kw: '["不应生成"]')
    resp2 = client.post(
        "/api/qa/ask", json={"question": "能举个简单例子吗？", "conversation_id": cid}
    )
    events2 = _read_sse(resp2)
    assert events2[0][1]["is_new"] is False
    assert events2[-1][1]["suggested_tags"] == []
    assert "Python 装饰器是什么？" in captured["messages"][-1]["content"]  # 历史上下文

    # 会话列表：轮次数与关键词筛选
    resp = client.get("/api/qa/conversations")
    convs = resp.json()
    mine = next(c for c in convs if c["id"] == cid)
    assert mine["message_count"] == 2
    resp = client.get("/api/qa/conversations", params={"q": "装饰器"})
    assert any(c["id"] == cid for c in resp.json())
    resp = client.get("/api/qa/conversations", params={"q": "不存在的关键词"})
    assert all(c["id"] != cid for c in resp.json())

    # 会话详情：多轮按时间顺序，首轮带推荐标签与引用沉淀 id
    detail = client.get(f"/api/qa/conversations/{cid}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["suggested_tags"] == ["Python", "装饰器"]
    assert detail["messages"][1]["suggested_tags"] == []

    # 删除会话：级联删轮次
    assert client.delete(f"/api/qa/conversations/{cid}").status_code == 204
    assert client.get(f"/api/qa/conversations/{cid}").status_code == 404
    cleanup_conv.remove(cid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM qa_messages WHERE conversation_id = %s", (cid,))
        assert cur.fetchone()[0] == 0


def test_ask_with_knowledge_hit(client, cleanup_knowledge, cleanup_conv, monkeypatch):
    """混合检索命中：问题相关的手打笔记应出现在 start 事件并入库引用。"""
    resp = client.post(
        "/api/knowledge",
        json={
            "content": "红烧肉做法：五花肉切块焯水，炒糖色后加酱油料酒慢炖一小时。",
            "mastery_level": 0,
            "tags": ["美食"],
        },
    )
    assert resp.status_code == 201, resp.text
    kid = resp.json()["id"]
    cleanup_knowledge.append(kid)

    monkeypatch.setattr(qa_service, "search_web", _fake_search)
    monkeypatch.setattr(qa_service.llm, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(qa_service.llm, "chat", _fake_chat_tags)

    resp = client.post("/api/qa/ask", json={"question": "怎么做红烧肉？"})
    events = _read_sse(resp)
    assert events[0][0] == "start" and events[-1][0] == "done"
    knowledge = events[0][1]["knowledge"]
    assert any(k["id"] == kid for k in knowledge), "沉淀笔记应被混合检索命中"
    cid = events[0][1]["conversation_id"]
    cleanup_conv.append(cid)

    detail = client.get(f"/api/qa/conversations/{cid}").json()
    ref = detail["messages"][0]["referenced_knowledge"]
    assert any(k["id"] == kid and "红烧肉" in k["content"] for k in ref)


def test_ask_degrade_search_down(client, cleanup_knowledge, cleanup_conv, monkeypatch):
    """FR-1.7 降级：搜索全挂但有沉淀命中 → 仅基于沉淀回答并注明（Prompt 含降级说明）。"""
    resp = client.post(
        "/api/knowledge",
        json={
            "content": "黑胡椒牛肉粒：牛肉切块腌制后大火快炒，加黑胡椒酱。",
            "mastery_level": 0,
            "tags": ["美食"],
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_knowledge.append(resp.json()["id"])

    monkeypatch.setattr(qa_service, "search_web", lambda q: [])
    monkeypatch.setattr(qa_service.llm, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(qa_service.llm, "chat", _fake_chat_tags)

    resp = client.post("/api/qa/ask", json={"question": "黑胡椒牛肉粒怎么做？"})
    events = _read_sse(resp)
    assert events[0][0] == "start" and events[-1][0] == "done"
    assert events[0][1]["sources"] == []
    assert any("牛肉" in k["content"] for k in events[0][1]["knowledge"])
    assert "联网搜索不可用" in captured["messages"][-1]["content"]
    cleanup_conv.append(events[0][1]["conversation_id"])


def test_ask_error_when_nothing_available(client, monkeypatch):
    """FR-1.7 降级：搜索与沉淀均不可用 → error 事件，且新会话被自动清理。"""
    monkeypatch.setattr(qa_service, "search_web", lambda q: [])
    monkeypatch.setattr(qa_service, "retrieve_knowledge", lambda q, uid: [])

    resp = client.post(
        "/api/qa/ask", json={"question": "弦论里闭弦的振动模式对黑洞熵有什么贡献？"}
    )
    events = _read_sse(resp)
    assert len(events) == 1 and events[0][0] == "error"
    assert "均不可用" in events[0][1]["detail"]

    # 失败的新会话不应残留（空会话被清理，且列表只展示有消息的会话）
    resp = client.get("/api/qa/conversations")
    assert all("弦论" not in c["title"] for c in resp.json())


@pytest.mark.skipif(
    os.getenv("SAROS_LIVE") != "1", reason="需真实搜索/LLM：SAROS_LIVE=1 时运行"
)
def test_live_ask(client):
    """真实链路冒烟：真实搜索 + 真实 LLM（手动运行验证用）。"""
    resp = client.post("/api/qa/ask", json={"question": "什么是快速排序？"})
    assert resp.status_code == 200
    events = _read_sse(resp)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    done = events[-1][1]
    assert done["answer"]
    client.delete(f"/api/qa/conversations/{done['conversation_id']}")
