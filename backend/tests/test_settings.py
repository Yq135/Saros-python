"""M5 设置模块测试：.env 读写/热刷新单测 + cookie 校验单测 + 配置查询集成。

写入类测试全部重定向到 tmp_path（monkeypatch ENV_FILE / cookie_path），
绝不触碰真实 backend/.env；真实 PUT /api/settings 不在测试中调用（会改用户配置）。
"""
import httpx
import pytest

from app import llm
from app.config import settings as app_settings
from app.services import settings_service


# client 为 conftest.py 共享的会话级夹具（全测试会话一次 lifespan）


# ---------------- .env 读写单测 ----------------

def test_update_env_overwrite_append_keep_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# 注释行保留\nLLM_MODEL=old-model\nPG_HOST=127.0.0.1\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings_service, "ENV_FILE", env)

    settings_service.update_env({"LLM_MODEL": "new-model", "SKIP_SUBTITLE": "True"})
    text = env.read_text(encoding="utf-8")

    assert "# 注释行保留" in text  # 注释保留
    assert "LLM_MODEL=new-model" in text  # 已有键覆盖
    assert "PG_HOST=127.0.0.1" in text  # 无关键不动
    assert "SKIP_SUBTITLE=True" in text  # 缺失键追加


def test_update_env_creates_missing_file(tmp_path, monkeypatch):
    env = tmp_path / "not-exists.env"
    monkeypatch.setattr(settings_service, "ENV_FILE", env)

    settings_service.update_env({"LLM_API_KEY": "sk-test"})
    assert "LLM_API_KEY=sk-test" in env.read_text(encoding="utf-8")


def test_apply_settings_hot_reload(tmp_path, monkeypatch):
    """保存设置：写 .env + 进程内热刷新 + LLM 客户端重建（不重启生效）。"""
    env = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "ENV_FILE", env)
    reset_calls: list = []
    monkeypatch.setattr(llm, "reset_client", lambda: reset_calls.append(1))
    # 被修改的 settings 字段也做 monkeypatch，测试结束自动还原真实值
    monkeypatch.setattr(app_settings, "skip_subtitle", False)
    monkeypatch.setattr(app_settings, "llm_model", "before-model")

    settings_service.apply_settings({"skip_subtitle": True, "llm_model": "after-model"})
    text = env.read_text(encoding="utf-8")

    assert "SKIP_SUBTITLE=True" in text
    assert "LLM_MODEL=after-model" in text
    assert app_settings.skip_subtitle is True  # 进程内即时生效
    assert app_settings.llm_model == "after-model"
    assert len(reset_calls) == 1  # LLM 客户端重建一次


# ---------------- cookie 校验单测 ----------------

def test_parse_netscape_cookie(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc123\n"
        "短行会被跳过\n",
        encoding="utf-8",
    )
    assert settings_service._parse_netscape_cookie(f) == {"SESSDATA": "abc123"}


def test_check_cookie_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "cookie_path", str(tmp_path / "no-cookie.txt"))
    result = settings_service.check_cookie()
    assert result["valid"] is False
    assert "未找到" in result["detail"]


def test_check_cookie_invalid_format(tmp_path, monkeypatch):
    f = tmp_path / "cookies.txt"
    f.write_text("不是 Netscape 格式\n", encoding="utf-8")
    monkeypatch.setattr(app_settings, "cookie_path", str(f))
    result = settings_service.check_cookie()
    assert result["valid"] is False
    assert "格式不正确" in result["detail"]


def test_check_cookie_login_ok(tmp_path, monkeypatch):
    f = tmp_path / "cookies.txt"
    f.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tok123\n", encoding="utf-8")
    monkeypatch.setattr(app_settings, "cookie_path", str(f))

    class FakeResp:
        def json(self):
            return {"code": 0, "data": {"isLogin": True, "uname": "测试用户", "mid": 123}}

    monkeypatch.setattr(settings_service.httpx, "get", lambda *a, **kw: FakeResp())
    result = settings_service.check_cookie()
    assert result["valid"] is True
    assert result["uname"] == "测试用户"
    assert result["uid"] == 123


def test_check_cookie_not_logged_in(tmp_path, monkeypatch):
    f = tmp_path / "cookies.txt"
    f.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tbad\n", encoding="utf-8")
    monkeypatch.setattr(app_settings, "cookie_path", str(f))

    class FakeResp:
        def json(self):
            return {"code": -101, "message": "账号未登录"}

    monkeypatch.setattr(settings_service.httpx, "get", lambda *a, **kw: FakeResp())
    result = settings_service.check_cookie()
    assert result["valid"] is False
    assert "未登录" in result["detail"]


def test_check_cookie_network_error(tmp_path, monkeypatch):
    f = tmp_path / "cookies.txt"
    f.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tok\n", encoding="utf-8")
    monkeypatch.setattr(app_settings, "cookie_path", str(f))

    def boom(*a, **kw):
        raise httpx.HTTPError("连接超时")

    monkeypatch.setattr(settings_service.httpx, "get", boom)
    result = settings_service.check_cookie()
    assert result["valid"] is False
    assert "失败" in result["detail"]


# ---------------- 集成（只读，不改任何配置） ----------------

def test_get_settings_readonly(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("llm_base_url", "llm_api_key", "llm_model",
                "asr_base_url", "asr_model", "cookie_path", "skip_subtitle"):
        assert key in data
