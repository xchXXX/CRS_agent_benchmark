# Agent Loop 架构演进分析

> 编写日期：2026-03-18
> 状态：方案讨论阶段

## 一、背景

当前系统采用 Orchestrator + Handler 编排器模式，随着业务能力扩展（DTC 诊断、文档搜索、参数查询、ECU 数据读取、图像绘制等），现有架构在灵活性和可扩展性上面临瓶颈。本文档对比分析当前架构与 Agent Loop 架构的优劣，并给出演进建议。

## 二、当前架构

### 核心流程

```
用户请求 → Orchestrator → IntentRouter(规则引擎 + AI分类器) → 固定Handler → 返回
```

### 关键组件

| 组件 | 职责 |
|------|------|
| ChatOrchestrator | 中央编排器，协调全流程 |
| RuleEngine | 正则/关键词匹配，快速意图识别 |
| AIIntentClassifier | Ollama/OpenRouter 模型做意图分类 |
| IntentRouter | 综合规则+AI结果，路由到对应Handler |
| DocSearchHandler | 资料搜索处理 |
| FaultDiagHandler | 故障诊断处理 |
| GeneralChatHandler | 通用对话处理 |
| IntentClarifyHandler | 意图澄清处理 |

### 特点

- 单跳流程：识别一次意图 → 执行一次处理 → 返回
- 确定性路径：每种意图对应固定的 Handler
- 意图类型硬编码为枚举（IDLE / DOC_SEARCH / FAULT_DIAGNOSIS / GENERAL_CHAT / INTENT_CLARIFYING）

## 三、Agent Loop 架构

### 核心流程

```
用户请求 → Agent Loop → LLM自主选择tool/skill → 执行 → 结果回灌 → 继续推理 → 收束 → 返回
```

### 核心机制

1. LLM 不只做意图分类，而是在 loop 中持续推理
2. 每轮 loop：模型决策 → 调用 tool → 结果写回上下文 → 再次推理
3. 直到模型判断已收束（问题解决或信息充足），退出 loop

### 规划的 Skills

| Skill | 说明 |
|-------|------|
| DTC 诊断 | 故障码解析、诊断报告获取 |
| 文档搜索 doc_search | 混合检索（词法+向量） |
| 查参数 | 针脚电压、传感器参数等 |
| 读 ECU Data | ECU 数据读取与解析 |
| AskUserQuestion | 向用户提问以获取更多细节 |
| 图像绘制 | 诊断步骤图、电路图等 |
| 其他可扩展 skills | 注册即可用，无需改动核心 loop |

## 四、优劣对比

| 维度 | 当前架构（Orchestrator + Handler） | Agent Loop 架构 |
|------|----------------------------------|-----------------|
| **灵活性** | 低。新业务 = 新 Handler + 修改路由逻辑 | 高。新能力 = 注册一个 tool，LLM 自动发现 |
| **多步推理** | 不支持。一次意图识别 → 一次处理 | 天然支持。loop 内可多次调用不同 tool |
| **组合能力** | 弱。"查故障码+找相关资料"需手动编排 | 强。LLM 自主组合：先查 DTC → 再搜文档 → 再问用户 |
| **可扩展性** | 中等。加 Handler 需改 Orchestrator 路由 | 强。tool 注册即可用，不改核心 loop |
| **可控性** | 强。每条路径确定性执行 | 需要设计 guardrail，LLM 可能走偏 |
| **成本** | 低。意图分类用小模型，最终回复用大模型 | 较高。每轮 loop 都要调模型做推理 |
| **延迟** | 低。单跳 | 较高。多轮 loop，每轮有 LLM 调用 |
| **调试难度** | 低。流程确定，日志清晰 | 较高。LLM 决策路径不确定，需完善 trace |
| **用户交互** | 有限。只有意图澄清一种交互模式 | 丰富。AskUserQuestion 可在任意环节反问 |
| **未来扩展** | 每加一个功能都要改编排逻辑 | 加 skill 即扩展，维护成本低 |

### 典型场景对比

**场景：用户输入"东风天锦 P0087 故障码，帮我找下相关电路图"**

当前架构：
```
IntentRouter 只能识别为一个意图（FAULT_DIAGNOSIS 或 DOC_SEARCH）
→ 只能处理其中一个需求，另一个丢失
```

Agent Loop 架构：
```
Loop 第1轮：LLM 判断需要先查 DTC → 调用 dtc_diagnosis tool → 获取 P0087 诊断信息
Loop 第2轮：LLM 判断还需要电路图 → 调用 doc_search tool（带品牌=东风天锦 + 类型=电路图）
Loop 第3轮：LLM 综合两个结果，生成完整回复 → 收束退出
```

## 五、推荐方案：混合架构

不建议全部推翻重来，推荐渐进式演进——快速路径 + Agent Loop 混合。

```
用户请求
    ↓
[快速路径] 规则引擎命中（明确故障码、明确搜索词）
    → 直接调用对应 tool，单跳返回（低延迟、低成本）
    ↓ (未命中)
[Agent Loop] LLM 进入推理循环
    → 选择 tool → 执行 → 判断是否需要更多信息
    → 可能调用 AskUser → 继续 → 收束返回
```

优势：
- 80% 简单请求走快速路径，保持低延迟低成本
- 20% 复杂请求走 Agent Loop，获得灵活性和组合能力
- 现有 Handler 逻辑直接包装成 tool，不浪费已有代码

## 六、框架选型建议

### 结论：推荐 Pydantic AI 作为 Agent Loop 内核

不建议从零自建 Agent Loop，也不建议直接使用 nanobot 或 Pi 作为内核。

### 排除项

| 方案 | 排除原因 |
|------|---------|
| 从零自建 | tool call 解析、结果回灌、循环控制、多模型适配、上下文治理全部自己写，周期 3-6 个月 |
| nanobot | 偏向多渠道 bot 平台（Slack/Telegram），渠道层复杂度对 Web App 场景是负担 |
| Pi | 偏向 coding agent，monorepo 结构重，会话分支/fork 对维修诊断场景过度设计 |
| LangGraph | 有向图模型更灵活但学习曲线陡，抽象层厚 |
| OpenAI Agents SDK | 偏向 OpenAI 生态，对非 OpenAI 模型的 tool calling 适配不够成熟 |

### 选择 Pydantic AI 的理由

1. **技术栈一致**：现有后端是 FastAPI，Pydantic AI 设计哲学就是"FastAPI 感觉的 Agent 框架"
2. **tool 注册简洁**：`@agent.tool` 装饰器，现有 Handler 可直接包装
3. **模型全覆盖**：原生支持 OpenRouter、Ollama、DeepSeek、Gemini、Claude
4. **MIT 许可**：商业化无障碍
5. **MCP 支持**：未来可将 skill 包装为 MCP Server
6. **生产级成熟度**：V1 版本，100% 测试覆盖

### 迁移映射

```
现有代码                        Pydantic AI
─────────────                  ──────────────
DocSearchHandler    →  包装为   @agent.tool doc_search
FaultDiagHandler    →  包装为   @agent.tool dtc_diagnosis
SearchEngine        →  保留     被 tool 内部调用
DiagnosisClient     →  保留     被 tool 内部调用
Redis SessionMgr    →  保留     作为持久化层
LLMClient           →  替换为   Pydantic AI 模型抽象层
ChatOrchestrator    →  替换为   Pydantic AI Agent Loop
IntentRouter        →  弱化     LLM 在 loop 中自主选择 tool
```

## 七、模型策略

商业化后需要分层使用模型控制成本：

| 用途 | 推荐模型 | 输入/输出价格 ($/1M tokens) | 说明 |
|------|---------|---------------------------|------|
| Agent Loop 主推理 | DeepSeek V3.2 | $0.14 / $0.28 | 工具调用深度集成推理，成本极低 |
| Agent Loop 备选 | Qwen3-30B-A3B | 自托管免费 或 $0.20/$0.80 | Apache 2.0 可自托管，旗舰 90% 能力 |
| 复杂诊断场景 | Claude Haiku 4.5 | $1.00 / $5.00 | 复杂多步推理时升级 |
| 复杂诊断备选 | Gemini 2.5 Flash | $0.30 / $2.50 | 可配置 thinking budget |
| 简单分类/提取 | Gemini 2.0 Flash | $0.10 / $0.40 | 最便宜，简单任务够用 |
| 自托管兜底 | Qwen3-30B-A3B via Ollama | 免费 | 网络不可用时降级 |

核心原则：**按场景动态切换模型**，简单文档搜索用便宜模型，复杂多步诊断用好模型。

## 八、关键挑战与应对

### 1. 上下文管理

**挑战**：多轮 loop 后 token 累积，可能超出上下文窗口。

**应对**：
- 借鉴 nanobot 的热/冷状态分层：当前 session messages（热）+ 摘要归档（冷）
- 现有 Redis 中 session state + context + history 的分层可复用
- 增加 compaction 机制，loop 超过阈值时自动压缩历史

### 2. 会话流程控制

**挑战**：loop 可能死循环或过度消耗 token。

**应对**：
- 设定最大轮次（如 10 轮）
- 设定 token 预算上限
- timeout 机制
- LLM 判断收束 + 强制退出双保险

### 3. AskUserQuestion 交互设计

**挑战**：loop 需要"暂停"等待用户输入，然后恢复。

**应对**：
- 当 LLM 调用 AskUserQuestion tool 时，loop 暂停，返回问题给前端
- 用户回答后，答案作为新输入重新进入 loop（参考 Pi 的 steer 机制）
- Redis 中保存 loop 中间状态，支持恢复

### 4. 成本控制

**挑战**：每轮 loop 都调用 LLM，成本高于单跳。

**应对**：
- 快速路径分流，简单请求不进 loop
- 分层模型策略，常规推理用便宜模型
- token 预算机制，超预算强制收束

## 九、参考架构

本方案参考了以下开源项目的设计思想：

- **nanobot**：上下文治理（MEMORY.md / HISTORY.md 分层）、会话状态流转设计
- **Pi**：Agent Runtime 分层（模型层 → runtime 层 → session 层 → 壳层）、资源系统设计
- **Pydantic AI**：tool 注册机制、多模型抽象、生产级 agent loop 实现
