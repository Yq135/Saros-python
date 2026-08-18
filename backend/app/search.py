"""联网搜索层：SearchProvider 接口 + DDG 主力 + Bing/百度抓取兜底。

全部免费源；合并去重由 search_web() 统一处理（按配置顺序逐个尝试，
前序源结果不足时由后序源补齐，按 URL 去重）。
"""
import html
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from ddgs import DDGS

from app.config import settings

logger = logging.getLogger("uvicorn.error")

SEARCH_TIMEOUT = 10.0  # 抓取类兜底源超时（秒）；ddgs 用构造参数
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(ABC):
    """搜索源接口：返回 SearchResult 列表；失败抛异常（由编排层捕获降级）。"""

    name: str = ""

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class DDGProvider(SearchProvider):
    """DuckDuckGo（ddgs 库，主力源）。"""

    name = "ddgs"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        results = DDGS(timeout=SEARCH_TIMEOUT).text(query, max_results=max_results)
        out: list[SearchResult] = []
        for r in results:
            url = r.get("href") or r.get("url") or ""
            if not url:
                continue
            out.append(
                SearchResult(
                    title=(r.get("title") or "")[:200],
                    url=url,
                    snippet=(r.get("body") or r.get("snippet") or "")[:500],
                )
            )
        return out


class _HTMLSearchProvider(SearchProvider):
    """抓取类搜索源基类：httpx 抓结果页 + 正则解析标题/链接/摘要（尽力而为的兜底）。"""

    base_url = ""

    def _fetch(self, params: dict) -> str | None:
        try:
            resp = httpx.get(
                self.base_url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=SEARCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001 — 兜底源，任何网络问题都记为失败
            logger.warning("搜索源 %s 抓取失败: %s", self.name, e)
            return None

    @staticmethod
    def _strip_tags(text: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


# Bing 结果块：<li class="b_algo"> 内 <h2><a href>标题</a></h2> + <p>摘要</p>
_BING_ITEM_RE = re.compile(
    r'<li class="b_algo".*?<h2><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'(?:<p[^>]*>(.*?)</p>)?',
    re.S,
)


class BingProvider(_HTMLSearchProvider):
    name = "bing"

    base_url = "https://www.bing.com/search"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        text = self._fetch({"q": query})
        if not text:
            return []
        out: list[SearchResult] = []
        for m in _BING_ITEM_RE.finditer(text):
            title = self._strip_tags(m.group(2))
            snippet = self._strip_tags(m.group(3) or "")
            if title:
                out.append(SearchResult(title=title[:200], url=m.group(1), snippet=snippet[:500]))
            if len(out) >= max_results:
                break
        return out


# 百度结果块：<h3> 内 <a href>标题</a>；摘要 class 前缀 content-right_（class 常变，前缀匹配）
_BAIDU_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)
_BAIDU_SNIPPET_RE = re.compile(
    r'<span class="content-right_[^"]*"[^>]*>(.*?)</span>',
    re.S,
)


class BaiduProvider(_HTMLSearchProvider):
    name = "baidu"

    base_url = "https://www.baidu.com/s"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        text = self._fetch({"wd": query})
        if not text:
            return []
        snippets = [self._strip_tags(s) for s in _BAIDU_SNIPPET_RE.findall(text)]
        out: list[SearchResult] = []
        for i, m in enumerate(_BAIDU_TITLE_RE.finditer(text)):
            title = self._strip_tags(m.group(2))
            if not title:
                continue
            snippet = snippets[i] if i < len(snippets) else ""
            out.append(SearchResult(title=title[:200], url=m.group(1), snippet=snippet[:500]))
            if len(out) >= max_results:
                break
        return out


_PROVIDERS = {
    "ddgs": DDGProvider,
    "bing": BingProvider,
    "baidu": BaiduProvider,
}


def _providers() -> list[SearchProvider]:
    """按 settings.search_providers（逗号分隔）实例化启用的搜索源。"""
    return [
        _PROVIDERS[name]()
        for name in (p.strip() for p in settings.search_providers.split(","))
        if name in _PROVIDERS
    ]


def search_web(query: str, max_results: int = 10) -> list[SearchResult]:
    """合并去重搜索：逐个源尝试（前源失败/不足由后源补齐），按 URL 去重取 top N。

    全部源失败返回空列表（调用方按 FR-1.7 降级）。
    """
    merged: dict[str, SearchResult] = {}
    for provider in _providers():
        try:
            results = provider.search(query, max_results)
        except Exception as e:  # noqa: BLE001 — 单源失败不影响其他源
            logger.warning("搜索源 %s 失败: %s", provider.name, e)
            continue
        for r in results:
            if r.url not in merged:
                merged[r.url] = r
        if len(merged) >= max_results:
            break
    return list(merged.values())[:max_results]
