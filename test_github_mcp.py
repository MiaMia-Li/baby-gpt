"""
test_github_mcp.py — 验证 GitHub MCP Client 是否正常工作

运行：venv/bin/python3.14 test_github_mcp.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.registry import auto_discover, execute_tool

auto_discover("tools", "skills")

from tools.mcp_client import connect_mcp_server, list_mcp_connections, disconnect_mcp_server

print("=" * 56)
print("  GitHub MCP Client 连接测试")
print("=" * 56)

# ── 1. 检查 Token ────────────────────────────────────────
token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
if not token:
    print("❌ .env 里没有 GITHUB_PERSONAL_ACCESS_TOKEN，请先配置。")
    sys.exit(1)
print(f"✅ Token 已读取：{token[:8]}…{token[-4:]}")

# ── 2. 连接 MCP Server ───────────────────────────────────
print("\n🔌 正在连接 GitHub MCP（npx 首次运行需要下载，最多等 120s）…")
result = connect_mcp_server(
    server_name="github",
    command="npx -y @modelcontextprotocol/server-github",
    env_json=f'{{"GITHUB_PERSONAL_ACCESS_TOKEN": "{token}"}}',
    timeout=120,
)
print(result)

if "❌" in result:
    print("\n连接失败，测试终止。")
    sys.exit(1)

# ── 3. 查看已注册工具 ────────────────────────────────────
print("\n" + "-" * 56)
print(list_mcp_connections("github"))

# ── 4. 调用 get_file_contents ────────────────────────────
print("\n" + "-" * 56)
print("📖 测试：读取 anthropics/claude-code README.md …")
from core.registry import _registry
if "github_get_file_contents" in _registry:
    content = execute_tool(
        "github_get_file_contents",
        owner="anthropics",
        repo="claude-code",
        path="README.md",
    )
    print(content[:800])
    print("… (截断)")
    print("\n✅ github_get_file_contents 调用成功")
else:
    print("⚠️  github_get_file_contents 未注册，可能 Server 工具名不同")
    print("已注册的 github_* 工具：")
    for name in _registry:
        if name.startswith("github_"):
            print(f"  • {name}")

# ── 5. 断开连接 ──────────────────────────────────────────
print("\n" + "-" * 56)
print(disconnect_mcp_server("github"))
print("\n🎉 测试完成")
