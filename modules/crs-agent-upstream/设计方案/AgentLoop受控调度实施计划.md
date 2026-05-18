# AgentLoop 受控调度实施计划

> 编写日期：2026-03-27
> 状态：实施计划
> 关联文档：`设计方案/AgentLoop受控调度与终止治理改造方案.md`

## 一、计划目标

这份文档不再讨论“为什么要改”，而是把前一份方案收敛成一个可以执行的 implementation plan。

本计划的目标只有一个：

**把当前偏开放式的 AgentLoop，逐步改造成有预算、有停止条件、有收敛策略的受控调度系统。**

本次计划强调四件事：

1. 先控制失控，再优化智能
2. 先改运行时治理，再改业务 prompt
3. 先让 guard 会“收敛”，再让 agent 更“聪明”
4. 不一次性重写整个 loop，而是分阶段替换高风险部分

## 二、当前基线

当前代码已经具备以下基础：

- `RequestIntentRouter` 已存在，具备业务前置分流
- `doc_search` 已经是外部确定性 workflow
- `repair_knowledge` 已有 `gate + renderer` 雏形
- `LoopGuard` 已支持总次数、同工具次数、同参重复次数限制
- `CaseContext` 已能存储槽位、证据摘要和 pending action
- ask-user 的暂停、持久化和恢复已经打通

当前最主要的问题有三类：

### 2.1 主 agent 仍然过于开放

除 `doc_search` 和部分 `repair_knowledge` 外，主 agent 仍然会自主决定：

- 是否继续调用工具
- 调用几次
- 什么时候停止

### 2.2 guard 只有“阻止”，没有“收敛”

当前 guard 命中后，主要结果还是报错，不够适合线上体验。

### 2.3 context 还是“证据容器”，还不是“调度状态”

目前 `CaseContext` 更像共享上下文，而不是能驱动停止审查的 working state。

## 三、实施原则

### 3.1 先做最小闭环

先做最能控制失控问题的一组改造，不追求一步到位上复杂 graph。

### 3.2 优先改高收益模块

优先顺序如下：

1. LoopGuard
2. 收敛策略
3. CaseContext 扩展
4. repair_knowledge guard review
5. fault_diagnosis 显式状态机
6. param_query 强约束 skill 化
7. 通用主 agent 工具面收缩

### 3.3 每个阶段都必须可单独上线

每一阶段都必须满足：

- 不依赖后续阶段才能工作
- 能单独验证收益
- 出问题时可快速回退

### 3.4 不在第一阶段引入大规模架构重写

第一轮不引入完整图式执行框架，不推翻现有 `AgentLoopService`，而是在当前 runtime 上增量增强。

## 四、实施范围

本次计划涉及四类模块。

### 4.1 Runtime 层

重点文件：

- `backend/app/agent/runtime/service.py`
- `backend/app/agent/runtime/factory.py`
- `backend/app/agent/runtime/deps.py`

职责：

- 请求级预算注入
- guard 命中后的收敛逻辑
- 高风险链路前置审查

### 4.2 Context 层

重点文件：

- `backend/app/agent/context/models.py`
- `backend/app/agent/context/manager.py`
- `backend/app/agent/context/prompt_builder.py`
- `backend/app/agent/context/guard.py`

可能新增：

- `backend/app/agent/context/action_review.py`
- `backend/app/agent/context/answer_ready.py`

职责：

- 扩展 working state
- 保存动作摘要和预算状态
- 支撑 answer-ready 与 no-gain 判定

### 4.3 Domain 层

优先业务：

- `repair_knowledge`
- `fault_diagnosis`
- `parameter_query`

职责：

- 定义关键槽位
- 定义 stop condition
- 定义 ask-user gating

### 4.4 Tests 层

重点文件：

- `backend/tests/test_loop_guard.py`
- `backend/tests/test_case_context_manager.py`
- `backend/tests/test_case_context_runtime_integration.py`
- `backend/tests/test_repair_knowledge_service.py`
- `backend/tests/test_agent_loop_service.py`

可能新增：

- `backend/tests/test_action_review.py`
- `backend/tests/test_answer_ready.py`
- `backend/tests/test_fault_diagnosis_runtime.py`

## 五、阶段拆分

## Phase 1：增强 LoopGuard，先控制明显失控

### 目标

先把当前“重复调工具、重复外部调用、明显转圈”的问题压住。

### 改造内容

#### 1. 扩展 guard 维度

在现有 `LoopGuard` 基础上新增：

- 最大 ask-user 次数
- 最大外部工具调用次数
- 连续无增量次数阈值
- 更强的参数标准化签名

#### 2. 工具分级

在 runtime 中给工具补一层元数据，不需要先全面重构工具注册，但至少能在 guard 中区分：

- 本地工具
- 外部工具
- 用户交互工具

#### 3. 增加 guard 快照

让 runtime 在 tracer 中记录：

- 剩余预算
- 当前 no-gain streak
- 当前是否命中重复调用

### 涉及文件

- `backend/app/agent/context/guard.py`
- `backend/app/agent/runtime/factory.py`
- `backend/app/agent/runtime/service.py`
- `backend/app/core/config.py`

### 验收标准

- 同参重复调用在更早阶段被拦截
- 外部工具预算能单独生效
- ask-user 次数可限制
- tracer 中能看到 guard 预算变化
- 原有测试不回归

### 本阶段不做

- 不改变主 agent 的运行方式
- 不引入 answer-ready
- 不把 guard 命中改成收敛输出

## Phase 2：把“报错型 guard”改成“收敛型 guard”

### 目标

让系统在 guard 命中后优先收敛，而不是直接报错。

### 改造内容

#### 1. 引入统一收敛结果

建议在 runtime 内部引入三种收敛出口：

- `best_effort_answer`
- `ask_user_required`
- `insufficient_information`

#### 2. 改造 guard 命中后的处理逻辑

从“抛错 -> error response”改为：

1. 先看当前是否已具备最低回答条件
2. 如果具备，直接输出最佳努力答案
3. 如果缺关键槽位且 ask-user 预算未耗尽，转 ask-user
4. 如果两者都不满足，再返回明确的不足说明

#### 3. 保留错误出口，但下沉为最后兜底

真正的 runtime error 和模型异常仍然返回 error，但 guard 预算命中不再默认走 error。

### 涉及文件

- `backend/app/agent/runtime/service.py`
- `backend/app/agent/context/guard.py`
- `backend/app/agent/context/models.py`

可能新增：

- `backend/app/agent/runtime/convergence.py`

### 验收标准

- guard 超限场景不再直接返回 error
- 同参重复时可返回更明确的用户态结果
- 现有 ask-user 恢复流程不受影响

### 本阶段不做

- 不引入完整动作审查
- 不大改 domain service

## Phase 3：把 CaseContext 扩展成 working state

### 目标

让调度层拥有真正可用于审查的状态，而不只是摘要上下文。

### 改造内容

#### 1. 扩展 `CaseContext` 结构

建议新增以下字段：

- `missing_slots`
- `attempted_actions`
- `candidate_answer`
- `no_gain_streak`
- `answer_ready`
- `remaining_budget`

#### 2. 动作摘要入库

每次工具调用完成后，不只记录结果 artifact，还要记录：

- 动作名
- 参数签名
- 结果摘要
- info gain 评级
- 是否补齐了槽位

#### 3. prompt 改为更多喂状态、少喂原始历史

`CaseContextPromptBuilder` 从“证据摘要器”逐步变成“working state 视图构造器”。

### 涉及文件

- `backend/app/agent/context/models.py`
- `backend/app/agent/context/manager.py`
- `backend/app/agent/context/prompt_builder.py`

### 验收标准

- context 中能看到缺失槽位和已尝试动作
- no-gain streak 能跨动作更新
- prompt 不会因为新增状态而明显膨胀失控

### 本阶段不做

- 不让主模型直接产出完整 decision schema
- 不强制所有业务都走同一状态图

## Phase 4：引入 Action Review，先接 repair_knowledge

### 目标

先在最适合、收益最大的链路上落地“候选动作 -> 外部审查 -> 执行”的模式。

### 为什么优先 repair_knowledge

原因有三个：

1. 当前已经有 `gate + renderer` 基础
2. 最容易出现“还能继续搜标题/继续读正文”的行为
3. ask-user batch once 的规则在这个领域最明确

### 改造内容

#### 1. 为 repair_knowledge 增加显式审查层

建议增加以下判定：

- 是否缺关键字段
- 是否应立即 ask-user
- 是否已经有足够证据回答
- 是否允许继续调用维修知识工具

#### 2. 把 gate 从 prompt 约束升级为外部规则

当前 gate 仍然较依赖 prompt，应逐步引入更明确的外部判定：

- 有无 loaded context
- 有无 source refs
- 缺失字段数
- 连续 no-gain 次数

#### 3. 在 repair_knowledge 链路引入 `answer_ready`

满足以下条件时直接结束：

- 关键字段已齐
- 已加载至少一条相关正文
- 不存在未解决冲突
- 再继续调用工具收益低

### 涉及文件

- `backend/app/agent/runtime/service.py`
- `backend/app/agent/runtime/factory.py`
- `backend/app/agent/domain/repair_knowledge/service.py`
- `backend/app/agent/adapters/repair_knowledge_followup_adapter.py`

可能新增：

- `backend/app/agent/domain/repair_knowledge/review.py`

### 验收标准

- 维修知识链路明显减少无意义继续检索
- 缺信息场景能更早切到 ask-user
- 可答场景不再继续多轮 tool 调用

## Phase 5：fault_diagnosis 显式状态化

### 目标

让故障诊断从“工具链条”变成更明确的业务状态机。

### 改造内容

建议把故障诊断拆成以下阶段：

- 故障码识别
- ECU 候选判定
- ask-user 选择 ECU
- 执行诊断
- 诊断结果整合

关键规则：

- 缺 ECU 时禁止继续诊断
- `lookup_ecu_candidates` 后若多候选，不允许继续盲猜
- `dtc_diagnosis` 失败后要有明确 fallback，不允许无限重试

### 涉及文件

- `backend/app/agent/domain/fault_diagnosis/service.py`
- `backend/app/agent/runtime/service.py`
- `backend/app/agent/runtime/factory.py`

可能新增：

- `backend/app/agent/domain/fault_diagnosis/review.py`

### 验收标准

- ECU 不明确时只走 ask-user
- `dtc_diagnosis` 重试次数受控
- 失败场景能明确收敛

## Phase 6：param_query 强约束 skill 化

### 目标

把参数查询进一步从“自由工具”收紧为“强约束技能”。

### 改造内容

#### 1. 明确参数查询关键槽位

至少包括：

- pin / signal / component target
- ECU 或 source
- 选中的参数资料源

#### 2. 参数问题优先走显式技能逻辑

对于明确参数问题，建议逐步从通用主 agent 中收紧为：

- 优先判定是否可直接查
- 不可直接查则 ask-user
- 已命中结构化结果则直接 answer-ready

#### 3. 限制参数查询在同轮中的探索空间

禁止一轮内反复变体试探多个近似 query。

### 涉及文件

- `backend/app/agent/domain/parameter_query/service.py`
- `backend/app/agent/runtime/service.py`
- `backend/app/agent/runtime/factory.py`
- `backend/app/agent/context/manager.py`

### 验收标准

- 参数查询命中结果时可更快收敛
- source 不明确时 ask-user 更稳定
- 同轮试探性查询减少

## Phase 7：收缩通用主 agent 的工具面

### 目标

最终把“一个主 agent 直连所有底层工具”的模式，逐步收敛为“主 agent 只看到高层技能”。

### 改造内容

建议长期目标如下：

- 主 agent 只选择技能
- 技能内部受 guard review 约束
- 底层工具不再全部直接暴露给主 agent

这一步不是短期必须完成，但它是后续多能力协同仍能保持可控的关键。

## 六、推荐执行顺序

按性价比和风险控制，建议严格按这个顺序推进：

1. `Phase 1`：增强 LoopGuard
2. `Phase 2`：guard 收敛化
3. `Phase 3`：扩展 CaseContext
4. `Phase 4`：repair_knowledge action review
5. `Phase 5`：fault_diagnosis 状态化
6. `Phase 6`：param_query 强约束 skill 化
7. `Phase 7`：主 agent 工具面收缩

原因很简单：

- 前三阶段主要解决运行时层面的共性问题
- 后四阶段再逐步把业务能力收紧

如果跳过前面直接改业务链路，后面还会重复返工。

## 七、每阶段交付物

为防止计划落空，每个阶段都应明确交付物。

### Phase 1 交付物

- 扩展版 `LoopGuard`
- 配置项补充
- 对应单测

### Phase 2 交付物

- runtime 收敛出口
- guard 命中后不再直接报错
- 对应集成测试

### Phase 3 交付物

- working state 字段扩展
- prompt builder 改造
- context manager 改造

### Phase 4 交付物

- repair_knowledge review 规则
- answer-ready 初版
- ask-user gating 集成测试

### Phase 5 交付物

- fault_diagnosis 状态拆分
- ECU gating 逻辑
- 失败收敛策略

### Phase 6 交付物

- param_query 技能化规则
- 参数问题更稳定的 ask-user/收敛逻辑

### Phase 7 交付物

- 主 agent 工具面收缩方案
- 高层 skill 注册方式

## 八、测试策略

本次改造必须同步补测试，不能先改逻辑后补。

### 8.1 单元测试

重点覆盖：

- guard 预算命中
- no-gain 判定
- answer-ready 判定
- missing slot gating

### 8.2 集成测试

重点覆盖：

- 同参重复时的收敛结果
- ask-user 次数超限后的行为
- repair_knowledge gate 的提前收敛
- fault_diagnosis 的 ECU 选择路径

### 8.3 回归测试

至少确保以下路径不回归：

- doc_search 闭环恢复
- 现有 ask-user deferred 恢复
- 流式输出
- 参数查询 dedicated response

## 九、风险控制

### 9.1 最大风险

最大风险不是“功能做不出来”，而是：

- runtime 越改越复杂
- guard 规则过多导致行为不可预测
- 业务链路局部优化后彼此不一致

### 9.2 控制策略

应坚持：

- 先共性运行时，后业务细化
- 每阶段都补测试
- 每阶段都保留最小可回退边界

### 9.3 暂不做的事项

当前不建议在这轮计划里做以下事情：

- 全量迁移到图式执行框架
- 大规模重写 AgentFactory
- 一次性把所有 tool 抽成 skill wrapper
- 重新设计完整前端协议
- 引入复杂 LLM 评分器作为第一版本的核心依赖

## 十、明确结论

这次改造不应该被理解为“继续给 agent 加规则”，而应该被理解为：

**把当前系统从 prompt 驱动的工具循环，升级为 runtime 驱动的受控调度循环。**

短期最重要的不是做出一个更复杂的 agent，而是先做出一个：

- 会停
- 会收敛
- 会 ask-user
- 会在预算内结束

的 agent。

如果严格按这个计划推进，当前项目会从“已经能跑的 AgentLoop”，进入“可控、可上线、可继续扩展”的下一阶段。
