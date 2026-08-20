"""模块四集成测试：真实 PG + 真实嵌入（.env 需可达，嵌入模型需就绪）。

测试数据自建自清：创建 id 收集到 cleanup fixture，测试结束（含断言失败）统一删除。
"""
import pytest
from fastapi.testclient import TestClient

from app.db import get_conn, get_user_id
from app.embeddings import encode_query
from app.main import app
from app.vector_store import search


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建默认用户 + 加载模型
        yield c


@pytest.fixture()
def cleanup(client):
    created: list[int] = []
    yield created
    for kid in created:
        client.delete(f"/api/knowledge/{kid}")


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_filter(client, cleanup):
    # 创建（含嵌入；首次调用若模型未加载会较慢）
    resp = client.post(
        "/api/knowledge",
        json={
            "content": "FastAPI 是 Python 的异步 Web 框架，基于类型注解自动生成接口文档。",
            "mastery_level": 3,
            "tags": ["python", "web框架", "python"],  # 重复标签应去重
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    cleanup.append(data["id"])
    assert data["tags"] == ["python", "web框架"]
    assert data["mastery_level"] == 3

    # 标签筛选
    resp = client.get("/api/knowledge", params={"tag": "python"})
    ids = [k["id"] for k in resp.json()["items"]]
    assert data["id"] in ids

    # 关键词筛选
    resp = client.get("/api/knowledge", params={"q": "FastAPI"})
    ids = [k["id"] for k in resp.json()["items"]]
    assert data["id"] in ids

    # 无关筛选不命中
    resp = client.get("/api/knowledge", params={"tag": "不存在的标签"})
    assert data["id"] not in [k["id"] for k in resp.json()["items"]]

    # 标签自动补全
    resp = client.get("/api/tags", params={"q": "web"})
    assert "web框架" in resp.json()


def test_update_and_delete(client, cleanup):
    resp = client.post(
        "/api/knowledge",
        json={"content": "数据库索引可以加速查询。", "mastery_level": 1, "tags": ["数据库"]},
    )
    assert resp.status_code == 201, resp.text
    kid = resp.json()["id"]
    cleanup.append(kid)

    # 编辑：改内容与标签
    resp = client.put(
        f"/api/knowledge/{kid}",
        json={"content": "B-tree 索引适合等值与范围查询。", "mastery_level": 2, "tags": ["数据库", "索引"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["content"] == "B-tree 索引适合等值与范围查询。"
    assert data["tags"] == ["数据库", "索引"]

    # 删除
    assert client.delete(f"/api/knowledge/{kid}").status_code == 204
    assert client.get(f"/api/knowledge/{kid}").status_code == 404
    assert client.delete(f"/api/knowledge/{kid}").status_code == 404  # 幂等

    # 向量行已随删除清理
    cleanup.remove(kid)  # 已被删除，避免 fixture 重复删
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM embeddings WHERE source_id = %s", (kid,))
        assert cur.fetchone()[0] == 0


def test_knn_retrieval(client, cleanup):
    """语义检索：与「炖肉」相关的查询应命中美食笔记而非编程笔记。"""
    resp = client.post(
        "/api/knowledge",
        json={"content": "红烧肉做法：五花肉切块焯水，炒糖色后加酱油料酒慢炖一小时。", "mastery_level": 0, "tags": ["美食"]},
    )
    assert resp.status_code == 201, resp.text
    food_id = resp.json()["id"]
    cleanup.append(food_id)

    resp = client.post(
        "/api/knowledge",
        json={"content": "pytest 支持 fixture 与参数化测试，mark 可标记用例分组。", "mastery_level": 0, "tags": ["测试"]},
    )
    assert resp.status_code == 201, resp.text
    cleanup.append(resp.json()["id"])

    hits = search(vector=encode_query("怎么炖红烧肉？"), user_id=get_user_id(), top_k=2)
    assert hits, "向量检索无结果，嵌入可能未写入"
    assert hits[0]["source_id"] == food_id
    assert hits[0]["similarity"] > 0.3


def test_list_pagination(client, cleanup):
    """分页：total/页码正确、按更新时间倒序（同秒时 id 倒序）、超范围页返回空。"""
    tag = "分页测试专用"
    ids = []
    for content in ("分页笔记A", "分页笔记B", "分页笔记C"):
        resp = client.post(
            "/api/knowledge", json={"content": content, "mastery_level": 0, "tags": [tag]}
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    cleanup += ids

    # 第 1 页：2 条，total=3，最新创建的排最前
    resp = client.get("/api/knowledge", params={"tag": tag, "page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert [k["id"] for k in data["items"]] == [ids[2], ids[1]]

    # 第 2 页：剩 1 条
    resp = client.get("/api/knowledge", params={"tag": tag, "page": 2, "page_size": 2})
    data = resp.json()
    assert [k["id"] for k in data["items"]] == [ids[0]]

    # 超范围页：200 + 空列表
    resp = client.get("/api/knowledge", params={"tag": tag, "page": 99, "page_size": 2})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_mastery_filter_and_validation(client, cleanup):
    """掌握度等值筛选 + 非法参数 422。"""
    r3 = client.post("/api/knowledge", json={"content": "掌握度3的笔记", "mastery_level": 3, "tags": []})
    r5 = client.post("/api/knowledge", json={"content": "掌握度5的笔记", "mastery_level": 5, "tags": []})
    assert r3.status_code == 201 and r5.status_code == 201
    cleanup += [r3.json()["id"], r5.json()["id"]]

    resp = client.get("/api/knowledge", params={"mastery": 3})
    ids = [k["id"] for k in resp.json()["items"]]
    assert r3.json()["id"] in ids
    assert r5.json()["id"] not in ids

    resp = client.get("/api/knowledge", params={"mastery": 5})
    ids = [k["id"] for k in resp.json()["items"]]
    assert r5.json()["id"] in ids
    assert r3.json()["id"] not in ids

    # 非法参数由 FastAPI Query 校验拦截
    assert client.get("/api/knowledge", params={"mastery": 6}).status_code == 422
    assert client.get("/api/knowledge", params={"page": 0}).status_code == 422
    assert client.get("/api/knowledge", params={"page_size": 101}).status_code == 422


def test_semantic_search_api(client, cleanup):
    """POST /api/knowledge/search：语义查询返回命中笔记全文 + 相似度（纯检索）。"""
    resp = client.post(
        "/api/knowledge",
        json={
            "content": "清蒸鲈鱼的做法：鲈鱼处理干净，铺葱姜上锅蒸八分钟，淋蒸鱼豉油。",
            "mastery_level": 2,
            "tags": ["美食"],
        },
    )
    assert resp.status_code == 201, resp.text
    food_id = resp.json()["id"]
    cleanup.append(food_id)
    resp = client.post(
        "/api/knowledge",
        json={"content": "pytest 支持 fixture 与参数化测试，mark 可标记用例分组。", "mastery_level": 0, "tags": ["测试"]},
    )
    assert resp.status_code == 201, resp.text
    cleanup.append(resp.json()["id"])

    # 语义查询：美食查询应命中鱼笔记
    resp = client.post("/api/knowledge/search", json={"query": "怎么做清蒸鱼？", "top_k": 5})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "语义查询无结果，嵌入可能未写入"
    assert items[0]["id"] == food_id
    assert items[0]["similarity"] > 0.3
    # 每条含笔记完整字段
    for key in ("content", "mastery_level", "tags", "created_at", "updated_at", "similarity"):
        assert key in items[0]

    # 参数校验：top_k 越界 422、空/纯空格查询 422/400
    assert client.post("/api/knowledge/search", json={"query": "x", "top_k": 0}).status_code == 422
    assert client.post("/api/knowledge/search", json={"query": "x", "top_k": 51}).status_code == 422
    assert client.post("/api/knowledge/search", json={"query": ""}).status_code == 422  # min_length=1
    assert client.post("/api/knowledge/search", json={"query": "   "}).status_code == 400
