"""B 站视频下载服务：链接校验（BV 号 / b23.tv 短链）+ yt-dlp 下载（字幕/音频/视频）。

步骤 1 实现字幕下载（官方 CC 优先，B 站 AI 字幕兜底）与元信息获取；
音频/视频下载在任务框架中按需扩展。
yt-dlp 用 Python API（后续任务框架需 progress_hook 上报下载进度，subprocess 不便于回调）。

字幕决议（v0.8）：官方 CC（zh-Hans/zh-CN/zh）→ B 站 AI 字幕（ai-zh，知识区主力）→
均无则返回 None（不算失败，交由上层走音频模式）。AI 字幕同样走字幕模式（vtt 带时间戳）。
"""
import logging
import re
from pathlib import Path

import httpx
import yt_dlp

from app.config import BACKEND_DIR, settings

logger = logging.getLogger("uvicorn.error")

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 15.0

MEDIA_ROOT = BACKEND_DIR / "data" / "media"

# 人工 CC 字幕语言码候选（B 站人工字幕常见 zh-Hans）
SUB_LANGS = ["zh-Hans", "zh-CN", "zh"]
# B 站 AI 字幕语言码（自动生成，知识区主力；同样走字幕模式）
AI_SUB_LANG = "ai-zh"
SUB_SUFFIXES = (".vtt", ".srt")


class VideoDownloadError(Exception):
    """下载/校验失败：detail 为中文提示，可直接展示给用户。"""


def parse_video_ref(url: str) -> tuple[str, int | None]:
    """解析 B 站视频链接，返回 (bvid, p)。

    p 为多 P 合集的分集参数（?p=1）；无 p 返回 None（下载时按第一集处理）。
    b23.tv 短链跟随重定向后解析，跳转后的地址自带 p 参数。
    """
    url = (url or "").strip()
    m = BV_RE.search(url)
    if not m:
        if "b23.tv" in url:
            try:
                resp = httpx.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=HTTP_TIMEOUT,
                    follow_redirects=True,
                )
            except httpx.HTTPError as e:
                raise VideoDownloadError(f"短链解析失败，请检查网络或链接：{e}") from e
            url = str(resp.url)
            m = BV_RE.search(url)
            if not m:
                raise VideoDownloadError("短链解析失败：未能从 b23.tv 跳转地址中找到 BV 号（短链可能已失效）")
        else:
            raise VideoDownloadError("链接不是 B 站视频链接（需要包含 BV 号或 b23.tv 短链）")
    bvid = m.group(0)
    pm = re.search(r"[?&]p=(\d+)", url)
    return bvid, int(pm.group(1)) if pm else None


def parse_bvid(url: str) -> str:
    """从链接提取 bvid（含 p 参数与短链的完整解析见 parse_video_ref）。"""
    return parse_video_ref(url)[0]


def build_video_url(bvid: str, p: int | None) -> str:
    """构造下载用 URL：多 P 合集带 p 参数；未指定 p 时配合 noplaylist 只取第一集。"""
    base = f"https://www.bilibili.com/video/{bvid}"
    return f"{base}?p={p}" if p else base


def media_dir(bvid: str) -> Path:
    """某视频的本地媒体目录 data/media/{bvid}/。"""
    return MEDIA_ROOT / bvid


def _cookie_opt() -> dict:
    """cookie 文件存在时返回 cookiefile 参数；否则返回空（无 cookie 尝试）。"""
    f = settings.cookie_file()
    if f.exists():
        return {"cookiefile": str(f)}
    logger.warning("未找到 cookie 文件 %s，将以无 cookie 方式尝试（若 403 请提供 cookie）", f)
    return {}


def _base_opts(out_dir: Path) -> dict:
    """通用下载参数：静默输出、浏览器 UA、输出目录、cookie。"""
    return {
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": USER_AGENT},
        **_cookie_opt(),
    }


def _friendly_download_error(e: Exception) -> str:
    """把 yt-dlp 异常翻译成中文提示；403/cookie 问题单独指引。"""
    msg = str(e)
    if "403" in msg:
        return "下载被 B 站拒绝（403）：cookie 缺失或已失效，请更新 backend/data/cookies.txt 后重试"
    if "cookie" in msg.lower():
        return f"cookie 校验失败，请更新 {settings.cookie_file()} 后重试（{msg}）"
    return f"B 站下载失败：{msg}"


def fetch_video_info(url: str) -> tuple[str, int | None, dict]:
    """获取视频元信息（标题等），仅请求不下载。返回 (bvid, p, info)。失败抛 VideoDownloadError。"""
    bvid, p = parse_video_ref(url)
    opts = _base_opts(media_dir(bvid))
    if p is None:
        opts["noplaylist"] = True  # 多 P 合集未指定分集时只取第一集（首期单 P）
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(build_video_url(bvid, p), download=False)
    except yt_dlp.utils.DownloadError as e:
        raise VideoDownloadError(_friendly_download_error(e)) from e
    return bvid, p, info


def download_subtitle(url: str) -> tuple[str, int | None, str, str | None, Path | None]:
    """下载字幕到 data/media/{bvid}/：官方 CC 优先，B 站 AI 字幕兜底。

    返回 (bvid, p, 标题, mode, 字幕文件路径)。mode ∈ CC/AI；无任何字幕时 mode 与
    路径均为 None（不算失败，交由上层决定走音频模式）。下载/解析失败抛 VideoDownloadError。
    """
    bvid, p = parse_video_ref(url)
    out_dir = media_dir(bvid)
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        **_base_opts(out_dir),
        "skip_download": True,
        "writesubtitles": True,  # 人工 CC 字幕
        "writeautomaticsub": True,  # B 站 AI 字幕（ai-zh，自动生成）
        "subtitleslangs": SUB_LANGS + [AI_SUB_LANG],
        "subtitlesformat": "vtt/best",
    }
    if p is None:
        opts["noplaylist"] = True  # 多 P 合集未指定分集时只处理第一集（首期单 P）
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(build_video_url(bvid, p), download=True)
    except yt_dlp.utils.DownloadError as e:
        raise VideoDownloadError(_friendly_download_error(e)) from e

    title = (info or {}).get("title") or ""
    mode, subtitle = _find_subtitle(out_dir)
    if subtitle is None:
        logger.info("视频 %s 无任何字幕（后续走音频模式）", bvid)
    return bvid, p, title, mode, subtitle


def _find_subtitle(d: Path) -> tuple[str | None, Path | None]:
    """在媒体目录中找字幕：人工 CC 优先（mode=CC），其次 AI 字幕（mode=AI），无则 (None, None)。

    文件名可能带语言后缀：人工如 xx.zh-Hans.vtt，AI 为 xx.ai-zh.vtt。
    """
    cc = ai = None
    for suffix in SUB_SUFFIXES:
        for f in sorted(d.glob(f"*{suffix}")):
            if AI_SUB_LANG in f.stem:
                ai = ai or f
            else:
                cc = cc or f
    if cc:
        return "CC", cc
    if ai:
        return "AI", ai
    return None, None


def media_name(bvid: str, p: int | None, ext: str) -> str:
    """本地媒体文件名：单 P 为 {bvid}.{ext}，多 P 指定分集为 {bvid}_p{p}.{ext}。"""
    return f"{bvid}_p{p}.{ext}" if p else f"{bvid}.{ext}"


def download_audio(url: str, progress_hook=None) -> Path:
    """下载 audio-only 流（B 站 DASH 音视频分离，比下载视频再提取更快），返回本地文件路径。

    已存在同名媒体文件时跳过下载（重试断点续跑）。失败抛 VideoDownloadError。
    """
    bvid, p = parse_video_ref(url)
    out_dir = media_dir(bvid)
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = _find_existing_media(out_dir, bvid, p)
    if existing is not None:
        logger.info("音频已存在，跳过下载：%s", existing)
        return existing
    opts = {
        **_base_opts(out_dir),
        "format": "ba/b",
        "outtmpl": str(out_dir / media_name(bvid, p, "%(ext)s")),
        "noplaylist": True,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(build_video_url(bvid, p), download=True)
    except yt_dlp.utils.DownloadError as e:
        raise VideoDownloadError(_friendly_download_error(e)) from e
    audio = _find_existing_media(out_dir, bvid, p)
    if audio is None:
        raise VideoDownloadError("音频下载失败：未找到下载产物")
    return audio


def download_video(url: str, progress_hook=None) -> Path:
    """下载视频（清晰度上限 MAX_VIDEO_HEIGHT），音视频流合并输出 mp4，返回本地文件路径。

    DASH 流（高清）合并后为 mp4；低清单文件（flv）经 ffmpeg 转封装为 mp4，
    保证浏览器 video 标签可播放。已存在则跳过下载。失败抛 VideoDownloadError。
    """
    bvid, p = parse_video_ref(url)
    out_dir = media_dir(bvid)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / media_name(bvid, p, "mp4")
    if target.exists():
        logger.info("视频已存在，跳过下载：%s", target)
        return target
    opts = {
        **_base_opts(out_dir),
        "format": (
            f"bv*[height<={settings.max_video_height}]+ba/"
            f"b[height<={settings.max_video_height}]/b"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / media_name(bvid, p, "%(ext)s")),
        "noplaylist": True,
        # 低清单文件（flv）转封装为 mp4，保证浏览器可播
        # 注意：yt-dlp 内部会自动补 PP 后缀，key 只写 FFmpegVideoRemuxer
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(build_video_url(bvid, p), download=True)
    except yt_dlp.utils.DownloadError as e:
        raise VideoDownloadError(_friendly_download_error(e)) from e
    if not target.exists():
        raise VideoDownloadError("视频下载失败：未找到合并后的 mp4 产物（可重试）")
    return target


def _find_existing_media(out_dir: Path, bvid: str, p: int | None) -> Path | None:
    """查找已下载的媒体文件（音频/视频，排除字幕文件），用于断点续跑跳过。"""
    prefix = f"{bvid}_p{p}" if p else bvid
    for f in sorted(out_dir.glob(f"{prefix}*")):
        if f.suffix.lower() not in SUB_SUFFIXES:
            return f
    return None
