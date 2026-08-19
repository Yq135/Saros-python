"""M5 设置页 API：LLM / ASR / B 站 cookie / 视频开关。

GET  /api/settings                  当前配置（api_key 明文回显，本地单用户）
PUT  /api/settings                  保存配置（写 .env + 进程内热刷新，无需重启）
GET  /api/settings/cookie           cookie 文件内容
PUT  /api/settings/cookie           保存 cookie 内容到文件
POST /api/settings/cookie/check     校验 cookie 有效性（B 站 nav 接口）
"""
from fastapi import APIRouter

from app import schemas
from app.config import settings
from app.services import settings_service

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=schemas.SettingsOut)
def get_settings():
    return schemas.SettingsOut(
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
        asr_base_url=settings.asr_base_url,
        asr_api_key=settings.asr_api_key,
        asr_model=settings.asr_model,
        cookie_path=settings.cookie_path,
        skip_subtitle=settings.skip_subtitle,
    )


@router.put("/settings", response_model=schemas.SettingsOut)
def update_settings(payload: schemas.SettingsUpdate):
    """保存设置：仅更新传入的字段，写 .env 并热刷新（全部配置即时生效，无需重启）。"""
    fields = payload.model_dump(exclude_none=True)
    settings_service.apply_settings(fields)
    return get_settings()


@router.get("/settings/cookie", response_model=schemas.CookieContentIn)
def get_cookie():
    return schemas.CookieContentIn(content=settings_service.read_cookie())


@router.put("/settings/cookie", response_model=schemas.CookieCheckOut)
def save_cookie(payload: schemas.CookieContentIn):
    """保存 cookie 内容并立即校验，返回有效性（登录用户名）。"""
    settings_service.write_cookie(payload.content)
    return schemas.CookieCheckOut(**settings_service.check_cookie())


@router.post("/settings/cookie/check", response_model=schemas.CookieCheckOut)
def check_cookie():
    return schemas.CookieCheckOut(**settings_service.check_cookie())
