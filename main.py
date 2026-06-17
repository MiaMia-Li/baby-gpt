"""
main.py — Baby GPT 启动入口

目录结构：
  tools/   — 原生 Python 工具（直接执行的函数）
  skills/  — 远程 Skill 加载器（操作 SKILL.md 生态）
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.registry import auto_discover, get_registry
from core.session import AgentSession

# MCP Server 自动连接配置
# key = server_name, value = (command, env_var_for_token)
_MCP_AUTO_CONNECT = {
    "github": (
        "npx -y @modelcontextprotocol/server-github",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ),
}


def _auto_connect_mcp():
    """启动时自动连接已配置 Token 的 MCP Server。"""
    from tools.mcp_client import connect_mcp_server
    import json

    connected = []
    for server_name, (command, token_env) in _MCP_AUTO_CONNECT.items():
        token = os.getenv(token_env, "")
        if not token:
            continue
        print(f"  🔌 自动连接 {server_name} MCP…", end=" ", flush=True)
        env_json = json.dumps({token_env: token})
        result = connect_mcp_server(
            server_name=server_name,
            command=command,
            env_json=env_json,
            timeout=120,
        )
        if "✅" in result:
            tool_count = result.count("  •")
            print(f"✅ {tool_count} 个工具已注册")
            connected.append(server_name)
        else:
            # 只打印第一行错误，不刷屏
            print(f"⚠️  {result.splitlines()[0]}")
    return connected


def main():
    loaded = auto_discover("tools", "skills")

    from core.llm_client import get_provider
    if get_provider() == "anthropic":
        print("=" * 52)
        print("   🤖  Universal ReAct Agent  ·  LLM: Claude")
        print(f"   后端：Anthropic {os.getenv('ANTHROPIC_MODEL', 'claude-opus-4-8')} · 原生 tool_use")
        print("=" * 52)
    else:
        print("=" * 52)
        print("   🤖  Universal ReAct Agent  ·  LLM: DeepSeek/OpenAI")
        print("   后端：OpenAI 兼容 · ReAct 文本解析")
        print("=" * 52)
    print(f"已加载模块：{[m.split('.')[-1] for m in loaded]}")
    print(f"本地工具数：{len(get_registry())} 个")

    mcp_connected = _auto_connect_mcp()
    if mcp_connected:
        print(f"MCP 连接数：{len(mcp_connected)} 个（{', '.join(mcp_connected)}）")
    print(f"总工具数：  {len(get_registry())} 个")
    print("\n命令：'tools' 查看工具  'history' 查看历史  'reset' 清空历史  'exit' 退出\n")

    session = AgentSession(max_history_turns=20)

    while True:
        try:
            user_input = input("你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() == "exit":
            print("再见！")
            break
        if user_input.lower() == "tools":
            from core.registry import get_tools_description
            print(get_tools_description())
            continue
        if user_input.lower() == "history":
            session.show_history()
            continue
        if user_input.lower() == "reset":
            session.reset()
            continue

        answer, trace = session.chat(
            user_input,
            verbose=True,
            verbose_llm=True,
            return_trace=True,
        )
        print(f"\n💬 最终答案：\n{answer}\n")
        print("-" * 52)


if __name__ == "__main__":
    main()
