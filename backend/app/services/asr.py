"""ASR 转写服务：自建 mlx-qwen3-asr（OpenAI 兼容 /v1/audio/transcriptions，verbose_json）。

音频模式兜底流程：下载音频流 → ffmpeg 约 5 分钟切片（-c copy，无重编码）→
逐片转写 → segments（start/end/text）按片偏移拼接 → 与字幕模式共用大纲/出题流程。
ASR 输出带精确时间戳，锚点粒度与字幕模式一致（优于原 omni 粗粒度方案）。
"""
import logging
import subprocess
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")

SEGMENT_SECONDS = 300  # 切片时长（约 5 分钟/片，决议）
TRANSCRIBE_TIMEOUT = 600.0  # 单片转写超时（1.7B MLX 约 0.2-0.5 RTF，留足余量）
MAX_RETRIES = 1


class ASRError(Exception):
    """转写失败：detail 为中文提示。"""


def split_audio(path: Path, out_dir: Path) -> list[Path]:
    """ffmpeg 按约 5 分钟切片（流拷贝不重编码，秒级完成），返回切片文件列表（按序）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("seg_*.m4a"):
        old.unlink()  # 重试时清掉旧切片
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
        "-c", "copy", str(out_dir / "seg_%03d.m4a"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ASRError(f"音频切片失败：{proc.stderr.strip()[-300:]}")
    slices = sorted(out_dir.glob("seg_*.m4a"))
    if not slices:
        raise ASRError("音频切片失败：未产生切片文件")
    return slices


def _post_transcribe(path: Path, language: str | None) -> tuple[list[dict], float]:
    """单次转写请求 → (segments, duration)；HTTP/网络异常抛出（由 transcribe_file 兜底）。"""
    data: dict = {"model": settings.asr_model, "response_format": "verbose_json"}
    if language:
        data["language"] = language
    with path.open("rb") as f:
        resp = httpx.post(
            f"{settings.asr_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.asr_api_key}"},
            files={"file": (path.name, f, "audio/mp4")},
            data=data,
            timeout=TRANSCRIBE_TIMEOUT,
        )
    resp.raise_for_status()
    payload = resp.json()
    segments = [
        {"start_sec": float(s.get("start", 0.0)), "end_sec": float(s.get("end", 0.0)),
         "text": (s.get("text") or "").strip()}
        for s in (payload.get("segments") or [])
        if (s.get("text") or "").strip()
    ]
    if not segments:
        raise ASRError("ASR 转写失败：返回结果中没有带时间戳的分段（segments）")
    return segments, float(payload.get("duration") or 0.0)


def transcribe_file(path: Path) -> tuple[list[dict], float]:
    """转写单个音频文件 → (segments, duration)。

    兜底策略：服务端自动语言检测对部分音频（实测日文歌）会 500，
    此时改用 language=zh 重试（多语言音频仍按原语言转写，已验证）；
    网络抖动重试一次。
    """
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _post_transcribe(path, None)
        except httpx.HTTPStatusError as e:  # 服务端 4xx/5xx：自动检测 bug，语言兜底
            logger.warning("ASR 服务端错误（%s），改用 language=zh 兜底", e.response.status_code)
            try:
                return _post_transcribe(path, "zh")
            except (httpx.HTTPError, ValueError) as e2:
                raise ASRError(f"ASR 转写请求失败（language 兜底也失败）：{e2}") from e2
        except httpx.HTTPError as e:  # 网络抖动：重试
            last_err = e
            if attempt < MAX_RETRIES:
                logger.warning("ASR 请求失败，重试：%s", e)
                continue
            break
    raise ASRError(f"ASR 转写请求失败：{last_err}")


def transcribe_audio(path: Path, out_dir: Path, progress_cb=None) -> list[dict]:
    """切片 + 逐片转写 + 时间戳拼接 → [{start_sec, end_sec, text}]（相对原音频起点）。

    progress_cb(done, total) 用于任务进度上报（可选）。
    """
    slices = split_audio(path, out_dir)
    total = len(slices)
    segments: list[dict] = []
    offset = 0.0  # 切片用 -c copy，片边界取关键帧，实际偏移用 ASR 返回的 duration 累加
    for i, seg in enumerate(slices):
        parts, duration = transcribe_file(seg)
        for s in parts:
            s["start_sec"] += offset
            s["end_sec"] += offset
        segments.extend(parts)
        offset += duration
        logger.info("ASR 转写 %d/%d 片完成（%s，累计 %d 段）", i + 1, total, seg.name, len(segments))
        if progress_cb:
            progress_cb(i + 1, total)
    return segments
