"""
tools/memory.py — RAG 持久化记忆工具

【核心技术点】

1. Embedding（向量化）
   把文字转成一串数字（向量），例如：
   "我喜欢 Queenstown" → [0.23, -0.11, 0.84, ...]（384 维）
   语义相近的句子，向量在空间里也相近。
   这样就能用"找最近的向量"来代替"关键词匹配"。

2. ChromaDB
   专门存向量的数据库。存进去的每条记忆包含：
   - 原始文本（documents）
   - 向量（由 embedding 模型自动生成）
   - 元数据（metadata）：时间戳、标签等
   数据持久化到磁盘（memory_db/），重启程序不丢失。

3. Semantic Search（语义搜索）
   用当前问题的向量，在数据库里找最相似的记忆。
   不需要关键词完全匹配——"我能花多少钱？"能找到"预算 S$3500"。

4. RAG（Retrieval-Augmented Generation）
   recall() 把检索到的记忆注入到 LLM 的上下文里。
   LLM 回答时"看到"这些背景信息，就好像它真的记得一样。
"""

import uuid
import time
from pathlib import Path
from core.registry import register_tool

# ── 初始化 ChromaDB ────────────────────────────────────────────
# PersistentClient：数据存到磁盘，重启不丢失
# 相对路径：baby-gpt/memory_db/
_DB_PATH = str(Path(__file__).parent.parent / "memory_db")

try:
    import chromadb
    _client = chromadb.PersistentClient(path=_DB_PATH)

    # Collection = 向量数据库里的"表"
    # embedding_function=None → 用 ChromaDB 默认的 sentence-transformers 模型
    # 首次运行会自动下载 all-MiniLM-L6-v2（约 80MB），之后缓存到本地
    _collection = _client.get_or_create_collection(name="memories")
    _MEMORY_AVAILABLE = True
except ImportError:
    _MEMORY_AVAILABLE = False


def _check_available() -> str | None:
    if not _MEMORY_AVAILABLE:
        return "记忆功能不可用：请先运行 `venv/bin/python3.14 -m pip install chromadb`"
    return None


# ── 工具 1：存入记忆 ───────────────────────────────────────────

@register_tool(
    description=(
        "把一条信息永久记住，下次对话也能用。"
        "适合存：个人偏好、重要事实、用户告知的背景信息。"
        "例：'帮我记住我的预算是 S$3500' 或 '记住我不喜欢太远的地方'"
    ),
    params={
        "content": "要记住的内容，用自然语言描述",
        "tags":    "可选，用逗号分隔的标签，方便分类。如 '偏好,租房' 或 '个人信息'",
    }
)
def remember(content: str, tags: str = "") -> str:
    err = _check_available()
    if err:
        return err

    # 每条记忆生成唯一 ID
    memory_id = str(uuid.uuid4())
    timestamp = time.strftime("%Y-%m-%d %H:%M")

    _collection.add(
        ids       = [memory_id],
        documents = [content],
        metadatas = [{
            "tags":      tags,
            "timestamp": timestamp,
            "source":    "user",
        }]
    )

    return f"✅ 已记住：{content}\n（时间：{timestamp}，ID：{memory_id[:8]}...）"


# ── 工具 2：语义检索记忆 ───────────────────────────────────────

@register_tool(
    description=(
        "从长期记忆中检索相关信息。"
        "当需要用户之前说过的背景信息时调用。"
        "例：回答'推荐哪个区'前，先 recall('租房偏好预算') 获取用户背景"
    ),
    params={
        "query":       "用来搜索的关键词或问题，语义匹配，不需要精确",
        "max_results": "返回最相关的几条记忆，默认 3",
    }
)
def recall(query: str, max_results: int = 3) -> str:
    err = _check_available()
    if err:
        return err

    count = _collection.count()
    if count == 0:
        return "记忆库为空，还没有存过任何信息。"

    # 语义搜索：用 query 的向量找最近的记忆
    # n_results 不能超过总条数
    n = min(max_results, count)
    results = _collection.query(
        query_texts = [query],
        n_results   = n,
    )

    docs      = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]  # 越小越相似（0=完全相同）

    if not docs:
        return f"没有找到与 '{query}' 相关的记忆。"

    lines = [f"找到 {len(docs)} 条相关记忆：\n"]
    for i, (doc, meta, dist) in enumerate(zip(docs, metadatas, distances), 1):
        similarity = round((1 - dist) * 100, 1)  # 转为相似度百分比
        lines.append(f"{i}. {doc}")
        lines.append(f"   时间：{meta.get('timestamp', '未知')}  相似度：{similarity}%")
        if meta.get("tags"):
            lines.append(f"   标签：{meta['tags']}")
        lines.append("")

    return "\n".join(lines)


# ── 工具 3：查看所有记忆 ───────────────────────────────────────

@register_tool(
    description="列出记忆库里存储的所有信息",
    params={"limit": "最多显示几条，默认 20"}
)
def list_memories(limit: int = 20) -> str:
    err = _check_available()
    if err:
        return err

    count = _collection.count()
    if count == 0:
        return "记忆库为空。"

    results = _collection.get(limit=limit)
    lines   = [f"记忆库共 {count} 条记录：\n"]
    for doc, meta in zip(results["documents"], results["metadatas"]):
        lines.append(f"• {doc}")
        lines.append(f"  {meta.get('timestamp', '')}  {meta.get('tags', '')}")
    return "\n".join(lines)


# ── 工具 4：删除记忆 ───────────────────────────────────────────

@register_tool(
    description="删除一条记忆（用 list_memories 获取 ID）",
    params={"memory_id": "要删除的记忆 ID 前缀（至少 8 位）"}
)
def forget(memory_id: str) -> str:
    err = _check_available()
    if err:
        return err

    all_ids = _collection.get()["ids"]
    matches = [id_ for id_ in all_ids if id_.startswith(memory_id)]

    if not matches:
        return f"没有找到 ID 以 '{memory_id}' 开头的记忆。"
    if len(matches) > 1:
        return f"找到 {len(matches)} 条匹配，请提供更长的 ID 前缀。"

    _collection.delete(ids=matches)
    return f"✅ 已删除记忆（ID：{matches[0][:8]}...）"
