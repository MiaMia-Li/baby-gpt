"""
tools/mcp_client.py — MCP Client

让 baby-gpt 作为 MCP Client，调用任意外部 MCP Server（GitHub、Notion、Filesystem…）。

【架构】
  baby-gpt → subprocess(stdio) → MCP Server
  协议：JSON-RPC 2.0，JSON Lines（每条消息一行 \n 结尾）

【线程模型】
  主线程      → 写 stdin（发请求）
  reader 线程 → 读 stdout（收响应），用 threading.Event 通知主线程
  stderr 线程 → 吸走 stderr，防止缓冲区满导致 Server 进程阻塞

【注册机制】
  connect_mcp_server 连接成功后，把 Server 的每个工具以
  "{server_name}_{mcp_tool_name}" 形式注册到 baby-gpt registry。
  Agent 可以直接调用，和本地工具完全一样。

【与 mcp_server/server.py 的区别】
  server.py：baby-gpt 作为 Server，把自己的工具暴露给外部
  mcp_client.py：baby-gpt 作为 Client，消费外部 Server 的工具
"""

import json
import os
import shlex
import subprocess
import threading

from core.registry import _registry, register_dynamic_tool, register_tool

# 全局连接池
_connections: dict[str, "MCPConnection"] = {}


class MCPConnection:
    """
    管理与单个 MCP Server 的进程连接。

    JSON-RPC 2.0 over stdio 协议实现：
      - 每条消息是一行完整 JSON（JSON Lines 格式）
      - Request 带 id，等待对应 id 的 Response
      - Notification 不带 id，不等待响应
    """

    def __init__(self, command: str, extra_env: dict | None = None, timeout: float = 60.0):
        self.server_name = ""
        self._timeout = timeout
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict] = {}
        self._closed = False

        env = {**os.environ, **(extra_env or {})}
        cmd = shlex.split(command) if isinstance(command, str) else command

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        threading.Thread(target=self._read_stdout, daemon=True, name=f"mcp-reader-{command[:20]}").start()
        threading.Thread(target=self._drain_stderr, daemon=True, name=f"mcp-stderr-{command[:20]}").start()

        self._initialize()

    # ── 底层 I/O ─────────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send(self, msg: dict):
        if self._closed:
            raise RuntimeError("连接已关闭")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        self._proc.stdin.flush()

    def _read_stdout(self):
        """后台线程：持续读 stdout，把带 id 的响应 dispatch 给等待的 Event。"""
        while not self._closed:
            try:
                raw = self._proc.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8").strip()
                if not text:
                    continue
                msg = json.loads(text)
                msg_id = msg.get("id")
                if msg_id is not None:
                    with self._lock:
                        self._responses[msg_id] = msg
                        if msg_id in self._pending:
                            self._pending[msg_id].set()
            except (json.JSONDecodeError, ValueError):
                continue
            except Exception:
                break

    def _drain_stderr(self):
        """后台线程：吸走 stderr，防止缓冲区满导致子进程阻塞。"""
        while not self._closed:
            try:
                if not self._proc.stderr.readline():
                    break
            except Exception:
                break

    # ── JSON-RPC 协议层 ───────────────────────────────────────────────

    def _request(self, method: str, params: dict | None = None) -> dict:
        """发送请求，阻塞等待响应（最多 self._timeout 秒）。"""
        req_id = self._next_id()
        event = threading.Event()
        with self._lock:
            self._pending[req_id] = event

        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

        if not event.wait(timeout=self._timeout):
            with self._lock:
                self._pending.pop(req_id, None)
                self._responses.pop(req_id, None)
            raise TimeoutError(f"MCP 请求超时：{method}（>{self._timeout}s）")

        with self._lock:
            resp = self._responses.pop(req_id)
            self._pending.pop(req_id, None)

        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"MCP error [{err.get('code', -1)}]: {err.get('message', str(err))}")

        return resp.get("result", {})

    def _notify(self, method: str, params: dict | None = None):
        """发送通知（无 id，不等待响应）。"""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    # ── MCP 协议层 ────────────────────────────────────────────────────

    def _initialize(self):
        """MCP 握手：initialize request + notifications/initialized。"""
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "baby-gpt", "version": "1.0.0"},
        })
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            return f"❌ 工具出错：{' '.join(parts)}"
        parts = [c["text"] for c in result.get("content", []) if c.get("type") == "text" and "text" in c]
        return "\n".join(parts) if parts else str(result)

    def close(self):
        self._closed = True
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            pass


# ── 注册到 Agent 的工具 ───────────────────────────────────────────────


@register_tool(
    description=(
        "连接外部 MCP Server，将其所有工具自动注册到 Agent 工具列表。"
        "连接后 Agent 可直接调用 {server_name}_{工具名}(参数…) 格式的工具。"
        "GitHub 示例：connect_mcp_server(server_name='github', "
        "command='npx -y @modelcontextprotocol/server-github', "
        "env_json='{\"GITHUB_PERSONAL_ACCESS_TOKEN\":\"ghp_xxx\"}')"
    ),
    params={
        "server_name": "Server 别名，工具以 {server_name}_{原名} 注册，如 github_get_file_contents",
        "command":     "启动 MCP Server 的命令，如 'npx -y @modelcontextprotocol/server-github'",
        "env_json":    "可选，额外环境变量 JSON，Token 等敏感信息放这里，不要硬编码",
        "timeout":     "可选，超时秒数，默认 60（npx 首次运行需要下载，建议设 120）",
    }
)
def connect_mcp_server(
    server_name: str,
    command: str,
    env_json: str = "{}",
    timeout: int = 60,
) -> str:
    if server_name in _connections:
        return (
            f"⚠️ '{server_name}' 已连接。\n"
            f"如需重连请先：disconnect_mcp_server(server_name='{server_name}')"
        )

    try:
        extra_env = json.loads(env_json) if env_json.strip() else {}
    except json.JSONDecodeError as e:
        return f"❌ env_json 解析失败：{e}\n请确保是合法 JSON，如 {{\"KEY\": \"value\"}}"

    try:
        conn = MCPConnection(command=command, extra_env=extra_env, timeout=float(timeout))
    except FileNotFoundError:
        cmd0 = shlex.split(command)[0]
        return (
            f"❌ 命令未找到：'{cmd0}'\n"
            f"请先安装：npm install -g @modelcontextprotocol/server-github\n"
            f"或者直接用 npx 自动安装，command 改为：npx -y @modelcontextprotocol/server-github"
        )
    except TimeoutError as e:
        return (
            f"❌ 握手超时：{e}\n"
            f"npx 首次运行需要下载包，请传 timeout=120 重试"
        )
    except Exception as e:
        return f"❌ 连接失败：{e}"

    try:
        tools = conn.list_tools()
    except Exception as e:
        conn.close()
        return f"❌ 握手失败，无法获取工具列表：{e}"

    if not tools:
        conn.close()
        return f"⚠️ '{server_name}' 连接成功，但 Server 没有提供任何工具。"

    _connections[server_name] = conn
    conn.server_name = server_name

    registered: list[str] = []
    for t in tools:
        tool_id = f"{server_name}_{t['name']}"
        schema    = t.get("inputSchema", {})
        props     = schema.get("properties", {})
        required  = schema.get("required", [])

        # 构建标准 param_meta，格式与 registry 内部一致
        param_meta = {
            p_name: {
                "required": p_name in required,
                "default":  None,
                "type":     p_schema.get("type", "str"),
                "desc":     p_schema.get("description", ""),
            }
            for p_name, p_schema in props.items()
        }

        # 闭包捕获 conn 和 mcp_tool_name，防止 loop variable 共享
        def _make_caller(c: MCPConnection, mcp_name: str):
            def _caller(**kwargs) -> str:
                if c._closed:
                    return f"❌ MCP Server '{c.server_name}' 已断开，请重新 connect_mcp_server"
                try:
                    return c.call_tool(mcp_name, kwargs)
                except Exception as ex:
                    return f"❌ {mcp_name} 调用失败：{ex}"
            _caller.__name__ = tool_id
            return _caller

        actual_name = register_dynamic_tool(
            name=tool_id,
            func=_make_caller(conn, t["name"]),
            description=f"[MCP:{server_name}] {t.get('description', '')}",
            params={p: param_meta[p]["desc"] for p in param_meta},
        )

        # register_dynamic_tool 用 inspect.signature 推导参数，**kwargs 会丢失参数名。
        # 直接覆盖 _registry 里的 params 元数据，确保 get_tools_description() 正确生成说明书。
        if actual_name in _registry:
            _registry[actual_name]["params"] = param_meta

        registered.append(actual_name)

    lines = [
        f"✅ 已连接 '{server_name}'，注册了 {len(registered)} 个工具：",
        *[f"  • {name}" for name in registered],
        "",
        f"直接调用示例：Action: {registered[0]}(…)" if registered else "",
    ]
    return "\n".join(lines)


@register_tool(
    description=(
        "列出已连接的 MCP Server 及其工具。"
        "detail=false（默认）只显示工具名列表；detail=true 显示参数和描述。"
    ),
    params={
        "server_name": "可选，只查指定 Server 的工具；留空列出全部",
        "detail":      "可选，true/false，是否显示参数和描述，默认 false",
    }
)
def list_mcp_connections(server_name: str = "", detail: str = "false") -> str:
    if not _connections:
        return "当前没有已连接的 MCP Server。使用 connect_mcp_server 连接。"

    if server_name and server_name not in _connections:
        return f"❌ 未找到 '{server_name}'，已连接：{list(_connections.keys())}"

    targets = {server_name: _connections[server_name]} if server_name else _connections
    show_detail = detail.lower() in ("true", "1", "yes")
    lines: list[str] = []

    for name, conn in targets.items():
        status = "🔴 已关闭" if conn._closed else "🟢 运行中"
        try:
            tools = conn.list_tools()
            lines.append(f"{status}  {name}  （{len(tools)} 个工具）")
            if show_detail:
                for t in tools:
                    props = list(t.get("inputSchema", {}).get("properties", {}).keys())
                    sig   = ", ".join(props)
                    desc  = t.get("description", "")[:60]
                    lines.append(f"  • {name}_{t['name']}({sig})")
                    if desc:
                        lines.append(f"    {desc}")
            else:
                # 紧凑格式：只列工具名，减少 Observation 体积
                tool_names = [f"{name}_{t['name']}" for t in tools]
                lines.append("  工具列表：" + ", ".join(tool_names))
                lines.append("  （如需参数和描述，请用 detail='true' 再次查询）")
        except Exception as e:
            lines.append(f"{status}  {name}：查询失败 — {e}")

    return "\n".join(lines)


@register_tool(
    description="断开并关闭指定的 MCP Server 连接，释放子进程",
    params={"server_name": "要断开的 Server 别名"}
)
def disconnect_mcp_server(server_name: str) -> str:
    if server_name not in _connections:
        return f"❌ 未找到 '{server_name}'，当前连接：{list(_connections.keys()) or '无'}"
    _connections[server_name].close()
    del _connections[server_name]
    return f"✅ 已断开并关闭 '{server_name}'"
