"""
core/tracer.py — Skill 执行追踪器

【解决的问题】
verbose_llm=True 打印了所有内容，但信息太多，
"Skill 有没有真正被执行"这件事淹没在大量输出里。

这个模块提供三个明确的验证点：
  1. Skill 指令是否出现在 messages（输入侧验证）
  2. LLM 输出是否引用了 Skill 定义的关键词（输出侧验证）
  3. 执行链路的可视化 timeline
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StepRecord:
    step:        int
    llm_input_snapshot: list[dict]   # 该步调用 LLM 时的完整 messages
    llm_output:  str
    action:      str | None = None
    observation: str | None = None


@dataclass
class AgentTrace:
    question: str
    steps:    list[StepRecord] = field(default_factory=list)
    answer:   str = ""

    # ── 验证方法 ────────────────────────────────────────────

    def skill_injected_at(self, skill_keyword: str) -> list[int]:
        """
        验证输入侧：Skill 指令在哪几步出现在 messages 里。
        返回步骤编号列表，空列表表示从未注入。
        """
        keyword_lower = skill_keyword.lower()
        injected = []
        for record in self.steps:
            for msg in record.llm_input_snapshot:
                if keyword_lower in msg.get("content", "").lower():
                    injected.append(record.step)
                    break
        return injected

    def skill_followed_in_output(self, *keywords: str) -> dict[str, list[int]]:
        """
        验证输出侧：Skill 定义的关键词出现在哪几步的 LLM 输出里。
        例如：skill_followed_in_output("npx skills", "leaderboard", "install count")
        """
        result = {}
        for kw in keywords:
            kw_lower = kw.lower()
            result[kw] = [
                r.step for r in self.steps
                if kw_lower in r.llm_output.lower()
            ]
        return result

    def print_timeline(self):
        """打印清晰的执行 timeline，突出 Skill 注入时刻。"""
        print(f"\n{'━'*56}")
        print(f"  执行 Timeline：{self.question[:40]}...")
        print(f"{'━'*56}")
        for r in self.steps:
            # 统计该步 messages 总长度（上下文大小的直观指标）
            ctx_chars = sum(len(m.get("content","")) for m in r.llm_input_snapshot)
            action_str = f"→ {r.action}" if r.action else "→ Final Answer"
            print(f"\n  步骤 {r.step}  [上下文 {ctx_chars} 字符]")
            print(f"  {action_str}")
            if r.observation:
                # 检测这一步是否注入了 Skill
                if "=== Skill:" in (r.observation or ""):
                    skill_name = r.observation.split("=== Skill:")[1].split("===")[0].strip()
                    print(f"  ⚡ Skill '{skill_name}' 指令已注入 messages")
                else:
                    obs_preview = r.observation[:80].replace('\n', ' ')
                    print(f"  Obs: {obs_preview}...")
        print(f"\n  最终答案: {self.answer[:100]}...")
        print(f"{'━'*56}")

    def verify(self, skill_keyword: str, output_keywords: list[str]) -> bool:
        """
        一键验证：Skill 是否真的加载并执行了。

        判断标准：
          ✅ Skill 指令出现在至少一步的 LLM 输入里（注入）
          ✅ LLM 输出里至少有一个 output_keyword（行为符合）
        两个都满足才算"真正执行了"。
        """
        injected_steps = self.skill_injected_at(skill_keyword)
        output_hits    = self.skill_followed_in_output(*output_keywords)

        print(f"\n{'─'*56}")
        print("  Skill 执行验证报告")
        print(f"{'─'*56}")

        if injected_steps:
            print(f"  ✅ 输入侧：Skill 指令在第 {injected_steps} 步注入了 messages")
        else:
            print(f"  ❌ 输入侧：未检测到 '{skill_keyword}' 被注入 messages")

        any_output_hit = False
        for kw, steps in output_hits.items():
            if steps:
                print(f"  ✅ 输出侧：关键词 '{kw}' 出现在第 {steps} 步的 LLM 输出里")
                any_output_hit = True
            else:
                print(f"  ⚠️  输出侧：关键词 '{kw}' 未出现在任何步骤的输出里")

        success = bool(injected_steps) and any_output_hit
        print(f"\n  结论：{'✅ Skill 已注入并生效' if success else '❌ Skill 未生效'}")
        print(f"{'─'*56}\n")
        return success
