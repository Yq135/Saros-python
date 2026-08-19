"""全部中文 Prompt 模板（语气遵循「温柔陪伴」：不说教、不冷冰冰、像陪读伙伴）。

模块一：回答合成/推荐标签；模块二：网页出题（题目+标签一次生成）；模块三的 Prompt 到对应里程碑再补充。
"""
import json
import re

# 回答合成：温柔陪伴语气 + 引用/冲突/诚实规则
ANSWER_SYSTEM = """你是 Saros，一个高度专业的个人知识沉淀与拓展助手。语气像陪读伙伴：温柔、不冷冰冰，回答清晰有温度。你的核心任务是结合用户的【沉淀笔记】和【实时网络搜索结果】，为用户提供准确、有深度且易于理解的答案。

回答规则：
1. 用中文 Markdown 输出，结构清晰（可用小标题、列表）。
2. 引用联网搜索资料回答的关键事实、数据或观点时，在对应句末用 [n] 标注，n 与「搜索结果」列表中的编号一致。
3. 用户沉淀笔记的权威性高于搜索结果：两者冲突时以沉淀笔记为基础框架进行解答，并温和地说明差异。若笔记中信息不全，再使用【网络搜索结果】进行补充、拓展和最新事实核查。
4. 诚实原则：如果提供的参考资料中完全没有相关信息，或者信息不足以回答问题，请直接回复：“抱歉，在您的个人笔记和网络资料中均未找到相关信息。”，绝不编造事实或来源，不要尝试强行作答。
5. 本轮若没有搜索结果，仅基于沉淀笔记回答，并注明「本轮联网搜索不可用」。
6. 若沉淀笔记与搜索结果都不足，直接说明无法回答，不要勉强。
7. 拓展延伸（可选）：如果网络资料提供了笔记中没有的前沿观点或最新动态，请在此处补充说明，帮助用户拓宽认知。"""


def build_answer_messages(
    *,
    question: str,
    sources: list[dict],
    knowledge: list[dict],
    history: str = "",
) -> list[dict]:
    """组装回答请求：system + 用户消息（历史上下文 + 沉淀笔记 + 搜索结果 + 问题）。"""
    parts: list[str] = []
    if history:
        parts.append("## 本轮之前的对话（仅供理解上下文，不要引用其中的编号）\n" + history)
    if knowledge:
        parts.append(
            "## 你的沉淀笔记（权威，优先采信）\n"
            + "\n\n".join(f"- 笔记{k['id']}：{k['content']}" for k in knowledge)
        )
    if sources:
        parts.append(
            "## 搜索结果\n"
            + "\n".join(
                f"[{i}] {s['title']}（{s['url']}）\n{s['snippet']}"
                for i, s in enumerate(sources, 1)
            )
        )
    else:
        parts.append("（本轮联网搜索不可用，没有搜索结果。）")
    parts.append(f"## 用户问题\n{question}")
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


# 推荐标签：仅要求 JSON 数组输出，便于解析
TAG_SYSTEM = """你是标签生成器。根据用户的提问与回答，提炼 3-5 个中文标签。
要求：每个标签 2-6 个汉字；覆盖主题关键词；只输出 JSON 数组（如 ["装饰器", "Python"]），不要输出其他内容。"""


def build_tag_messages(*, question: str, answer: str) -> list[dict]:
    return [
        {"role": "system", "content": TAG_SYSTEM},
        {"role": "user", "content": f"问题：{question}\n\n回答：{answer}"},
    ]


def parse_tags(text: str) -> list[str]:
    """解析标签生成结果：JSON 数组优先，兜底提取引号内文本；失败返回空列表。"""
    text = (text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            tags = [str(t).strip() for t in data if str(t).strip()]
            if tags:
                return tags[:5]
    except (json.JSONDecodeError, TypeError):
        pass
    # 兜底：提取 "…"、「…」、'…' 中的文本
    return re.findall(r'["「\']([^"」\']{1,20})["」\']', text)[:5]


# ---------------------------------------------------------------
# 模块二：网页出题（题目 + 推荐标签一次生成，JSON 结构化输出）
# ---------------------------------------------------------------

QUESTION_SYSTEM = """你是出题助手。根据给定网页文章的正文，生成 3-5 道「读完后能检验是否掌握内容」的开放式问题，每题附参考答案，并提炼 3-5 个中文推荐标签。

要求：
1. 问题覆盖文章的核心概念与关键论证，避免琐碎细节；每道题都应能用文章内容完整回答。
2. 参考答案具体、准确，直接引述文章要点，2-5 句话为宜。
3. 标签为 2-6 个汉字的中文词，覆盖主题关键词。
4. 只输出 JSON，格式：{"questions": [{"question": "题干", "reference_answer": "参考答案"}], "tags": ["标签1", "标签2"]}，不要输出其他内容。
5. 若正文过短或信息不足，questions 可为空数组。"""


def build_question_messages(*, title: str, content: str) -> list[dict]:
    """组装出题请求：system + 用户消息（标题 + 正文）。"""
    parts: list[str] = []
    if title:
        parts.append(f"文章标题：{title}")
    parts.append(f"文章正文：\n{content}")
    return [
        {"role": "system", "content": QUESTION_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _strip_code_fence(text: str) -> str:
    """去除模型输出常见的外层 ```json ... ``` 包裹。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_questions(text: str) -> tuple[list[dict], list[str]]:
    """解析出题结果：(题目列表, 标签列表)。解析失败返回 ([], []) —— 文章仍保留，题目可后补。"""
    try:
        data = json.loads(_strip_code_fence(text))
    except (json.JSONDecodeError, TypeError):
        return [], []
    questions: list[dict] = []
    tags: list[str] = []
    if isinstance(data, dict):
        if isinstance(data.get("questions"), list):
            for q in data["questions"]:
                if not isinstance(q, dict):
                    continue
                question = str(q.get("question") or "").strip()
                answer = str(q.get("reference_answer") or "").strip()
                if question:
                    questions.append({"question": question, "reference_answer": answer})
        if isinstance(data.get("tags"), list):
            tags = [str(t).strip() for t in data["tags"] if str(t).strip()][:5]
    return questions[:5], tags


# ---------------------------------------------------------------
# 模块三：B 站视频（大纲 + 出题 + 推荐标签，JSON 结构化输出）
# ---------------------------------------------------------------

VIDEO_OUTLINE_SYSTEM = """你是视频学习大纲整理助手。根据带时间戳的字幕内容，为视频整理「学习大纲」：让人知道哪个时间点讲什么内容（大致框架，不是逐句笔记）。

要求：
1. 用中文输出 JSON 数组，每项为 {"time": "[MM:SS]", "title": "小节标题", "summary": "小节要点"}。
2. time 是该小节开始的时间点，必须使用字幕中真实存在的时间点（取该小节第一条字幕的时间）；MM 或 SS 不足两位补零，如 [03:05]。
3. title 概括小节主题，8-20 字；summary 概述小节要点，40-80 字。
4. 按内容自然分段，小节数量约 5-15 个（视频很长可适当更多），顺序与时间一致。
5. 只输出 JSON 数组，不要输出其他内容。"""

VIDEO_QUESTIONS_SYSTEM = """你是视频学习出题助手。根据带时间戳的字幕内容，生成 5-8 道「看完视频后应能掌握」的开放式问题，每题附参考答案与关联时间点。

要求：
1. 用中文输出 JSON 数组，每项为 {"question": "题干", "answer": "参考答案", "time": "[MM:SS]"}。
2. 问题考察对内容的理解与运用，而非记忆琐碎细节；覆盖视频的核心内容。
3. time 是该题对应的视频时间点，必须取自字幕中真实存在的时间点（取相关内容的字幕时间）。
4. 参考答案具体、准确，60-150 字。
5. 只输出 JSON 数组，不要输出其他内容。"""

VIDEO_TAG_SYSTEM = """你是标签生成器。根据视频标题与大纲，提炼 3-5 个中文标签。
要求：每个标签 2-8 个字符，中文为主，可包含常见技术专有名词（如 Python、RAG、Vue）；覆盖主题关键词；只输出 JSON 数组（如 ["装饰器", "Python"]），不要输出其他内容。"""


def build_video_outline_messages(*, title: str, subtitle_text: str) -> list[dict]:
    """组装大纲请求：system + 用户消息（标题 + 带时间戳的字幕全文）。"""
    parts: list[str] = []
    if title:
        parts.append(f"视频标题：{title}")
    parts.append(f"字幕内容（每行格式：[MM:SS] 文本）：\n{subtitle_text}")
    return [
        {"role": "system", "content": VIDEO_OUTLINE_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_video_question_messages(*, title: str, subtitle_text: str) -> list[dict]:
    """组装出题请求：system + 用户消息（标题 + 带时间戳的字幕全文）。"""
    parts: list[str] = []
    if title:
        parts.append(f"视频标题：{title}")
    parts.append(f"字幕内容（每行格式：[MM:SS] 文本）：\n{subtitle_text}")
    return [
        {"role": "system", "content": VIDEO_QUESTIONS_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_video_tag_messages(*, title: str, outline_text: str) -> list[dict]:
    """组装推荐标签请求：system + 用户消息（标题 + 大纲文本）。"""
    parts: list[str] = []
    if title:
        parts.append(f"视频标题：{title}")
    parts.append(f"视频大纲：\n{outline_text}")
    return [
        {"role": "system", "content": VIDEO_TAG_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def mmss_to_sec(text: str) -> float | None:
    """[MM:SS] 文本转秒数（MM 可为 1-3 位，超一小时视频）；无法解析返回 None。"""
    m = re.search(r"(\d{1,3}):(\d{2})", text or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_video_outline(text: str) -> list[dict]:
    """解析大纲 JSON 数组 → [{time_sec, title, summary}]；time 由 [MM:SS] 转秒，解析失败返回空。"""
    try:
        data = json.loads(_strip_code_fence(text))
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            sec = mmss_to_sec(str(item.get("time") or ""))
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if sec is None or not title:
                continue
            out.append({"time_sec": sec, "title": title, "summary": summary})
    return out


def parse_video_questions(text: str) -> list[dict]:
    """解析题目 JSON 数组 → [{question, answer, time_sec}]；time 转秒（失败为 None），最多 8 题。"""
    try:
        data = json.loads(_strip_code_fence(text))
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if not q:
                continue
            out.append({"question": q, "answer": a, "time_sec": mmss_to_sec(str(item.get("time") or ""))})
    return out[:8]
