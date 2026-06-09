"""
core/registry.py — 工具注册中心（整个通用化改造的核心）

【这个文件解决的问题】
原来：TOOLS_REGISTRY 写死在 tools.py，加工具要改核心文件
现在：任何人在 skills/ 目录建文件 + 加 @register_tool，自动接入

【对外暴露的 3 个接口】
1. @register_tool  — 装饰器，用来声明一个函数是工具
2. auto_discover() — 自动扫描 skills/ 目录，触发所有注册
3. execute_tool()  — Agent 调用工具的统一入口
4. get_tools_description() — 自动生成 LLM 的工具说明书
"""

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

# 全局注册表：tool_name -> {func, description, params}
# 用下划线开头表示"内部变量，不要直接访问，用下面的函数"
_registry: dict = {}


def register_tool(description: str, params: dict[str, str] | None = None, name: str | None = None):
    """
    装饰器：把一个普通 Python 函数注册为 Agent 可调用的工具。

    用法：
        @register_tool(
            description="查询 HDB 历史成交价",
            params={
                "town": "区域名，如 Queenstown、Ang Mo Kio",
                "flat_type": "户型，如 2 room、3 room",
            }
        )
        def query_hdb_prices(town: str, flat_type: str, months: int = 3) -> str:
            ...

    【为什么 description 和 params 分开传，而不是解析 docstring？】
    Trade-off：
    - 解析 docstring：代码更简洁，但格式不统一容易解析失败
    - 显式传参（我们选的）：多写几行，但对 LLM 生成的说明书完全可控
    开源项目里，可控 > 简洁。
    """
    def decorator(func):
        tool_name = name or func.__name__

        # 从类型注解提取参数信息（默认值、是否必填）
        sig = inspect.signature(func)
        param_meta = {}
        for p_name, p in sig.parameters.items():
            param_meta[p_name] = {
                "required": p.default is inspect.Parameter.empty,
                "default":  None if p.default is inspect.Parameter.empty else p.default,
                "type":     p.annotation.__name__ if p.annotation != inspect.Parameter.empty else "str",
                "desc":     (params or {}).get(p_name, ""),
            }

        _registry[tool_name] = {
            "func":        func,
            "description": description,
            "params":      param_meta,
        }
        return func
    return decorator


def get_registry() -> dict:
    """返回当前所有已注册工具的快照。"""
    return dict(_registry)


def get_tools_description() -> str:
    """
    自动生成工具说明书字符串，直接放进 System Prompt。

    【为什么要自动生成而不是手写？】
    手写字符串和代码容易不同步：你加了新工具，忘了更新说明书，
    LLM 就不知道有这个工具。自动生成从注册表实时读取，永远同步。
    """
    if not _registry:
        return "当前没有可用工具。"

    lines = ["你可以使用以下工具（每次只调用一个）：\n"]
    for i, (tool_name, info) in enumerate(_registry.items(), 1):
        # 生成函数签名：required_param, optional_param=default
        sig_parts = []
        for p_name, meta in info["params"].items():
            if meta["required"]:
                sig_parts.append(p_name)
            else:
                sig_parts.append(f'{p_name}={repr(meta["default"])}')

        lines.append(f'{i}. {tool_name}({", ".join(sig_parts)})')
        lines.append(f'   说明：{info["description"]}')

        for p_name, meta in info["params"].items():
            req = "必填" if meta["required"] else f'可选，默认 {repr(meta["default"])}'
            desc = f"，{meta['desc']}" if meta["desc"] else ""
            lines.append(f'   - {p_name} ({meta["type"]}，{req}{desc})')

        lines.append("")

    return "\n".join(lines)


def execute_tool(tool_name: str, **kwargs) -> str:
    """
    Agent 调用工具的统一入口。

    【为什么要有这一层？】
    agent.py 不需要知道工具在哪里定义，只通过这个函数调用。
    以后加日志、限流、权限控制，只改这一个地方。
    """
    if tool_name not in _registry:
        available = list(_registry.keys())
        return f"错误：工具 '{tool_name}' 不存在。可用工具：{available}"
    try:
        return _registry[tool_name]["func"](**kwargs)
    except TypeError as e:
        return f"参数错误：{e}。请检查工具的参数格式。"
    except Exception as e:
        return f"工具执行出错：{e}"


def register_dynamic_tool(
    name: str,
    func,
    description: str,
    params: dict | None = None,
    overwrite_native: bool = False,
) -> str:
    """
    运行时动态注册工具的公开接口（替代直接操作 _registry）。

    overwrite_native=False 时，若同名原生工具已存在，自动加 _remote 后缀。
    返回实际注册使用的工具名。
    """
    actual_name = name
    if not overwrite_native and name in _registry:
        if "[远程 Skill]" not in _registry[name].get("description", ""):
            actual_name = f"{name}_remote"

    import inspect
    param_meta = {}
    sig = inspect.signature(func)
    for p_name, p in sig.parameters.items():
        param_meta[p_name] = {
            "required": p.default is inspect.Parameter.empty,
            "default":  None if p.default is inspect.Parameter.empty else p.default,
            "type":     p.annotation.__name__ if p.annotation != inspect.Parameter.empty else "str",
            "desc":     (params or {}).get(p_name, ""),
        }

    _registry[actual_name] = {
        "func":        func,
        "description": description,
        "params":      param_meta,
    }
    return actual_name


def auto_discover(*dirs: str) -> list[str]:
    """
    自动扫描一个或多个目录，导入所有模块，触发 @register_tool 注册。

    用法：auto_discover("tools", "skills")
    返回：成功加载的模块名列表
    """
    loaded = []
    for d in dirs:
        path = Path(d)
        if not path.exists():
            continue
        for module_info in pkgutil.iter_modules([str(path)]):
            module_name = f"{d}.{module_info.name}"
            importlib.import_module(module_name)
            loaded.append(module_name)
    return loaded


# ── 快速验证 ──────────────────────────────────
if __name__ == "__main__":
    # 模拟注册一个测试工具
    @register_tool(
        description="测试工具：返回 hello",
        params={"name": "要打招呼的名字"}
    )
    def hello(name: str) -> str:
        return f"Hello, {name}!"

    print(get_tools_description())
    print(execute_tool("hello", name="Singapore"))
