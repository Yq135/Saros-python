"""设置服务：.env 读写 + 进程内热刷新 + B 站 cookie 校验。

设置页保存后写 backend/.env（保留注释与其他行），并热刷新进程内 settings 对象：
LLM 客户端重建（base_url/api_key 即时生效）；ASR/cookie/开关等每次调用实时读
settings 字段，天然即时生效——全部配置**无需重启**。
"""
import logging
import re
from pathlib import Path

import httpx

from app import llm
from app.config import BACKEND_DIR, settings

logger = logging.getLogger("uvicorn.error")

ENV_FILE = BACKEND_DIR / ".env"
ENV_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")

# 设置页可编辑项：env 键名（大写）→ settings 字段名（snake_case）
EDITABLE_KEYS = {
    "LLM_BASE_URL": "llm_base_url",
    "LLM_API_KEY": "llm_api_key",
    "LLM_MODEL": "llm_model",
    "ASR_BASE_URL": "asr_base_url",
    "ASR_API_KEY": "asr_api_key",
    "ASR_MODEL": "asr_model",
    "COOKIE_PATH": "cookie_path",
    "SKIP_SUBTITLE": "skip_subtitle",
}

# 布尔项：写 .env 时转 True/False 字符串
BOOL_KEYS = {"SKIP_SUBTITLE"}

BILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def update_env(updates: dict[str, str]) -> None:
    """更新 .env 文件：已有 KEY 行覆盖，缺失键追加到末尾；保留注释与其他行。"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        m = ENV_RE.match(line.strip())
        key = m.group(1) if m else None
        if key and key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply_settings(fields: dict[str, object]) -> None:
    """保存设置：写 .env 并热刷新进程内配置（LLM 客户端重建）。"""
    env_updates: dict[str, str] = {}
    for env_key, field in EDITABLE_KEYS.items():
        if field not in fields:
            continue
        value = fields[field]
        # 写 .env 均为字符串；布尔项转 "True"/"False"（不能先转 str 再 bool，会得到恒 True），
        # 非布尔项转 str。注意三元嵌套必须加括号：不加会解析成「值为真 → 恒 "True"」
        env_updates[env_key] = (
            ("True" if value else "False") if env_key in BOOL_KEYS else str(value)
        )
        setattr(settings, field, bool(value) if env_key in BOOL_KEYS else value)
    update_env(env_updates)
    llm.reset_client()
    logger.info("设置已保存并热刷新：%s", ", ".join(env_updates))


def read_cookie() -> str:
    """读取 cookie 文件内容（不存在返回空串）。"""
    f = settings.cookie_file()
    return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


def write_cookie(content: str) -> None:
    """保存 cookie 内容到配置的文件（Netscape 格式原文）。"""
    f = settings.cookie_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("cookie 已保存：%s", f)


def _parse_netscape_cookie(path: Path) -> dict[str, str]:
    """解析 Netscape 格式 cookie（yt-dlp cookiefile 格式），返回 name=value 映射。"""
    cookies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def check_cookie() -> dict:
    """校验 B 站 cookie 有效性：调 nav 接口，返回 {valid, uname?}。"""
    f = settings.cookie_file()
    if not f.exists():
        return {"valid": False, "detail": f"未找到 cookie 文件：{f}"}
    cookies = _parse_netscape_cookie(f)
    if not cookies:
        return {"valid": False, "detail": "cookie 文件为空或格式不正确（应为 Netscape 格式）"}
    try:
        resp = httpx.get(
            BILI_NAV_URL,
            cookies=cookies,
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
            timeout=15.0,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"valid": False, "detail": f"请求 B 站接口失败：{e}"}
    if data.get("code") == 0 and (data.get("data") or {}).get("isLogin"):
        user = data["data"]
        return {"valid": True, "uname": user.get("uname"), "uid": user.get("mid")}
    return {"valid": False, "detail": f"cookie 无效：code={data.get('code')}，{data.get('message')}"}
