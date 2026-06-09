"""
mcp_server/install.py — 一键配置 Claude Desktop 或 Cursor

用法：
  python3.14 mcp_server/install.py           # 默认配置 Cursor
  python3.14 mcp_server/install.py cursor    # 配置 Cursor
  python3.14 mcp_server/install.py claude    # 配置 Claude Desktop
  python3.14 mcp_server/install.py all       # 两个都配置
"""

import json
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PYTHON_PATH  = sys.executable
SERVER_PATH  = str(PROJECT_ROOT / "mcp_server" / "server.py")

# 不同工具的配置文件路径
CONFIGS = {
    "cursor": Path.home() / ".cursor" / "mcp.json",
    "claude": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
}

MCP_ENTRY = {
    "command": PYTHON_PATH,
    "args":    [SERVER_PATH],
    "env": {
        "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
        "OPENAI_API_KEY":   os.getenv("OPENAI_API_KEY", ""),
    }
}


def write_config(target: str):
    config_path = CONFIGS[target]
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with open(config_path) as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                config = {}
    else:
        config = {}

    config.setdefault("mcpServers", {})
    config["mcpServers"]["baby-gpt"] = MCP_ENTRY

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"✅ [{target}] 配置已写入：{config_path}")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "cursor"

    if target == "all":
        write_config("cursor")
        write_config("claude")
    elif target in CONFIGS:
        write_config(target)
    else:
        print(f"未知目标：{target}，可选：cursor / claude / all")
        sys.exit(1)

    print(f"\n   Python : {PYTHON_PATH}")
    print(f"   Server : {SERVER_PATH}")
    print("\n下一步：完全重启 Cursor，在 Settings → MCP 里确认 baby-gpt 出现")


if __name__ == "__main__":
    main()
