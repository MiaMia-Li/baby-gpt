"""
mcp_server/server.py — baby-gpt 的 MCP Server

把 baby-gpt tools/ 里注册的所有工具，通过 MCP 协议暴露给外部。

启动方式：python3 mcp_server/server.py
"""

import sys
import os
import asyncio

# 所有日志必须输出到 stderr，stdout 是 MCP 协议专用通道
# 任何 print() 到 stdout 都会破坏协议握手
def log(msg: str):
    print(msg, file=sys.stderr, flush=True)

# 项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types
    except ImportError:
        log("❌ 请先安装 mcp 包：python3 -m pip install mcp --break-system-packages")
        sys.exit(1)

    # 加载工具（放在这里避免模块级副作用干扰 MCP 握手）
    log("🔧 加载工具...")
    try:
        from core.registry import auto_discover, get_registry, execute_tool
        # 用绝对路径，避免 Cursor 启动时 cwd 不对
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tools_dir  = os.path.join(project_root, "tools")
        skills_dir = os.path.join(project_root, "skills")
        # 切换到项目根目录，确保 import 正常
        os.chdir(project_root)
        auto_discover("tools", "skills")
        registry = get_registry()
        log(f"✅ 已加载 {len(registry)} 个工具：{list(registry.keys())}")
    except Exception as e:
        log(f"⚠️  工具加载失败：{e}")
        registry = {}

    server = Server("baby-gpt")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = []
        for name, info in registry.items():
            properties, required = {}, []
            for p_name, meta in info.get("params", {}).items():
                properties[p_name] = {
                    "type":        "string",
                    "description": meta.get("desc", ""),
                }
                if meta.get("required"):
                    required.append(p_name)

            tools.append(types.Tool(
                name        = name,
                description = (info.get("description") or "")[:200],
                inputSchema = {
                    "type":       "object",
                    "properties": properties,
                    "required":   required,
                }
            ))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        log(f"📞 调用工具：{name}({arguments})")
        try:
            from core.registry import execute_tool as _exec
            result = _exec(name, **arguments)
        except Exception as e:
            result = f"工具执行出错：{e}"
        log(f"✅ 工具返回：{str(result)[:100]}")
        return [types.TextContent(type="text", text=str(result))]

    log("🚀 MCP Server 启动，等待连接...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
