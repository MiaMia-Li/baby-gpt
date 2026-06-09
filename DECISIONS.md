# HDB Agent — 设计决策记录

> 这个文件记录我们在搭建过程中做的每一个决策，以及背后的原因。
> 这是学习 ReAct 架构最有价值的部分：不只是"怎么做"，而是"为什么这么做"。

---

## 项目背景

**目标**：用新加坡 HDB 组屋分析场景，从零实现一个 ReAct Agent，学会 Agent 架构的底层原理。

**用户背景**：前后端开发经验，Agent 初学者，在新加坡生活。

---

## 决策 1：为什么选 HDB 分析这个场景？

**候选方案**：
- 旅行规划助手
- 租房决策 Agent（HDB）
- 数码选购 Agent
- 新马比价 Agent

**选择 HDB 的原因**：
1. 新加坡政府 `data.gov.sg` 提供完全免费的 HDB Resale 历史数据 API，不需要爬虫
2. 场景真实 —— 对在新加坡生活的人有直接使用价值
3. 工具调用自然（查价格 + 查地图 + 算费用），能覆盖 ReAct 的核心场景
4. API 返回的是真实数据，而不是 mock，验证效果更直接

---

## 决策 2：为什么不用 LangChain / LlamaIndex？

**LangChain 的问题**：
- 高度封装，初学者无法理解 ReAct 循环在哪里
- 版本变动频繁，教程代码经常跑不通
- 出问题时不知道调哪里

**我们的选择**：100 行原生 Python 实现完整 ReAct 循环。

**Trade-off**：
- 代码量多一点（约 150 行 vs 框架的 20 行）
- 但每一行你都知道它在做什么
- 等你理解底层原理后，用 LangGraph 之类的框架才能真正驾驭

---

## 决策 3：项目结构为什么这样分？

```
hdb-agent/
├── llm_client.py   ← 只负责"跟 LLM 说话"
├── tools.py        ← 只负责"定义工具和执行"
├── agent.py        ← 只负责"ReAct 循环逻辑"
└── main.py         ← 只负责"启动和用户交互"
```

**核心原则：关注点分离（Separation of Concerns）**

- `tools.py` 不知道 Agent 的存在 → 你可以把这些工具给任何 Agent 用
- `agent.py` 不关心工具怎么实现 → 你可以换 LLM 而不改 Agent 逻辑
- 这个结构让你以后加新功能（新工具、新 Agent 范式）时不需要改其他文件

---

## 决策 4：ReAct 的 Prompt 怎么设计？

**核心挑战**：怎么让 LLM 输出固定格式（Thought/Action/Observation）？

**方案 A**：用自然语言描述格式，让 LLM 自由发挥
- 优点：灵活
- 缺点：格式不稳定，解析困难

**方案 B（我们选的）**：在 System Prompt 里强制规定格式，用正则解析
```
每次只输出一步，格式严格如下：
Thought: 你的推理过程
Action: tool_name(param1="value1", param2="value2")
---或者---
Final Answer: 最终答案
```

**为什么**：对初学者来说，可预测的格式比灵活性更重要。
你能看到 LLM 的每一步思考，调试容易，理解清晰。

---

## 决策 5：工具用真实 API 还是 Mock？

**阶段 1（现在）**：先用 Mock 数据，让 ReAct 循环跑通
**阶段 2（下一步）**：接入真实 data.gov.sg API

**原因**：
- 先验证架构是否正确，再接真实数据
- 避免"API 调不通"这个外部问题干扰对 ReAct 本身的理解
- 这是工程上的标准做法：先 unit test，再 integration test

---

## 待决策

- [ ] 是否加入对话历史（让 Agent 记住上一轮问题）？
- [ ] 是否用 streaming 输出（实时看到 LLM 的 Thought 过程）？
- [ ] 接入真实 HDB API 后，错误处理怎么做？
