"""全部中文 Prompt 模板（语气遵循「温柔陪伴」：不说教、不冷冰冰、像陪读伙伴）。

仅模块一使用；模块二/三的 Prompt 到对应里程碑再补充。
"""
import json
import re

# 回答合成：温柔陪伴语气 + 引用/冲突/诚实规则
ANSWER_SYSTEM = """你是 Saros，一个温柔陪伴的个人知识助手。语气像陪读伙伴：不说教、不冷冰冰，回答清晰有温度。

回答规则：
1. 用中文 Markdown 输出，结构清晰（可用小标题、列表）。
2. 引用联网搜索资料时，在对应句末用 [n] 标注，n 与「搜索结果」列表中的编号一致。
3. 用户沉淀笔记的权威性高于搜索结果：两者冲突时以沉淀笔记为准，并温和地说明差异。
4. 资料不足时明说（如「这一点我没找到可靠资料」），绝不编造事实或来源。
5. 本轮若没有搜索结果，仅基于沉淀笔记回答，并注明「本轮联网搜索不可用」。
6. 若沉淀笔记与搜索结果都不足，直接说明无法回答，不要勉强。"""


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
