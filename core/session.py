"""
core/session.py — 多轮对话会话管理

【解决的问题】
原来每次 run_agent 都从零开始，Agent 不记得上一轮说了什么。
Session 在多次调用之间保留对话历史，让 Agent 能追问、引用上文。

【实现原理】
messages 结构：
  [system_prompt]              ← 每次重新生成（工具可能动态变化）
  + [历史轮次的 user/assistant] ← Session 保存，每轮追加
  + [当前 user message]        ← 本次问题

【上下文窗口管理】
历史无限增长会超出 LLM 的 context window。
用 max_history_turns 控制保留最近 N 轮，超出后滑动丢弃最旧的。
"""

from core.agent import run_agent
from core.tracer import AgentTrace


class AgentSession:
    def __init__(self, max_history_turns: int = 20):
        """
        max_history_turns: 保留最近几轮对话历史。
        每轮 = 1 条 user + 若干 assistant/observation + 1 条 final answer
        设为 20 对大多数对话场景够用，超长对话自动滑动窗口。
        """
        self.max_history_turns = max_history_turns
        self._history: list[dict] = []  # 历史消息（不含 system prompt）
        self.turn_count: int = 0

    def chat(
        self,
        question:    str,
        verbose:     bool = True,
        verbose_llm: bool = False,
        return_trace: bool = False,
    ) -> str | tuple[str, AgentTrace]:
        """带历史的对话入口。每次调用自动把历史注入 run_agent。"""
        result = run_agent(
            user_question = question,
            verbose       = verbose,
            verbose_llm   = verbose_llm,
            return_trace  = True,
            history       = self._history,
        )
        answer, trace = result

        # 把本轮的完整消息追加到历史
        # trace 里已经有 input_snapshot，最后一步的 snapshot 就包含了本轮全部消息
        if trace.steps:
            last_snapshot = trace.steps[-1].llm_input_snapshot
            # last_snapshot = [system] + history + 本轮所有 user/assistant
            # 去掉第一条 system，取出本轮新增的部分
            new_messages = last_snapshot[1 + len(self._history):]
            # 再加上最终 answer 的 assistant 消息
            new_messages.append({"role": "assistant", "content": answer})
            self._history.extend(new_messages)

        # 滑动窗口：超出 max_history_turns 时丢弃最旧的一轮
        # 一轮至少有 user + assistant 两条消息，按消息数估算
        while len(self._history) > self.max_history_turns * 4:
            # 找到第一条 user 消息之后的下一条 user 消息，丢弃之前的
            drop_until = 1
            for i in range(1, len(self._history)):
                if self._history[i]["role"] == "user":
                    drop_until = i
                    break
            self._history = self._history[drop_until:]

        self.turn_count += 1

        return (answer, trace) if return_trace else answer

    def reset(self) -> None:
        """清空对话历史，开始新会话。"""
        self._history = []
        self.turn_count = 0
        print("✅ 对话历史已清空")

    def show_history(self) -> None:
        """打印当前对话历史摘要。"""
        if not self._history:
            print("（对话历史为空）")
            return
        print(f"\n📜 对话历史（共 {len(self._history)} 条消息，{self.turn_count} 轮）")
        for i, msg in enumerate(self._history):
            role    = msg["role"].upper()
            content = msg["content"][:80].replace("\n", " ")
            print(f"  [{i+1}] {role}: {content}...")
