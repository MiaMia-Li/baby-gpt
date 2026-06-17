"""
core/agent.py — 通用 ReAct 主循环

【和原来的区别】
原来：直接 import TOOLS_REGISTRY、TOOLS_DESCRIPTION（绑定了具体工具）
现在：从 registry 动态获取（不知道、也不关心有哪些工具）

这个文件不需要随工具的增减而修改。
"""

import re
from pathlib import Path
from datetime import datetime
from core.llm_client import chat, set_log_file, get_provider, chat_anthropic
from core.registry import get_tools_description, get_tools_schema, execute_tool, _registry
from core.tracer import AgentTrace, StepRecord

MAX_OBS = 3000          # Observation 截断上限，两条路径共用
MAX_ITERATIONS = 25     # tool_use 循环的硬上限，防止失控


def _build_system_prompt() -> str:
    """每次运行时实时生成 System Prompt，确保工具说明书和注册表同步。"""
    return f"""你是一个智能助手，能够使用工具来回答用户的问题。
你还具备自主扩展能力：当现有工具无法完成任务时，你会主动发现并加载新工具。

{get_tools_description()}

【严格遵守的输出格式】
每次回复只能是以下两种格式之一：

格式一：需要调用工具时
Thought: 你的推理过程
Action: tool_name(param1="value1", param2="value2")

格式二：已有足够信息时
Thought: 我已经有足够信息了
Final Answer: 给用户的完整答案（使用中文，清晰友好）

【能力自扩展规则 — 最重要】
当你判断现有工具无法完成用户请求时（例如生成图片、搜索网页、发送邮件等），
禁止直接说"我做不到"或放弃。必须按以下步骤主动扩展：

第一步：识别缺少什么能力
  Thought: 用户需要图片生成能力，但我当前没有这个工具

第二步：如果用户已给出 GitHub URL 或包名，直接加载；否则先搜索
  - 用户给了 URL → 直接: load_skill(url="用户给的URL")
  - 没有 URL    → 先: find_skills(query="英文关键词")

第三步：用 load_skill 加载找到的 skill
  Action: load_skill(url="找到的URL或包名")

第四步：调用刚加载的工具完成任务

常见能力缺口与搜索关键词对照（仅当工具列表里真的没有时才搜索）：
- 搜索网页 / 查新闻      → "web search"
- 发送邮件               → "email"
- 代码执行               → "code execution"
- 视频生成               → "video generation"

⚠️ 重要：调用 find_skills 之前，必须先确认工具列表里没有能完成任务的工具。
   如果工具列表里有 generate_image，直接调用它，不要再去搜索。

【主动记忆规则】
当用户在对话中提到以下类型的信息时，在回答之前先调用 remember() 存起来：
- 个人偏好（"我喜欢..."、"我不喜欢..."、"我习惯..."）
- 个人事实（名字、预算、工作地点、家庭情况）
- 重要决定（"我决定..."、"我打算..."）
- 明确要求（"每次都..."、"以后帮我..."）

不需要用户说"帮我记住"，自动判断并存储。
普通闲聊、一次性问题不需要存。

【其他规则】
- 每次只输出一个 Action，等 Observation 后再继续
- 不要猜测数据，必须通过工具获取
- 如果工具返回错误，换个参数或换个思路
"""


def _build_system_prompt_toolmode() -> str:
    """
    Claude tool_use 模式的 System Prompt。

    【和 ReAct 版的区别】
    - 不需要"Action: / Final Answer:"文本格式 —— Claude 用原生 tool_use
    - 不需要内嵌工具说明书 —— 工具通过 API 的 tools 参数以 schema 传入
    保留的是行为约束：自主扩展能力 + 主动记忆 + 不猜数据。
    """
    return """你是一个智能助手，能够使用工具来回答用户的问题。
可用工具通过 API 的 tools 参数提供，需要时直接调用即可（无需输出任何特定文本格式）。

【能力自扩展规则 — 最重要】
当现有工具无法完成任务时（例如生成图片、搜索网页、发送邮件等），
禁止直接说"我做不到"或放弃，必须主动扩展：
- 用户给了 GitHub URL / 包名 → 直接调用 load_skill(url="...")
- 没有 URL → 先 find_skills(query="英文关键词")，再用 load_skill 加载
- ⚠️ 调用 find_skills 前先确认工具列表里确实没有能完成任务的工具

【主动记忆规则】
当用户提到个人偏好、个人事实（名字/预算/工作地点）、重要决定、明确要求时，
在回答之前先调用 remember() 存起来。不需要用户说"帮我记住"，自动判断。
普通闲聊、一次性问题不需要存。

【其他规则】
- 不要猜测数据，必须通过工具获取
- 工具返回错误时，换个参数或换个思路
- 信息足够时，用中文给出清晰、友好的最终答案"""


def _parse_output(text: str) -> dict:
    """
    解析 LLM 输出，返回 action 或 final_answer。

    【为什么不用简单正则匹配括号？】
    calculator(expr="(1400 / 3800) * 100") 里面有嵌套括号。
    简单的 r'\\(.*?\\)' 会在第一个 ) 就停止，截断参数。
    正确做法：找到开始括号后，用计数器跟踪括号深度，
    深度回到 0 才是真正的结束位置。
    """
    if m := re.search(r'Final Answer[:：]\s*(.+)', text, re.DOTALL):
        return {"type": "final_answer", "content": m.group(1).strip()}

    action_match = re.search(r'Action[:：]\s*(\w+)\(', text)
    if action_match:
        tool_name = action_match.group(1).strip()
        start = action_match.end() - 1  # 开括号的位置
        depth, end = 0, start
        for i, ch in enumerate(text[start:], start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        args_str = text[start + 1 : end]
        return {"type": "action", "tool": tool_name, "args_str": args_str}

    # 降级处理：LLM 在长对话中可能忘记加 "Final Answer:" 前缀，
    # 但实际已输出有效内容。内容足够长且不含 Action 关键词，
    # 视为省略了前缀的 Final Answer，避免触发 else 分支陷入混乱。
    stripped = text.strip()
    if len(stripped) > 80 and not re.search(r'\bAction[:：]', stripped):
        return {"type": "final_answer", "content": stripped}

    return {"type": "unknown", "raw": text}


def _parse_args(args_str: str, tool_name: str = "") -> dict:
    """
    把 LLM 生成的参数字符串解析为 Python dict。

    支持两种 LLM 输出格式：
      关键字参数：town="Queenstown", months=3     → {"town": "Queenstown", "months": 3}
      位置参数：  "find-skills"                   → {"query": "find-skills"}  (按函数签名顺序映射)

    【为什么要支持位置参数】
    LLM 有时候会省略参数名，直接写值。
    纯关键字解析在这种情况下会失败，导致 Agent 陷入错误循环。
    """
    if not args_str.strip():
        return {}

    # 优先尝试关键字参数解析
    try:
        result = eval(f"dict({args_str})")  # noqa: S307
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # 降级：尝试位置参数解析，按函数签名顺序映射
    try:
        positional = eval(f"({args_str},)")  # noqa: S307 — 解析成 tuple
        if tool_name and tool_name in _registry:
            param_names = list(_registry[tool_name]["params"].keys())
            return {param_names[i]: v for i, v in enumerate(positional) if i < len(param_names)}
    except Exception:
        pass

    return {}


def _run_anthropic_loop(
    user_question: str,
    history:       list[dict] | None,
    verbose:       bool,
    verbose_llm:   bool,
    trace:         AgentTrace,
) -> str:
    """
    Claude 原生 tool_use 主循环。

    和 ReAct 路径的本质区别：
    - 工具调用是结构化的 tool_use block，block.input 已是 dict —— 直接
      execute_tool(name, **input)，不再正则解析文本、不再 eval 参数。
    - 工具结果作为 tool_result block 回传，而非 "Observation:" 文本。

    会话历史兼容性：
    循环内部用 Claude 的 block 消息（含 thinking / tool_use / tool_result），
    但写进 trace 的 llm_input_snapshot 用一份"干净的 ReAct 形状"快照
    （[system] + history + [user]，content 全是字符串），这样 session.py 的
    历史重建逻辑和 tracer.py 的统计完全不用改 —— 跨轮历史仍是纯字符串对。
    """
    system_prompt = _build_system_prompt_toolmode()

    # 干净快照：仅供 session/tracer 消费，不含循环内部的 block 消息
    clean_snapshot = (
        [{"role": "system", "content": system_prompt}]
        + (history or [])
        + [{"role": "user", "content": user_question}]
    )

    # 工作消息：循环内部真正发给 Claude 的（带 block 的）消息
    messages: list[dict] = [dict(m) for m in (history or [])]
    messages.append({"role": "user", "content": user_question})

    step = 0
    while True:
        step += 1
        if step > MAX_ITERATIONS:
            answer = f"（已连续执行 {MAX_ITERATIONS} 步仍未完成，停止以避免失控。）"
            trace.steps.append(StepRecord(step=step, llm_input_snapshot=clean_snapshot, llm_output=answer))
            trace.answer = answer
            return answer

        if verbose:
            print(f"\n── 第 {step} 步 {'─'*36}")

        # 每轮重新生成 tools：load_skill 在循环中途加载的新工具能立即可用
        tools = get_tools_schema()
        resp = chat_anthropic(messages, tools, system_prompt, verbose_llm=verbose_llm)

        # 把完整 content（含 thinking / tool_use）原样回写为 assistant 轮 —— tool_use 协议要求
        messages.append({"role": "assistant", "content": resp.content})

        text_out = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        if verbose and not verbose_llm and text_out:
            print(f"LLM：\n{text_out}")

        # 不是 tool_use 就是终点（end_turn / max_tokens 等）
        if resp.stop_reason != "tool_use":
            trace.steps.append(StepRecord(step=step, llm_input_snapshot=clean_snapshot, llm_output=text_out))
            trace.answer = text_out
            if verbose:
                print("  ✅ Final Answer")
            return text_out

        # 执行本轮所有 tool_use block，结果作为 tool_result 回传
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            observation = execute_tool(block.name, **dict(block.input))
            if len(observation) > MAX_OBS:
                observation = (
                    observation[:MAX_OBS]
                    + f"\n\n…[已截断，原始输出共 {len(observation)} 字符]"
                )
            if verbose:
                args_preview = str(dict(block.input))[:60]
                obs_preview  = observation[:100].replace("\n", " ")
                print(f"  🔧 {block.name}({args_preview})")
                print(f"  📋 {obs_preview}{'...' if len(observation) > 100 else ''}")

            trace.steps.append(StepRecord(
                step=step, llm_input_snapshot=clean_snapshot,
                llm_output=text_out, action=block.name, observation=observation,
            ))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": observation,
            })

        messages.append({"role": "user", "content": tool_results})


def run_agent(
    user_question: str,
    verbose:       bool            = True,
    verbose_llm:   bool            = False,
    return_trace:  bool            = False,
    history:       list[dict] | None = None,
) -> str | tuple[str, AgentTrace]:
    """
    通用 ReAct Agent 入口。

    参数说明
    --------
    user_question
        用户的自然语言问题或指令。

    verbose
        True（默认）：控制台打印每一步的步骤编号和 Observation 摘要，
        方便实时看到 Agent 在做什么。
        False：静默运行，只返回最终答案。

    verbose_llm
        True：把每次 LLM 调用的完整输入（messages）和完整输出写入
        logs/ 目录下的 .log 文件，同时控制台只保留 Timeline。
        用于调试 LLM 行为或分析 prompt 效果，日常使用不需要开。
        False（默认）：不写日志文件。

    return_trace
        True：返回 (answer, AgentTrace) 元组，AgentTrace 包含每步的
        上下文快照、工具调用记录，可调用 trace.print_timeline() 可视化。
        False（默认）：只返回 answer 字符串。

    history
        上一轮对话的消息列表（不含 system prompt），用于多轮对话。
        由 AgentSession 自动管理，直接使用 run_agent 时传 None 即可。
        格式：[{"role": "user", "content": "..."}, {"role": "assistant", ...}, ...]

    安全机制
    --------
    没有硬性步骤上限（通用 Agent 不应该被随意截断）。
    改用死循环检测：连续 3 次调用同一工具时，强制注入提示让 LLM 换思路。
    """
    # 初始化日志文件（verbose_llm 时写文件，不写控制台）
    log_path = None
    if verbose_llm:
        log_dir  = Path("logs")
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_q   = re.sub(r'[^\w一-鿿]', '_', user_question[:30])
        log_path = log_dir / f"llm_{ts}_{safe_q}.log"
        set_log_file(log_path)
        print(f"\n📝 LLM 完整日志写入：{log_path}")
    else:
        set_log_file(None)

    # Claude 路径：原生 tool_use（结构化调用），与 DeepSeek 的 ReAct 文本路径分流
    if get_provider() == "anthropic":
        trace = AgentTrace(question=user_question)
        if verbose:
            print(f"\n{'='*52}")
            print(f"问题：{user_question}")
            print(f"{'='*52}")
        answer = _run_anthropic_loop(user_question, history, verbose, verbose_llm, trace)
        return (answer, trace) if return_trace else answer

    # DeepSeek 路径（默认）：ReAct 文本解析
    # system prompt 放最前，历史对话居中，当前问题在最后
    # 注意：system prompt 每次重新生成，确保工具列表是最新的
    messages = (
        [{"role": "system", "content": _build_system_prompt()}]
        + (history or [])
        + [{"role": "user", "content": user_question}]
    )
    trace = AgentTrace(question=user_question)
    recent_actions: list[str] = []   # 最近 3 次 action，用于检测死循环

    if verbose:
        print(f"\n{'='*52}")
        print(f"问题：{user_question}")
        print(f"{'='*52}")

    step = 0
    while True:
        step += 1
        if verbose:
            print(f"\n── 第 {step} 步 {'─'*36}")

        # 记录本步调用 LLM 时的完整 messages 快照
        input_snapshot = [dict(m) for m in messages]
        llm_output     = chat(messages, verbose_llm=verbose_llm)

        if verbose and not verbose_llm:
            print(f"LLM：\n{llm_output}")

        parsed = _parse_output(llm_output)

        if parsed["type"] == "final_answer":
            trace.steps.append(StepRecord(step=step, llm_input_snapshot=input_snapshot, llm_output=llm_output))
            trace.answer = parsed["content"]
            if verbose:
                print(f"  ✅ Final Answer")
            return (parsed["content"], trace) if return_trace else parsed["content"]

        if parsed["type"] == "action":
            # 死循环检测：连续 3 次调用同一个工具，强制中断
            recent_actions.append(parsed["tool"])
            if len(recent_actions) > 3:
                recent_actions.pop(0)
            if len(recent_actions) == 3 and len(set(recent_actions)) == 1:
                stuck_tool = parsed["tool"]
                messages.append({"role": "assistant", "content": llm_output})
                messages.append({"role": "user", "content": (
                    f"你已经连续 3 次调用 {stuck_tool}，陷入循环了。"
                    f"这个工具需要的执行环境可能不可用。"
                    f"请换一个方案：尝试其他工具，或者直接告诉用户需要什么前提条件。"
                )})
                recent_actions.clear()
                continue

            kwargs = _parse_args(parsed["args_str"], tool_name=parsed["tool"])
            kwargs.pop("tool_name", None)  # LLM 偶尔会把 tool_name 带进参数里，防止冲突
            observation = execute_tool(parsed["tool"], **kwargs)

            # 超长 Observation 截断：避免 LLM 被淹没后丢失方向感
            MAX_OBS = 3000
            if len(observation) > MAX_OBS:
                observation = (
                    observation[:MAX_OBS]
                    + f"\n\n…[已截断，原始输出共 {len(observation)} 字符，"
                    f"如需完整内容请缩小查询范围或分页获取]"
                )

            # 无论 verbose_llm 是否开启，都打印简洁的步骤卡片
            if verbose:
                obs_preview = observation[:100].replace("\n", " ")
                print(f"  🔧 {parsed['tool']}({parsed['args_str'][:60]})")
                print(f"  📋 {obs_preview}{'...' if len(observation) > 100 else ''}")

            trace.steps.append(StepRecord(
                step=step, llm_input_snapshot=input_snapshot,
                llm_output=llm_output, action=parsed["tool"], observation=observation,
            ))
            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "user",      "content": f"Observation: {observation}"})

        else:
            trace.steps.append(StepRecord(step=step, llm_input_snapshot=input_snapshot, llm_output=llm_output))
            messages.append({"role": "assistant", "content": llm_output})
            messages.append({"role": "user",      "content": "请严格按照格式输出 Action 或 Final Answer。"})

        # while True 通过 return 退出，这里理论上不可达
        # 但 Python 需要函数有返回值，作为保险

