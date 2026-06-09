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
