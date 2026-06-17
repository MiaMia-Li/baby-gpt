"""
test_anthropic_tooluse.py — 验证 Claude 原生 tool_use 路径（Sprint 1）

没有真实 ANTHROPIC_API_KEY 也能跑：用打桩的 Claude client 模拟
tool_use → tool_result → final 的完整闭环，验证：
  1. registry → Anthropic tool schema 转换正确
  2. 单工具端到端：Claude 发起 tool_use → 真正执行工具 → 回传 tool_result → 出最终答案
  3. session 多轮历史仍是干净的字符串对（block 消息不污染跨轮历史）

运行：./venv/bin/python test_anthropic_tooluse.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 切到 Claude 路径（get_provider 运行时读取，所以在 import agent 前后设都行）
os.environ["LLM_PROVIDER"] = "anthropic"

import core.llm_client as llm_client
from core.registry import register_tool, get_tools_schema
from core.session import AgentSession

# ── 注册一个干净的验证工具 ──────────────────────────────
_calls = []  # 记录工具真实被调用的入参，证明"真的执行了"


@register_tool(
    description="把两个整数相加",
    params={"a": "第一个加数", "b": "第二个加数"},
)
def add_numbers(a: int, b: int) -> str:
    _calls.append((a, b))
    return str(a + b)


# ── 打桩的 Claude client ────────────────────────────────
class FakeBlock:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type, self.text, self.name, self.input, self.id = type, text, name, input, id


class FakeResp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


class FakeMessages:
    def __init__(self, script):
        self.script, self.calls, self.received = script, 0, []

    def create(self, **kwargs):
        self.received.append(kwargs)
        resp = self.script[self.calls]
        self.calls += 1
        return resp


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


# 脚本：turn1 先 tool_use 再终答；turn2 直接终答（靠历史"记得"上一轮）
_SCRIPT = [
    FakeResp([FakeBlock("tool_use", name="add_numbers", input={"a": 2, "b": 3}, id="toolu_1")], "tool_use"),
    FakeResp([FakeBlock("text", text="结果是 5。")], "end_turn"),
    FakeResp([FakeBlock("text", text="你刚才让我算的是 2 + 3。")], "end_turn"),
]
_fake = FakeAnthropic(_SCRIPT)
llm_client._get_anthropic = lambda: _fake  # 打桩

# ── 断言辅助 ────────────────────────────────────────────
_passed, _failed = 0, 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}")


# ── 1. schema 转换 ──────────────────────────────────────
print("\n[1] registry → Anthropic tool schema")
schema = {t["name"]: t for t in get_tools_schema()}
check("add_numbers 在 schema 里", "add_numbers" in schema)
add_schema = schema.get("add_numbers", {})
check("type=object + properties a/b",
      add_schema.get("input_schema", {}).get("type") == "object"
      and set(add_schema.get("input_schema", {}).get("properties", {})) == {"a", "b"})
check("int 注解 → JSON integer",
      add_schema.get("input_schema", {}).get("properties", {}).get("a", {}).get("type") == "integer")
check("a、b 都是 required",
      set(add_schema.get("input_schema", {}).get("required", [])) == {"a", "b"})

# ── 2 & 3. 端到端 + 多轮历史 ────────────────────────────
print("\n[2] 单工具端到端 + [3] 多轮历史")
session = AgentSession()

answer1 = session.chat("帮我算 2 + 3", verbose=False, verbose_llm=False)
check("工具真的被执行了，入参 (2, 3)", _calls == [(2, 3)])
check("turn1 最终答案含 '5'", "5" in answer1)

# 验证 tool_result 确实回传给了第二次 create
turn1_second_call_msgs = _fake.messages.received[1]["messages"]
tool_result_sent = any(
    isinstance(m.get("content"), list)
    and any(isinstance(b, dict) and b.get("type") == "tool_result"
            and b.get("tool_use_id") == "toolu_1" for b in m["content"])
    for m in turn1_second_call_msgs
)
check("tool_result(toolu_1) 回传给了 Claude", tool_result_sent)

# 历史必须是干净的字符串对（不含 block 列表），否则 session/tracer 会出问题
hist = session._history
check("turn1 后历史是 2 条（user+assistant）", len(hist) == 2)
check("历史 content 全是字符串（无 block 污染）",
      all(isinstance(m.get("content"), str) for m in hist))

answer2 = session.chat("我刚才让你算的是什么？", verbose=False, verbose_llm=False)
turn2_msgs = _fake.messages.received[2]["messages"]
# turn2 的入参里应包含 turn1 的历史（user 问题1 + assistant 答案1），再加 turn2 问题
check("turn2 把上一轮历史带上了",
      any(m.get("content") == "帮我算 2 + 3" for m in turn2_msgs)
      and any(isinstance(m.get("content"), str) and "5" in m["content"] for m in turn2_msgs))
check("turn2 答案合理", "2" in answer2 and "3" in answer2)

# ── 汇总 ────────────────────────────────────────────────
print(f"\n{'='*52}")
print(f"  通过 {_passed} / {_passed + _failed}")
print(f"{'='*52}")
sys.exit(0 if _failed == 0 else 1)
