"""pytest 共享夹具：会话级 TestClient（全测试会话一次 lifespan，集成测试共用）。

原因：多个 TestClient 顺序启停会各自启动/停止 lifespan（任务队列 worker +
anyio BlockingPortal），跨事件循环残留导致最后一个文件 teardown 时
RuntimeError（Future attached to a different loop）。统一会话级 client 后：
嵌入模型只加载一次、worker 只启停一次、无跨 loop 问题。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # 触发 lifespan：默认用户 + 嵌入模型 + 任务队列
        yield c
