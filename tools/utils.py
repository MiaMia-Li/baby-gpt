"""tools/utils.py — 通用工具"""

import re
from core.registry import register_tool


@register_tool(
    description="计算数学表达式，支持 +、-、*、/、括号",
    params={"expr": "如 '2600 * 12' 或 '(5200 - 3800) / 3800 * 100'"}
)
def calculator(expr: str) -> str:
    if not re.match(r'^[\d\s\+\-\*\/\(\)\.]+$', expr):
        return f"不支持的表达式：{expr}。只允许数字和 +-*/ 运算。"
    try:
        return f"{expr} = {eval(expr)}"  # noqa: S307 — 已白名单过滤
    except Exception as e:
        return f"计算出错：{e}"
