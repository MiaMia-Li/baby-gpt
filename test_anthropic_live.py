"""
test_anthropic_live.py — 真·端到端验证 Claude tool_use（需要真实 ANTHROPIC_API_KEY，会调 API）

运行：./venv/bin/python test_anthropic_live.py
零成本打桩版（不花钱）：./venv/bin/python test_anthropic_tooluse.py

三步：① key 检查 → ② 鉴权+协议冒烟（1 次最小调用）→ ③ 真·端到端 tool_use
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 必须在 import core.agent 之前切到 Claude 路径
os.environ["LLM_PROVIDER"] = "anthropic"

from dotenv import load_dotenv
load_dotenv()

# ── 0. key 检查 ──────────────────────────────────────────
key = os.getenv("ANTHROPIC_API_KEY")
print("ANTHROPIC_API_KEY 已加载:", bool(key))
if not key:
    print("❌ 没读到 key，检查 .env 里是否有 ANTHROPIC_API_KEY")
    sys.exit(1)

# ── 1. 鉴权 + 协议冒烟（最小 API 调用）──────────────────────
print("\n[1] 鉴权 + 协议冒烟…")
import anthropic
client = anthropic.Anthropic()
r = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=64,
    messages=[{"role": "user", "content": "只回复两个字：你好"}],
)
text = "".join(b.text for b in r.content if b.type == "text")
print(f"  model: {r.model} | stop: {r.stop_reason} | text: {text}")
assert r.model.startswith("claude-opus-4-8"), f"模型不对: {r.model}"
print("  ✅ 鉴权 + 协议通过")

# ── 2. 真·端到端 tool_use（走完整 agent 路径）────────────────
print("\n[2] 真·端到端 tool_use…")
from core.registry import register_tool

calls = []  # 记录工具真实被调用的入参，证明是 Claude 走 tool_use 调的，不是脑补

@register_tool(description="两个整数相乘", params={"a": "被乘数", "b": "乘数"})
def multiply(a: int, b: int) -> str:
    calls.append((a, b))
    return str(a * b)

from core.agent import run_agent
ans = run_agent("请用 multiply 工具计算 1234 乘以 5678 等于多少", verbose=True)

print("\n  工具实际被调用:", calls)
print("  最终答案:", ans)
# 去掉千分位逗号（ASCII , 和全角 ，）再比对：Claude 可能输出 7,006,652
normalized = ans.replace(",", "").replace("，", "")
assert calls == [(1234, 5678)], f"❌ 工具没被正确调用: {calls}"
assert "7006652" in normalized, f"❌ 答案不含正确结果 7006652: {ans}"
print("\n✅✅ 真·端到端 tool_use 全链路通过 —— Sprint 1（Claude + tool_use）验证完成")
