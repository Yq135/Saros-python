"""字幕文件解析：srt / vtt → 字幕段列表 [{start_sec, end_sec, text}]。

时间戳格式：srt 用逗号毫秒（HH:MM:SS,mmm），vtt 用点毫秒（HH:MM:SS.mmm）。
vtt 可能带 WEBVTT 头、NOTE 注释、样式标签（<c>、<b> 等）与行内时间轴。
输出按句末标点合并相邻短段：减少 LLM 输入行数、改善详情页阅读。
"""
import re
from pathlib import Path

TS_RE = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")
TAG_RE = re.compile(r"<[^>]+>")
SENTENCE_END = "。！？…"
MAX_SEG_CHARS = 50  # 无标点字幕（B 站 AI 字幕常见）的兜底分段长度


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_subtitle(path: Path) -> list[dict]:
    """解析字幕文件为 [{start_sec, end_sec, text}]。

    srt：块 = 序号 / 时间轴 / 文本；vtt：头与 NOTE 在首个时间轴前（当前块为空时跳过）。
    解析失败不抛异常（返回空列表，交由上层走音频模式兜底）。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    blocks: list[dict] = []
    current: dict | None = None
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            current = None  # 空行 = 块结束
            continue
        if " --> " in line:
            m1 = TS_RE.search(line)
            tail = line.split(" --> ", 1)[1]
            m2 = TS_RE.search(tail)
            if m1 and m2:
                current = {
                    "start_sec": _ts_to_sec(*m1.groups()),
                    "end_sec": _ts_to_sec(*m2.groups()),
                    "text": "",
                }
                blocks.append(current)
                # vtt 行内文本（时间轴后跟内容）
                after = TS_RE.sub("", tail, count=1).strip()
                if after:
                    current["text"] = after
            continue
        if current is None:
            continue  # 跳过 WEBVTT 头 / NOTE / srt 序号行
        current["text"] = f"{current['text']} {line}".strip()

    out: list[dict] = []
    for b in blocks:
        text = TAG_RE.sub("", b["text"]).strip()
        if text:
            out.append({"start_sec": b["start_sec"], "end_sec": b["end_sec"], "text": text})
    return _merge_sentences(out)


def _merge_sentences(blocks: list[dict]) -> list[dict]:
    """相邻短段合并：出现句末标点（。！？…）或累计超 MAX_SEG_CHARS 字即分段。

    B 站 AI 字幕经常整段无标点，兜底长度保证不会合并出超长段。
    """
    merged: list[dict] = []
    buf: dict | None = None
    for b in blocks:
        if buf is None:
            buf = dict(b)
        else:
            buf["text"] += b["text"]
            buf["end_sec"] = b["end_sec"]
        if any(ch in buf["text"] for ch in SENTENCE_END) or len(buf["text"]) >= MAX_SEG_CHARS:
            merged.append(buf)
            buf = None
    if buf is not None:
        merged.append(buf)
    return merged
