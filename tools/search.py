"""
tools/search.py — 网络搜索工具

两个后端，自动降级：
  主力：DuckDuckGo（免费，无需 Key，隐私友好）
  备选：Tavily（专为 Agent 设计，结果更干净，需要 TAVILY_API_KEY）

选择策略：
  - 有 TAVILY_API_KEY → 用 Tavily（结果更好）
  - 没有 Key         → 用 DuckDuckGo（零配置）
"""

import os
from core.registry import register_tool
from dotenv import load_dotenv

load_dotenv()


def _search_tavily(query: str, max_results: int) -> list[dict]:
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    resp = client.search(query=query, max_results=max_results)
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in resp.get("results", [])
    ]


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    try:
        from ddgs import DDGS  # 新包名
    except ImportError:
        from duckduckgo_search import DDGS  # 兼容旧包名
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title", ""),
                "url":     r.get("href", ""),
                "content": r.get("body", ""),
            })
    return results


@register_tool(
    description=(
        "搜索网络获取实时信息。用于需要最新数据的问题，如新闻、天气、价格、"
        "技术文档、人物资料等。不需要解释'我来搜索一下'，直接调用即可。"
    ),
    params={
        "query":       "搜索关键词，建议用英文以获得更好的结果",
        "max_results": "返回结果数量，默认 5，最多 10",
    }
)
def web_search(query: str, max_results: int = 5) -> str:
    max_results = min(max_results, 10)

    try:
        if os.getenv("TAVILY_API_KEY"):
            results = _search_tavily(query, max_results)
            source  = "Tavily"
        else:
            results = _search_duckduckgo(query, max_results)
            source  = "DuckDuckGo"
    except Exception as e:
        # 主力失败时尝试另一个
        try:
            results = _search_duckduckgo(query, max_results)
            source  = "DuckDuckGo (fallback)"
        except Exception as e2:
            return f"搜索失败：{e}；降级也失败：{e2}"

    if not results:
        return f"搜索 '{query}' 无结果。"

    lines = [f"搜索结果（{source}，共 {len(results)} 条）：\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r["content"]:
            snippet = r["content"][:200].replace("\n", " ")
            lines.append(f"   {snippet}...")
        lines.append("")

    return "\n".join(lines)
