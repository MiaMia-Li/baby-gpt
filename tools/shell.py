"""
tools/shell.py — Shell 执行工具

【安全设计：黑名单模式】
默认允许所有命令，只拦截明确危险的操作。

白名单 vs 黑名单 的 trade-off：
  白名单 → 安全边界最清晰，但每次加新工具都要手动添加前缀，限制通用 Agent 能力
  黑名单 → 能力最大化，代价是必须把危险操作想全，有遗漏风险

对于通用 Agent，黑名单更合适：
  Agent 需要调用各种未知的 CLI 工具（npm、bun、codev、自定义脚本等）
  用白名单会不断出现"拒绝执行"错误，降低 Agent 的自主能力
"""

import re
import subprocess
import shlex
from pathlib import Path
from core.registry import register_tool

_PROJECT_ROOT = str(Path(__file__).parent.parent)

# ── 黑名单：绝对禁止的危险命令模式 ────────────────────────────────
# 原则：只拦截不可逆的破坏性操作，不过度限制
BLOCKED_PATTERNS = [
    # 删除类（不可逆）
    r'\brm\s+(-\w+\s+)*/',           # rm 绝对路径
    r'\brm\s+(-[a-z]*f[a-z]*|-[a-z]*r[a-z]*)\b',  # rm -rf / rm -fr 等
    r'\brmdir\b',
    r'\bshred\b',

    # 覆盖系统文件
    r'>\s*/etc/',                     # 重定向到 /etc/
    r'>\s*/usr/',
    r'>\s*/bin/',
    r'>\s*/sbin/',
    r'>\s*/sys/',

    # 权限/所有者变更
    r'\bchmod\s+[0-7]*7[0-7]*\s+/',  # chmod 777 系统目录
    r'\bchown\b.*/etc\b',
    r'\bsudo\b',                      # 禁止提权

    # 进程/系统控制
    r'\bshutdown\b',
    r'\breboot\b',
    r'\bhalt\b',
    r'\bkill\s+-9\s+1\b',            # kill init
    r'\bpkill\s+-9\b',

    # 危险的管道组合（下载后执行）
    r'\bcurl\b.*\|\s*(sh|bash|zsh|python)',
    r'\bwget\b.*\|\s*(sh|bash|zsh|python)',

    # 磁盘操作
    r'\bdd\b.*of=/dev/',
    r'\bmkfs\b',
    r'\bfdisk\b',
]

_BLOCKED_RE = [re.compile(p) for p in BLOCKED_PATTERNS]


def _is_blocked(command: str) -> str | None:
    """检查命令是否匹配黑名单。返回匹配的模式描述，或 None 表示安全。"""
    for pattern in _BLOCKED_RE:
        if pattern.search(command):
            return pattern.pattern
    return None


@register_tool(
    description="向用户提问并等待回答。Skill 需要收集用户输入时使用。",
    params={"question": "要问用户的问题"}
)
def ask_user_question(question: str) -> str:
    print(f"\n🤖 Agent 提问：{question}")
    answer = input("你的回答：").strip()
    return f"用户回答：{answer}"


@register_tool(
    description="在 shell 中执行任意命令（黑名单拦截危险操作）。支持 npm、bun、python、node 等所有 CLI 工具。",
    params={
        "command": "要执行的 shell 命令，如 'npx skills find react' 或 'bun run build'",
        "timeout": "可选，超时秒数，默认 60",
    }
)
def run_command(command: str, timeout: int = 60) -> str:
    """
    执行 shell 命令，返回 stdout + stderr。

    【为什么用 subprocess 而不是 os.system？】
    subprocess.run 可以捕获输出，输出会成为 Agent 的 Observation。

    【超时设为 60 秒的原因】
    部分命令（如 npx skills add、bun install）需要下载依赖，30秒可能不够。
    """
    blocked = _is_blocked(command)
    if blocked:
        return (
            f"❌ 拒绝执行：'{command}'\n"
            f"安全原因：命令匹配危险模式 `{blocked}`\n"
            f"如果你确认这是安全操作，请在 tools/shell.py 的 BLOCKED_PATTERNS 中移除对应规则。"
        )

    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_PROJECT_ROOT,
        )
        output = ""
        if result.stdout.strip():
            output += result.stdout.strip()
        if result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        if not output:
            output = f"命令执行完成（退出码 {result.returncode}，无输出）"
        return output

    except subprocess.TimeoutExpired:
        return f"命令超时（{timeout}秒），如需更长时间请传入 timeout 参数"
    except FileNotFoundError:
        return f"命令未找到：'{command.split()[0]}'。请确认已安装。"
    except Exception as e:
        return f"执行出错：{e}"
