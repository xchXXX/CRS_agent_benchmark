# doc_search 多轮 benchmark 适配流程纲要

> 文档口径提示：
> 本文保留历史“阶段 1 到阶段 8”编号，主要用于解释旧多轮 benchmark 适配链路。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](./doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](./doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文档冻结 `CRS_agent` 仓库内 `doc_search benchmark` 的主适配流程纲要。
当前目标不是展开每一步实现细节，而是先统一：

- benchmark 要适配的真实被测链路是什么
- 主流程按什么顺序推进
- 各阶段的边界、输入、产出是什么
- 哪些内容先冻结，哪些留待后续实现

## 2. 当前已确认前提

### 2.1 真实代码是真源

本 benchmark 的所有协议与流程判断，都以当前已实现代码为真源。
不以理想中的产品形态为真源。

### 2.2 当前主链路

本次 benchmark 的主链路不是旧 `/search` 接口，而是 `/chat/completions` 驱动的多轮 `doc_search` 闭环。

已确认的目标流程如下：

1. 用户提出资料需求
2. 被测系统返回：
   - `documents`
   - `message`
   - `ask_user`
3. 若返回 `ask_user`，用户侧提交结构化选项
4. 被测系统继续收敛
5. 最终返回结果

### 2.3 当前撤回能力结论

当前代码真源下：

- 选错后“想撤回”这个业务需求是真实存在的
- 但新版 `ask_user` 主线还没有真实撤回协议

因此 benchmark 当前冻结口径是：

- “故意选错”可以定义为场景
- “想撤回”与“滞后撤回”也可以定义为场景
- 但它们当前只能记为能力缺口，暂不作为可执行主线

这里的“能力缺口”意思是：
需求存在，但真实系统还没支持。

### 2.4 用户模拟原则

已冻结：

- 模拟用户必须 AI 驱动
- 不能靠脚本写死每一步选项
- 不能靠具体字段提前规定第几轮点哪个值

不再保留旧点击脚本字段作为兼容层。

### 2.5 当前 benchmark 边界

- 只负责 benchmark 构建与适配
- 不负责被测 agent 的业务实现
- 不负责页面定位能力本身实现
- 可以先把页码作为目标与评测位保留
- 在页码能力未落地前，页码默认不进入正式 gate

## 3. 非目标

以下内容不在本轮纲要冻结范围内：

- 不展开被测系统内部业务代码改造
- 不伪造当前尚未打通的撤回协议
- 不提前冻结每个 case 的具体 AI 选项轨迹
- 不在本纲要阶段展开页码能力实现

## 4. 主适配原则

### 4.1 主协议原则

- benchmark 主线只围绕 `/chat/completions` 的多轮交互
- `/search` 只保留为诊断与历史兼容路径

### 4.2 AI 用户原则

- 首轮输入可以是自然文本
- `ask_user` 到来后，后续要由 AI 根据当轮真实选项自主决定
- benchmark 不应继续依赖逐轮点击脚本

### 4.3 能力缺口原则

- benchmark 可以表达“用户想撤回”
- benchmark 不得伪造成“系统已支持撤回”
- 当前阶段应把这类场景显式记为能力缺口

### 4.4 结果评测原则

- “正确文件 + 正确页码”仍属于同一个 `doc_search` 功能
- benchmark 可以拆成两个评测维度
- 但不应拆成两个彼此独立的业务链路

## 5. 适配总顺序

本次 benchmark 适配总顺序冻结如下：

1. 先冻结多轮交互协议
2. 再收 benchmark 内部回合模型
3. 再实现会话适配器
4. 再实现 AI 用户模拟
5. 再实现多轮运行器
6. 再收 task 与样本模型
7. 再收 judge 与 report
8. 最后做 smoke 与放量

## 6. 分阶段纲要

### 阶段 1：冻结多轮交互协议

目标：

- 明确 benchmark 要适配的真实交互外形
- 明确当前主链路不支持撤回

本阶段关注：

- 首轮请求如何发起
- `session_id` 如何续接
- `ask_user` 如何返回
- `ask_user_answer` 如何恢复会话
- 什么算中间态
- 什么算终态
- 撤回类 case 在当前阶段如何被记为能力缺口

产出：

- 多轮交互协议文档
- 状态流转说明文档

### 阶段 2：收 benchmark 内部回合模型

目标：

- 让 benchmark 内部模型能表达完整交互轨迹
- 把主模型从“点击脚本”改成“AI 用户场景配置”

本阶段关注：

- 一轮请求响应如何表示
- 如何记录用户行为与 agent 响应
- 如何记录 `ask_user`、`selection_payload`、`session_id`
- 如何记录“故意选错”
- 如何记录“想撤回但协议不支持”

产出：

- benchmark 内部统一回合模型
- 内外协议字段映射关系

### 阶段 3：实现会话适配器

目标：

- 让 benchmark 能正确构造首轮请求与恢复轮请求

本阶段关注：

- 首轮会话启动
- `ask_user_answer` 恢复轮
- 响应归一化
- 明确不实现回退请求

产出：

- 以 `/chat/completions` 为中心的会话适配器

### 阶段 4：实现 AI 用户模拟

目标：

- 让 benchmark 端“用户”真正由 AI 驱动

本阶段关注：

- 首轮如何说话
- `ask_user` 到来后如何基于真实选项自主决策
- 如何表达故意选错
- 如何表达想撤回与滞后撤回
- 在协议不支持时如何把撤回意图落到交互轨迹里

产出：

- 面向多轮 `doc_search` 的 AI 用户模拟器
- `docs/modules/doc-search-benchmark/contract/ai用户结构化决策合同.md`
- `docs/modules/doc-search-benchmark/implementation/ai用户结构化决策流程说明.md`

### 阶段 5：实现多轮运行器

目标：

- 把 AI 用户与被测系统真正跑成一条闭环

本阶段关注：

- 回合驱动
- 状态推进
- 停止条件
- 交互轨迹落盘
- 同一 case 完整重跑 5 次

产出：

- 多轮运行器
- 可复盘交互轨迹

### 阶段 6：收 task 与样本模型

目标：

- 让 case 定义服从真实多轮协议与 AI 用户场景模型

本阶段关注：

- 初始需求
- AI 用户场景配置
- 文件 gold
- 页码 gold
- 最大轮次
- 成功与失败条件

### 阶段 7：收 judge 与 report

目标：

- 在同一功能链路下完成文件与页码评测

本阶段关注：

- 协议级失败
- 文件级命中
- 页码级命中
- 多轮失败分类
- 能力缺口单独统计

### 阶段 8：smoke 与放量

目标：

- 先验证闭环，再扩大样本覆盖

本阶段关注：

- 最小可运行 smoke 集
- 关键多轮文件 case
- 少量页码 shadow case
- 少量误选 case
- 少量撤回意图 case

## 7. 当前建议产物边界

当前建议先完成以下骨架级产物：

- 交互协议文档
- benchmark 内部回合模型
- 会话适配器骨架
- AI 用户场景配置骨架
- 多轮运行器骨架

以下内容明确留给后续：

- AI 用户具体提示词细节
- 撤回意图场景的实际运行判定细节
- 页码追问轮次规则
- page shadow 指标细节
- case 拆分标准

## 8. 与代码路径的对齐参考

当前纲要对应的真实实现参考点如下：

- `modules/crs-agent-upstream/backend/app/agent/runtime/service.py`
- `modules/crs-agent-upstream/backend/app/agent/adapters/doc_search_response_adapter.py`
- `modules/crs-agent-upstream/backend/app/agent/domain/doc_search/service.py`
- `modules/crs-agent-upstream/backend/app/agent/domain/doc_search/pipeline.py`
- `modules/crs-agent-upstream/backend/app/schemas/chat.py`
- `modules/crs-agent-upstream/frontend/user/src/App.tsx`
- `modules/crs-agent-upstream/frontend/user/src/components/ClarifyWizard.tsx`

benchmark 当前入口：

- `benchmark/run.py`
- `benchmark/doc_search_bench/run.py`
- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/adapters.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/judges/`

## 9. 后续讨论方式

后续建议继续严格按阶段展开，每轮只细化一个阶段，避免同时改协议、运行器、task、judge 导致边界混乱。

建议讨论顺序：

1. 先收阶段 1 到阶段 3 的回改一致性
2. 再细化阶段 4 的 AI 用户模拟
3. 再细化阶段 5 的多轮运行器
4. 最后再细化阶段 6 到阶段 8
