# baby-gpt — 通用 ReAct Agent

从零实现的通用 Agent，完整覆盖 ReAct 架构的每一个环节。
Hello Agents 课程毕业设计项目。

## 快速开始

### 方式一：本地运行（开发者）

**前置条件**：Python 3.10+，Node.js 18+（MCP 功能需要）

```bash
# 1. 克隆仓库
git clone <repo-url>
cd baby-gpt

# 2. 安装依赖
make install
# 或者手动：python3 -m venv venv && venv/bin/python3 -m pip install -r requirements.txt

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY

# 4. 启动
make run
```

### 方式二：Docker 运行（推荐分发给他人）

**前置条件**：Docker

```bash
# 1. 构建镜像
make docker-build

# 2. 配置 Key（只需做一次）
cp .env.example .env
# 编辑 .env

# 3. 运行
make docker-run
```

## 配置文件

复制 `.env.example` 为 `.env`，填入以下 Key：

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek API Key（主 LLM） |
| `OPENAI_API_KEY` | 可选 | 图片生成功能需要 |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | 可选 | 启用 GitHub MCP（26 个工具） |
| `TAVILY_API_KEY` | 可选 | 更好的网络搜索，无则自动用 DuckDuckGo |

## 功能

- **ReAct 核心循环** — Thought → Action → Observation 迭代
- **工具注册系统** — `@register_tool` 装饰器，`tools/` 目录自动扫描
- **远程 Skill 加载** — `find_skills` + `load_skill`，运行时扩展能力
- **能力自扩展** — 遇到未知任务自动搜索并安装工具
- **多轮对话** — `AgentSession` 滑动窗口，保留最近 20 轮上下文
- **网络搜索** — DuckDuckGo（免费）/ Tavily（可选）双后端
- **GitHub MCP** — 连接 GitHub MCP Server，读写仓库、搜索代码、管理 Issue/PR
- **RAG 记忆** — Chroma 向量数据库，`remember` / `recall` 工具
- **MCP Server** — 把自己的工具暴露给 Claude Desktop / Cursor

## 项目结构

```
baby-gpt/
├── core/
│   ├── agent.py       ← ReAct 主循环
│   ├── llm_client.py  ← LLM 调用（支持 DeepSeek / OpenAI）
│   ├── registry.py    ← 工具注册中心
│   ├── session.py     ← 多轮对话管理
│   └── tracer.py      ← 可观测性（Timeline + 日志）
├── tools/
│   ├── shell.py       ← Shell 执行（黑名单安全模型）
│   ├── filesystem.py  ← 文件读写
│   ├── search.py      ← 网络搜索
│   ├── memory.py      ← RAG 记忆
│   ├── utils.py       ← 计算器等工具
│   └── mcp_client.py  ← MCP Client（连接外部 MCP Server）
├── skills/
│   └── remote_loader.py  ← find_skills + load_skill
├── mcp_server/
│   └── server.py      ← MCP Server（暴露给 Claude Desktop）
├── main.py            ← 启动入口
├── Dockerfile
├── Makefile
└── .env.example
```

## 接入 Claude Desktop（MCP Server 模式）

在 `~/Library/Application Support/Claude/claude_desktop_config.json` 添加：

```json
{
  "mcpServers": {
    "baby-gpt": {
      "command": "/path/to/baby-gpt/venv/bin/python",
      "args": ["/path/to/baby-gpt/mcp_server/server.py"]
    }
  }
}
```

或者 Docker 方式：

```json
{
  "mcpServers": {
    "baby-gpt": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "/path/to/.env:/app/.env", "baby-gpt", "python", "mcp_server/server.py"]
    }
  }
}
```
