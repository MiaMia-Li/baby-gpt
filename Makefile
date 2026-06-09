# ── baby-gpt 快捷命令 ────────────────────────────────────────────────
PYTHON = venv/bin/python3

# ── 本地开发 ─────────────────────────────────────────────────────────

install:          ## 初始化 venv 并安装依赖
	python3 -m venv venv
	venv/bin/python3 -m pip install --upgrade pip
	venv/bin/python3 -m pip install -r requirements.txt

run:              ## 启动 Agent
	$(PYTHON) main.py

test-mcp:         ## 测试 GitHub MCP 连接
	$(PYTHON) test_github_mcp.py

mcp-server:       ## 启动 MCP Server（供 Claude Desktop 调用）
	$(PYTHON) mcp_server/server.py

# ── Docker 方式（给他人使用）─────────────────────────────────────────

docker-build:     ## 构建 Docker 镜像
	docker build -t baby-gpt .

docker-run:       ## 运行 Docker 容器（需要当前目录有 .env 文件）
	docker run -it --rm \
		-v $(shell pwd)/.env:/app/.env \
		-v baby-gpt-memory:/app/memory_db \
		baby-gpt

docker-run-mcp:   ## 以 MCP Server 模式运行（供 Claude Desktop 调用）
	docker run -i --rm \
		-v $(shell pwd)/.env:/app/.env \
		baby-gpt python mcp_server/server.py

.PHONY: install run test-mcp mcp-server docker-build docker-run docker-run-mcp
