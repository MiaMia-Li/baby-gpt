# ── 基础镜像：Python 3.11 + Node.js（MCP 需要 npx）──────────────────
FROM python:3.11-slim

# 安装 Node.js（GitHub MCP 等 npx 启动的 Server 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 工作目录
WORKDIR /app

# 先装依赖（利用 Docker layer cache，代码改动时不重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY core/     ./core/
COPY tools/    ./tools/
COPY skills/   ./skills/
COPY mcp_server/ ./mcp_server/
COPY main.py   .
COPY .env.example .

# .env 由用户在运行时挂载，不打进镜像
# docker run -v $(pwd)/.env:/app/.env baby-gpt

CMD ["python", "main.py"]
