"""
tools/filesystem.py — 文件系统工具
"""

import re
import subprocess
from pathlib import Path
from core.registry import register_tool

# 禁止写入的系统路径前缀
_WRITE_BLOCKED = ("/etc/", "/usr/", "/bin/", "/sbin/", "/sys/", "/dev/", "/boot/")


@register_tool(
    description="读取本地文件内容。支持 md、txt、py、json、html、csv 等文本文件。",
    params={
        "path":       "文件路径，如 '/tmp/test.md' 或 './README.md'",
        "start_line": "可选，从第几行开始读（从 1 计），默认读全部",
        "end_line":   "可选，读到第几行结束，默认读全部",
    }
)
def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"文件不存在：{path}"
    if not p.is_file():
        return f"路径不是文件：{path}"
    if p.stat().st_size > 2 * 1024 * 1024:
        return f"文件过大（>2MB），请用 start_line/end_line 分段读取"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"读取失败：{e}"

    total = len(lines)
    if start_line or end_line:
        s = max(1, start_line) - 1 if start_line else 0
        e = min(total, end_line) if end_line else total
        lines = lines[s:e]
        range_info = f"（第 {s+1}–{e} 行，共 {total} 行）"
    else:
        range_info = f"（共 {total} 行）"

    return f"文件：{p}\n{range_info}\n\n" + "\n".join(lines)


@register_tool(
    description="列出目录下的文件和子目录。用于了解项目结构、查找文件。",
    params={
        "path":      "目录路径，默认当前目录 '.'",
        "recursive": "是否递归列出子目录，默认 False",
        "pattern":   "可选，文件名过滤，如 '*.py' 或 '*.md'",
    }
)
def list_files(path: str = ".", recursive: bool = False, pattern: str = "") -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"路径不存在：{path}"
    if not p.is_dir():
        return f"不是目录：{path}"

    try:
        if recursive:
            items = sorted(p.rglob(pattern or "*"))
        else:
            items = sorted(p.glob(pattern or "*"))

        # 过滤掉隐藏文件和常见无用目录
        skip = {".git", "__pycache__", "node_modules", ".DS_Store", "venv", ".venv"}
        items = [i for i in items if not any(part in skip for part in i.parts)]

        if not items:
            return f"目录为空：{path}"

        lines = [f"目录：{p}（共 {len(items)} 项）\n"]
        for item in items[:200]:  # 最多显示 200 条
            rel = item.relative_to(p)
            prefix = "📁 " if item.is_dir() else "📄 "
            size = f"  {item.stat().st_size:,}B" if item.is_file() else ""
            lines.append(f"{prefix}{rel}{size}")

        if len(items) > 200:
            lines.append(f"\n...（还有 {len(items)-200} 项，请用 pattern 过滤）")

        return "\n".join(lines)
    except Exception as e:
        return f"列出失败：{e}"


@register_tool(
    description="在文件内容中搜索匹配的文本（类似 grep）。支持普通字符串和正则表达式。",
    params={
        "pattern":   "要搜索的字符串或正则表达式，如 'def main' 或 'import .*'",
        "path":      "搜索的文件或目录路径",
        "recursive": "是否递归搜索子目录，默认 True",
        "file_pattern": "可选，限制文件类型，如 '*.py' 或 '*.md'，默认所有文本文件",
        "ignore_case":  "是否忽略大小写，默认 False",
    }
)
def grep(
    pattern:      str,
    path:         str  = ".",
    recursive:    bool = True,
    file_pattern: str  = "",
    ignore_case:  bool = False,
) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"路径不存在：{path}"

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"正则表达式错误：{e}"

    # 收集要搜索的文件
    if p.is_file():
        files = [p]
    else:
        glob_pattern = file_pattern or "*"
        files = list(p.rglob(glob_pattern)) if recursive else list(p.glob(glob_pattern))
        files = [f for f in files if f.is_file()]

    results = []
    skip_dirs = {".git", "__pycache__", "node_modules", "venv", ".venv"}

    for f in sorted(files):
        # 跳过无用目录里的文件
        if any(part in skip_dirs for part in f.parts):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                results.append(f"{f}:{lineno}: {line.rstrip()}")
                if len(results) >= 100:  # 最多返回 100 条
                    break
        if len(results) >= 100:
            break

    if not results:
        return f"未找到匹配 '{pattern}' 的内容"

    header = f"搜索 '{pattern}' 找到 {len(results)} 处匹配：\n"
    return header + "\n".join(results)


@register_tool(
    description="用 glob 模式查找文件，支持 ** 递归匹配。如 '**/*.py' 找所有 Python 文件。",
    params={
        "pattern": "glob 模式，如 '**/*.py'、'src/**/*.ts'、'*.md'",
        "path":    "搜索起始目录，默认当前目录 '.'",
    }
)
def glob_files(pattern: str, path: str = ".") -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"路径不存在：{path}"

    skip = {".git", "__pycache__", "node_modules", "venv", ".venv"}
    try:
        matches = [
            f for f in sorted(p.glob(pattern))
            if not any(part in skip for part in f.parts)
        ]
    except Exception as e:
        return f"glob 失败：{e}"

    if not matches:
        return f"没有找到匹配 '{pattern}' 的文件"

    lines = [f"找到 {len(matches)} 个文件：\n"]
    for m in matches[:100]:
        size = f"  {m.stat().st_size:,}B" if m.is_file() else ""
        lines.append(f"{'📁' if m.is_dir() else '📄'} {m}{size}")
    if len(matches) > 100:
        lines.append(f"...（还有 {len(matches)-100} 个）")

    return "\n".join(lines)


@register_tool(
    description=(
        "将内容写入文件（创建新文件或完全覆盖已有文件）。"
        "父目录不存在时自动创建。适合生成新文件或全量替换文件内容。"
        "如需局部修改，请用 edit_file。"
    ),
    params={
        "path":    "文件路径，如 './output.md' 或 '/tmp/result.json'",
        "content": "要写入的文件内容（字符串）",
    }
)
def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()

    resolved = str(p.resolve())
    if any(resolved.startswith(blocked) for blocked in _WRITE_BLOCKED):
        return f"❌ 拒绝写入系统路径：{resolved}"

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        action = "已更新" if existed else "已创建"
        lines  = content.count("\n") + 1
        size   = p.stat().st_size
        return f"✅ {action}：{p}\n   {lines} 行，{size:,} 字节"
    except Exception as e:
        return f"❌ 写入失败：{e}"


@register_tool(
    description=(
        "在文件中做精确的字符串替换（搜索 old_str，替换为 new_str）。"
        "old_str 必须在文件中唯一存在；找不到或有多处匹配时会报错，"
        "请提供更多上下文让 old_str 唯一。适合局部修改，不需要重写整个文件。"
    ),
    params={
        "path":    "要修改的文件路径",
        "old_str": "要被替换的原始文本（必须与文件内容完全一致，包括空格和换行）",
        "new_str": "替换后的新文本",
    }
)
def edit_file(path: str, old_str: str, new_str: str) -> str:
    p = Path(path).expanduser()

    resolved = str(p.resolve())
    if any(resolved.startswith(blocked) for blocked in _WRITE_BLOCKED):
        return f"❌ 拒绝修改系统路径：{resolved}"

    if not p.exists():
        return f"❌ 文件不存在：{path}\n提示：如需创建新文件请用 write_file"
    if not p.is_file():
        return f"❌ 路径不是文件：{path}"

    try:
        original = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"❌ 读取失败：{e}"

    count = original.count(old_str)
    if count == 0:
        snippet = original[:300].replace("\n", "↵")
        return (
            f"❌ 未找到要替换的内容。\n"
            f"   old_str 前 50 字：{repr(old_str[:50])}\n"
            f"   文件前 300 字预览：{snippet}"
        )
    if count > 1:
        return (
            f"❌ old_str 在文件中出现了 {count} 次，无法确定替换哪一处。\n"
            f"   请在 old_str 里加入更多上下文使其唯一。"
        )

    updated = original.replace(old_str, new_str, 1)
    try:
        p.write_text(updated, encoding="utf-8")
    except Exception as e:
        return f"❌ 写入失败：{e}"

    old_lines = old_str.count("\n") + 1
    new_lines = new_str.count("\n") + 1
    return f"✅ 编辑成功：{p}\n   替换了 {old_lines} 行 → {new_lines} 行"
