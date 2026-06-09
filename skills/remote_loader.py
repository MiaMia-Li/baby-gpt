"""
skills/remote_loader.py — 远程 Skill 加载器

【解决的问题】
公网上有很多开源 SKILL.md 文件（比如 github.com/vercel-labs/skills）。
这个模块让 Agent 能在运行时动态拉取这些 Skill 并使用它们。

【核心设计：远程 Skill 是什么？】
SKILL.md 是给 LLM 看的"指令文档"，不是 Python 函数。
所以"加载远程 Skill"的本质是：
  fetch URL → 拿到 Markdown 指令 → 动态注册为一个工具
  → 这个工具被调用时，把指令内容返回给 LLM
  → LLM 读到指令后按其执行（元工具模式）

【两个工具】
1. find_skills     — 在公开 Skill 索引里搜索，返回可用 Skill 列表
2. load_skill      — 拉取指定 URL 的 SKILL.md，动态注册为可调用工具
"""

import re
import urllib.request
import urllib.error
from core.registry import register_tool, register_dynamic_tool, _registry


# ─────────────────────────────────────────────────────────────
# 已动态加载的 Skill 缓存（避免重复拉取）
# ─────────────────────────────────────────────────────────────
_loaded_remote_skills: dict[str, str] = {}  # tool_name → skill content


# ─────────────────────────────────────────────────────────────
# 工具 1：搜索可用的公开 Skill
# ─────────────────────────────────────────────────────────────

# 已知的公开 Skill 索引（可持续扩充）
_PUBLIC_SKILL_INDEX = [
    {
        "name":        "find-skills",
        "description": "搜索并发现公开可用的 Skill（元技能）",
        "url":         "https://raw.githubusercontent.com/vercel-labs/skills/main/skills/find-skills/SKILL.md",
        "tags":        ["meta", "discovery"],
    },
]

# ── 运行时动态扩充索引（其他模块可调用此函数注册新 Skill 地址）───
def register_skill_url(name: str, description: str, url: str, tags: list[str] | None = None):
    """
    向索引添加一个新的远程 Skill 条目。
    任何人可以在自己的 skills/ 文件里调用此函数扩充索引，
    而不需要修改 remote_loader.py 本身。
    """
    _PUBLIC_SKILL_INDEX.append({
        "name":        name,
        "description": description,
        "url":         url,
        "tags":        tags or [],
    })


@register_tool(
    description="搜索公开可用的 Skill，返回匹配的 Skill 列表和加载 URL",
    params={"query": "搜索关键词，如 'code review'、'debug'、'data analysis'"}
)
def find_skills(query: str) -> str:
    """在公开 Skill 索引里模糊搜索。"""
    # 标准化：连字符/下划线/空格都视为等价
    def normalize(s: str) -> str:
        return s.lower().replace("-", " ").replace("_", " ")

    q = normalize(query)
    matches = [
        s for s in _PUBLIC_SKILL_INDEX
        if q in normalize(s["name"])
        or q in normalize(s["description"])
        or any(q in normalize(tag) for tag in s["tags"])
    ]

    if not matches:
        # 本地索引没有结果 → 自动走公网 npx skills find
        try:
            import subprocess, shlex
            result = subprocess.run(
                shlex.split(f"npx skills find {query}"),
                capture_output=True, text=True, timeout=20
            )
            raw = result.stdout.strip()
            # 去掉 ANSI 颜色码
            import re as _re
            clean = _re.sub(r'\x1b\[[0-9;]*m', '', raw)
            if clean and "No skills found" not in clean:
                return (
                    f"本地索引无结果，已从公网 skills.sh 搜索到以下结果：\n\n{clean}\n\n"
                    "⚠️  直接用包名格式加载，不要猜 URL：\n"
                    "    load_skill(url=\"owner/repo@skill-name\")\n"
                    "    例：load_skill(url=\"anycap-ai/anycap@anycap-media-production\")"
                )
        except Exception:
            pass
        lines = [f"没有找到匹配 '{query}' 的 Skill（本地+公网均无结果）。\n", "本地已知 Skill：\n"]
        for s in _PUBLIC_SKILL_INDEX:
            lines.append(f"  {s['name']} — {s['description']}")
            lines.append(f"    URL: {s['url']}")
        return "\n".join(lines)

    lines = [f"找到 {len(matches)} 个匹配的 Skill：\n"]
    for s in matches:
        lines.append(f"  名称：{s['name']}")
        lines.append(f"  描述：{s['description']}")
        lines.append(f"  加载命令：load_skill(url=\"{s['url']}\")")
        lines.append("")
    lines.append("⚠️  请使用上方的完整 URL 调用 load_skill，不要自己猜测 URL。")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 工具 2：从 URL 拉取 SKILL.md 并动态注册为工具
# ─────────────────────────────────────────────────────────────

def _github_blob_to_raw(url: str) -> str:
    """
    把 GitHub 页面 URL 转换为 raw 内容 URL。
    例：github.com/foo/bar/blob/main/file.md
    →  raw.githubusercontent.com/foo/bar/main/file.md

    【为什么需要这一步】
    GitHub 页面 URL 返回的是 HTML，不是文件内容。
    raw URL 才返回纯文本。
    """
    return re.sub(
        r'https://github\.com/([^/]+)/([^/]+)/blob/(.+)',
        r'https://raw.githubusercontent.com/\1/\2/\3',
        url
    )


def _fetch_url(url: str, timeout: int = 10) -> str:
    """拉取 URL 内容，返回文本。"""
    raw_url = _github_blob_to_raw(url)
    req     = urllib.request.Request(raw_url, headers={"User-Agent": "baby-gpt/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}：无法访问 {raw_url}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误：{e.reason}")


def _install_and_read_skill(package: str) -> str:
    """
    用 npx skills add 安装 skills.sh 包，返回 SKILL.md 内容。

    【为什么用安装而不是直接拉 GitHub URL？】
    skills.sh 包名如 owner/repo@skill 对应的 GitHub 路径不固定，
    LLM 猜 URL 容易 404。让 CLI 自己解析包名是最可靠的方式。
    """
    import subprocess, shlex, os
    from pathlib import Path

    install_dir = Path("/tmp/baby-gpt-skills")
    install_dir.mkdir(exist_ok=True)

    try:
        result = subprocess.run(
            shlex.split(f"npx skills add {package} -y"),
            capture_output=True, text=True, timeout=30,
            cwd=str(install_dir)
        )
        if result.returncode != 0:
            return f"ERROR: npx skills add 失败：{result.stderr[:200]}"

        # 找安装后的 SKILL.md 文件
        skill_files = list(install_dir.rglob("SKILL.md"))
        if not skill_files:
            return f"ERROR: 安装成功但找不到 SKILL.md 文件"

        # 取最新修改的那个
        latest = max(skill_files, key=lambda f: f.stat().st_mtime)
        return latest.read_text(encoding="utf-8")

    except subprocess.TimeoutExpired:
        return "ERROR: 安装超时"
    except Exception as e:
        return f"ERROR: {e}"


def _extract_skill_name(content: str, url: str) -> str:
    """从 SKILL.md 内容中提取 name 字段，作为工具名。"""
    # 尝试从 frontmatter 或 h1 标题提取
    if m := re.search(r'^name:\s*(.+)$', content, re.MULTILINE):
        raw = m.group(1).strip().strip('"\'')
        # 转成合法 Python 标识符：去掉特殊字符，空格换下划线
        return re.sub(r'[^a-z0-9_]', '', raw.lower().replace(' ', '_').replace('-', '_'))
    if m := re.search(r'^#\s+(.+)$', content, re.MULTILINE):
        raw = m.group(1).strip()
        return re.sub(r'[^a-z0-9_]', '', raw.lower().replace(' ', '_').replace('-', '_'))
    # fallback：用 URL 最后一段
    segment = url.rstrip('/').split('/')[-1]
    return re.sub(r'[^a-z0-9_]', '', segment.lower().replace('-', '_').replace('.', '_'))


def _extract_skill_description(content: str) -> str:
    """从 SKILL.md 中提取 description 字段。"""
    if m := re.search(r'^description:\s*(.+)$', content, re.MULTILINE):
        return m.group(1).strip().strip('"\'')
    # 取第一段正文（跳过 frontmatter）
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#') and not l.startswith('---')]
    return lines[0][:100] if lines else "远程加载的 Skill"


@register_tool(
    description="加载 Skill 并注册为工具。支持：① GitHub raw URL ② skills.sh 包名（如 owner/repo@skill）",
    params={
        "url":       "GitHub raw URL 或 skills.sh 包名，如 'anycap-ai/anycap@anycap-media-production'",
        "tool_name": "可选，指定注册后的工具名；默认从 SKILL.md 内容自动提取",
    }
)
def load_skill(url: str, tool_name: str = "") -> str:
    """
    拉取远程 SKILL.md，动态注册为 Agent 可调用的工具。

    【动态注册的原理】
    Python 允许在运行时修改任何对象。
    我们直接往 _registry 字典里插入新条目——
    和 @register_tool 装饰器做的事完全一样，只是在运行时做而不是启动时做。

    【这个工具被调用后发生什么】
    1. 拉取 SKILL.md 内容
    2. 以内容作为 Observation 返回给 LLM
    3. LLM 读到指令，在下一个 Thought 中按指令执行
    这就是"元工具"模式：工具的作用是给 LLM 注入新的行为指令。
    """
    # 检查缓存
    if url in _loaded_remote_skills:
        cached_name = _loaded_remote_skills[url]
        return f"Skill '{cached_name}' 已加载（命中缓存）。直接调用 {cached_name}() 即可使用。"

    # 判断是 skills.sh 包名还是 URL
    # 包名格式：owner/repo@skill-name（不含 http/https）
    is_package = not url.startswith("http") and "@" in url

    base_dir = None  # 记录安装目录，用于替换 {baseDir}

    if is_package:
        # 用 npx skills add 安装到本地，再读本地文件
        content = _install_and_read_skill(url)
        if content.startswith("ERROR:"):
            return f"加载失败：{content}"
        # 找安装后的目录（SKILL.md 的父目录）
        from pathlib import Path
        install_root = Path("/tmp/baby-gpt-skills")
        skill_files  = list(install_root.rglob("SKILL.md"))
        if skill_files:
            base_dir = str(max(skill_files, key=lambda f: f.stat().st_mtime).parent)
    else:
        # 直接拉取 GitHub raw URL
        try:
            content = _fetch_url(url)
        except RuntimeError as e:
            return f"加载失败：{e}"
        # 对于 raw URL，检查本地是否已有安装版本（npx skills add 过）
        from pathlib import Path
        agents_dir = Path("/Users/mengyao/mygit/baby-gpt/.agents/skills")
        if agents_dir.exists():
            skill_files = list(agents_dir.rglob("SKILL.md"))
            for sf in skill_files:
                if sf.read_text(encoding="utf-8")[:200] == content[:200]:
                    base_dir = str(sf.parent)
                    break

    # 把路径占位符替换成实际路径，让 LLM 知道脚本在哪里
    # 不同版本的 SKILL.md 用不同的变量名，全部替换
    if base_dir:
        import shutil
        bun_x = "bun" if shutil.which("bun") else "npx -y bun"
        for placeholder in ["{baseDir}", "${SKILL_DIR}", "$SKILL_DIR", "{SKILL_DIR}"]:
            content = content.replace(placeholder, base_dir)
        content = content.replace("${BUN_X}", bun_x)
        # 在 Skill 内容最前面加一行明确的路径说明，防止 LLM 猜路径
        path_hint = f"\n> **已安装路径**: `{base_dir}`  BUN_X=`{bun_x}`\n\n"
        content = content[:content.find('\n')+1] + path_hint + content[content.find('\n')+1:]

    # 确定工具名
    name = tool_name.strip() or _extract_skill_name(content, url)
    if not name:
        name = "remote_skill"
    desc = _extract_skill_description(content)

    # 动态注册：创建一个闭包，调用时返回 Skill 的完整指令内容
    # 【闭包是什么】
    # skill_content 在函数定义时被"捕获"，每次调用都返回同一份内容。
    # 这让 LLM 在看到 Observation 时能读到完整的 Skill 指令。
    skill_content = content  # 捕获到闭包

    def skill_caller(**kwargs) -> str:
        return (
            f"=== Skill: {name} ===\n\n"
            f"{skill_content}\n\n"
            f"=== 以上是 Skill 的完整指令，请按照指令执行 ==="
        )

    # 通过公开接口注册，内部自动处理同名保护逻辑
    name = register_dynamic_tool(
        name=name,
        func=skill_caller,
        description=f"[远程 Skill] {desc}\n\n执行此工具时请严格遵循以下指令：\n{skill_content[:800]}",
        params={},
        overwrite_native=False,
    )

    _loaded_remote_skills[url] = name

    return (
        f"✅ Skill '{name}' 已成功加载并注册！\n"
        f"描述：{desc}\n"
        f"调用方式：{name}()\n"
        f"现在可以在下一步直接调用它。"
    )
