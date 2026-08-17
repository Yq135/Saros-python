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
    ids = [k["id"] for k in resp.json()]
    assert data["id"] in ids

    # 关键词筛选
    resp = client.get("/api/knowledge", params={"q": "FastAPI"})
    ids = [k["id"] for k in resp.json()]
    assert data["id"] in ids

    # 无关筛选不命中
    resp = client.get("/api/knowledge", params={"tag": "不存在的标签"})
    assert data["id"] not in [k["id"] for k in resp.json()]

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
