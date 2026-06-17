"""
core/llm_client.py — 负责"跟 LLM 说话"这一件事

verbose_llm 模式：把每次 LLM 输入/输出完整写入日志文件，控制台不输出。
日志文件：logs/llm_<timestamp>.log，每次 run_agent 调用创建一个新文件。
"""

import json
import os
from pathlib import Path
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# DeepSeek API 兼容 OpenAI 格式，只需换 base_url 和 api_key
# 在 .env 里配置 DEEPSEEK_API_KEY=sk-xxx
import os
_api_key  = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
_base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
client    = OpenAI(api_key=_api_key, base_url=_base_url)


def get_provider() -> str:
    """
    当前 LLM 提供方：'anthropic'（Claude 原生 tool_use）或 'deepseek'（默认，ReAct 文本）。
    运行时读环境变量 LLM_PROVIDER，方便切换和测试。
    """
    return os.getenv("LLM_PROVIDER", "deepseek").strip().lower()

# 当前会话的日志文件路径（由 agent.py 在每次 run_agent 开始时设置）
_current_log_file: Path | None = None
_call_counter: int = 0


def set_log_file(path: Path | None) -> None:
    """由 agent.py 调用，设置本次 run_agent 的日志文件路径。"""
    global _current_log_file, _call_counter
    _current_log_file = path
    _call_counter = 0
    if path:
        path.parent.mkdir(exist_ok=True)
        path.write_text("", encoding="utf-8")  # 清空/创建文件


def _write_log(text: str) -> None:
    if _current_log_file:
        with open(_current_log_file, "a", encoding="utf-8") as f:
            f.write(text)


def chat(messages: list[dict], model: str = "deepseek-v4-flash", verbose_llm: bool = False) -> str:
    global _call_counter
    _call_counter += 1

    if verbose_llm and _current_log_file:
        sep = "=" * 70
        _write_log(f"\n{sep}\n📤  CALL #{_call_counter}  发送给 LLM 的完整 messages\n{sep}\n")
        for i, msg in enumerate(messages):
            _write_log(f"\n[{i+1}] {msg['role'].upper()}:\n{msg['content']}\n")
        _write_log(f"\n{'-' * 70}\n")

    response = client.chat.completions.create(model=model, messages=messages)
    reply    = response.choices[0].message.content

    if verbose_llm and _current_log_file:
        _write_log(f"📥  LLM 回复 #{_call_counter}:\n{reply}\n")

    return reply


# ── Anthropic Claude（原生 tool_use 路径）────────────────────────
# 懒加载：DeepSeek 用户不装 anthropic 也能正常跑。
_anthropic_client = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic  # 局部 import，避免对 DeepSeek 路径的硬依赖
        _anthropic_client = anthropic.Anthropic()  # 读环境变量 ANTHROPIC_API_KEY
    return _anthropic_client


def chat_anthropic(
    messages:    list[dict],
    tools:       list[dict],
    system:      str,
    model:       str | None = None,
    verbose_llm: bool = False,
):
    """
    调用 Claude Messages API（原生 tool_use），返回原始 response 对象。
    由 agent 的工具循环解析 content blocks 与 stop_reason。

    - 默认 claude-opus-4-8（可用 ANTHROPIC_MODEL 覆盖）
    - 开启 adaptive thinking：agent 任务复杂，让模型自行决定思考深度
    - 非流式 + max_tokens 8192：单步够用，且不触发 SDK 的长输出超时保护
    """
    global _call_counter
    _call_counter += 1
    model = model or os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

    if verbose_llm and _current_log_file:
        sep = "=" * 70
        _write_log(f"\n{sep}\n📤  CALL #{_call_counter}  发送给 Claude（tool_use 模式）\n{sep}\n")
        _write_log(f"\n[SYSTEM]:\n{system}\n")
        for i, msg in enumerate(messages):
            _write_log(f"\n[{i+1}] {msg['role'].upper()}:\n{msg['content']}\n")
        _write_log(f"\n{'-' * 70}\n")

    resp = _get_anthropic().messages.create(
        model      = model,
        max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
        system     = system,
        tools      = tools,
        thinking   = {"type": "adaptive"},
        messages   = messages,
    )

    if verbose_llm and _current_log_file:
        text_out   = "".join(b.text for b in resp.content if b.type == "text")
        tool_calls = [f"{b.name}({b.input})" for b in resp.content if b.type == "tool_use"]
        _write_log(f"📥  Claude 回复 #{_call_counter}（stop={resp.stop_reason}）:\n")
        if text_out:
            _write_log(f"  text: {text_out}\n")
        for tc in tool_calls:
            _write_log(f"  tool_use: {tc}\n")

    return resp
