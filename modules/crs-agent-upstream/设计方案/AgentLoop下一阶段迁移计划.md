# Agent Loop 下一阶段迁移计划

## 一、计划目标

当前新项目已经完成两件关键事：

1. `doc_search` 的旧业务能力已通过 `legacy` 目录迁入新项目，并接入了 Agent Loop。
2. `ask_user_question -> selection_payload -> 二次检索` 与 `hard_constraint_validator / existence_validator` 两个闭环已经打通。

下一阶段的目标不是继续“搬文件”，而是开始把旧项目中仍然混在 handler 里的业务规则，按正确的分层和设计模式，迁移到新架构里。

本阶段必须坚持一个核心原则：

**迁移的是业务能力，不是旧编排方式。**


## 二、当前基线

新项目当前已经具备：

- Pydantic AI Agent Loop 运行时
- Redis 消息历史与 deferred state 持久化
- MySQL 接入
- `legacy_doc_search_adapter`
- `search_documents`
- `analyze_doc_search_ambiguity`
- `ask_user_question`
- `selection_payload` 结构化回环
- `hard_constraint_validator`
- `existence_validator`

因此，后续迁移的重点不再是基础设施，而是以下三类能力：

1. `doc_search_handler` 中剩余的领域规则
2. `fault_diagnosis` 相关业务能力
3. history processor 和 Mem0 的分层接入


## 三、设计原则

### 1. 运行时与业务域分离

运行时负责：

- Agent 创建与执行
- tool 注册
- deferred ask_user 持久化与恢复
- 历史消息读写
- 流式事件转前端协议

业务域负责：

- 搜索
- 诊断
- 规则校验
- 歧义分析
- 结构化结果生成

不要把任何业务规则继续塞回 runtime。

### 2. 交互出口统一

所有需要用户补充信息的场景，统一由 `ask_user_question` 发起。

业务模块只能：

- 发现缺信息
- 生成澄清候选
- 返回结构化建议

业务模块不能：

- 自己维护 pending state
- 自己中断流程
- 自己直接拼前端澄清响应

### 3. 旧模块只做“受控复用”

旧项目可复用的部分：

- 搜索引擎
- 预处理
- 同义词、拼音、实体抽取
- 澄清分析
- 校验器
- 诊断规则

旧项目不能直接继承过来的部分：

- handler 状态机
- orchestrator 编排
- 面向前端的直接响应组装

### 4. 模式服务于边界，不服务于炫技

这次迁移只使用能真正降低耦合的模式，不做为了“看起来高级”而引入的空抽象。


## 四、设计模式使用约束

## 1. Adapter Pattern：用于“接旧能力”

适用位置：

- `legacy_doc_search_adapter`
- 后续 `legacy_fault_diag_adapter`

职责：

- 把旧业务服务包装成 Agent tool 可消费的输入输出
- 做少量协议转换
- 不承载复杂业务决策

要求：

- Adapter 只负责翻译，不负责发明新规则
- Adapter 不能演化成新的“大杂烩 Handler”

## 2. Facade Pattern：用于“聚合旧服务”

适用位置：

- `doc_search` 领域服务内部
- `fault_diagnosis` 领域服务内部

职责：

- 统一编排同一业务域内的多个 legacy service
- 对上层暴露稳定入口

要求：

- Facade 只聚合同一业务域能力
- 不跨域编排其他 tool

## 3. Strategy Pattern：用于“可替换规则”

适用位置：

- 澄清策略：规则澄清 / LLM 澄清
- 校验策略：硬约束 / 存在性 / 外部源补充
- 历史压缩策略：摘要化 / 截断 / 工具结果瘦身

职责：

- 让规则切换不影响主流程
- 方便灰度、A/B 和后续扩展

要求：

- Strategy 只替换算法，不改变调用时序
- 不要把简单 `if/else` 机械抽成 Strategy

## 4. Pipeline Pattern：用于“固定处理链”

适用位置：

- `doc_search` 请求处理链
- `fault_diagnosis` 请求处理链

典型链路：

- query normalize
- entity extraction
- search / diagnose
- validation
- ambiguity analysis
- structured result build

要求：

- 每一步输入输出清晰
- 处理链是领域链，不是 Agent Loop

## 5. Factory Pattern：用于“运行时对象创建”

适用位置：

- `AgentFactory`
- `AgentRuntimeDeps.build_default()`

职责：

- 创建 Agent
- 注入依赖
- 屏蔽模型、存储、legacy service 的装配细节

要求：

- Factory 负责创建，不承载业务流程

## 6. State Pattern：本阶段不引入

原因：

- 旧项目里最大的问题之一就是业务模块持有交互状态机
- 新架构已经把交互状态外提到 runtime + Redis deferred store

结论：

- 本阶段不在 `doc_search` 或 `fault_diagnosis` 内重新设计状态机
- 避免把旧的 `pending_clarify_*` 思路换个名字搬回来

## 7. Repository Pattern：谨慎使用

本阶段不建议为了“纯架构”把现有 SQLAlchemy 访问再套一层统一 Repository。

原因：

- 当前主要是旧业务迁移，不是重建数据访问层
- 强推 Repository 会增加大量搬运工作和抽象噪音

结论：

- 先保持 legacy service 直接使用 SQLAlchemy session
- 只有在新增写路径、聚合写事务、跨数据源切换时，再单独引入 Repository


## 五、下一阶段迁移范围

## Phase 1：补齐 `doc_search_handler` 中剩余的领域规则

### 目标

把旧 `doc_search_handler` 里仍然有价值、但尚未迁入的规则迁到新项目，尤其是：

- 实体冲突检测
- 自动实体过滤
- 父级维度回填
- 多来源结果统一裁剪规则
- 必要的 LLM 澄清补充

### 正确落点

放入新的领域层，而不是继续堆在 adapter 里。

建议新增：

- `backend/app/agent/domain/doc_search/`
- `backend/app/agent/domain/doc_search/service.py`
- `backend/app/agent/domain/doc_search/pipeline.py`
- `backend/app/agent/domain/doc_search/policies/`

### 模式落地

- `LegacyDocSearchAdapter`：继续做 Adapter
- `DocSearchService`：做 Facade
- `policies/`：承载 Strategy / Policy
- `pipeline.py`：承载固定处理链

### 产出

对上层提供一个稳定入口：

- `execute(query, filters, selection_payload) -> DocSearchResult`

Adapter 不再直接拼装所有业务细节，而是调用领域服务。


## Phase 2：收敛 `doc_search` 输出模型

### 目标

把当前 tool 输出中逐步稳定下来的字段，固化为领域模型，而不是继续传松散 dict。

建议新增模型：

- `DocSearchRequest`
- `DocSearchResult`
- `DocSearchValidity`
- `DocSearchClarifyCandidate`
- `DocSearchFilters`

### 原则

- 对 Agent tool 仍可输出 JSON
- 但内部流转先使用强类型模型

### 收益

- 减少适配层重复拼字典
- 减少字段漂移
- 为后续前端协议和 history processor 提供稳定数据结构


## Phase 3：迁移 `fault_diagnosis`

### 目标

在 doc_search 迁移方式跑顺后，用同样方法迁移故障诊断域。

### 迁移方式

1. 先做 `legacy_fault_diag_adapter`
2. 只接核心诊断能力，不接旧编排状态
3. 把需要用户补充的 ECU / 工况 / 车型澄清统一转成 `ask_user_question`

### 设计模式

- Adapter：接旧诊断能力
- Facade：封装诊断域入口
- Pipeline：故障码识别 -> 诊断 -> 补充搜索 -> 生成建议
- Strategy：多诊断来源或多诊断深度切换


## Phase 4：接入 history processors

### 目标

解决 Agent Loop 上下文膨胀问题，但不污染业务模块。

### 建议位置

- `backend/app/agent/memory/history_processors.py`

### 处理策略

- 对大结果集做瘦身
- 对 tool return 保留必要结构字段
- 保证 tool call / tool return 成对保留
- 对 `ask_user_question` 的问答对严格保留

### 模式

这里适合使用 Strategy，而不是把所有压缩逻辑写成一个巨型函数。


## Phase 5：接入 Mem0

### 目标

把长期记忆引入 runtime，但不让 Mem0 取代 Redis。

### 职责边界

Redis 负责：

- message history
- deferred state
- 热会话数据

Mem0 负责：

- 用户偏好
- 长期车型偏好
- 常见作业上下文

### 接入顺序

1. 先只做读取增强，不做自动写入
2. 验证记忆命中质量
3. 再增加 selective write-back

### 设计要求

- Mem0 只能作为 runtime 的记忆增强依赖
- 不要让业务 service 直接依赖 Mem0


## Phase 6：前端协议与样式对齐

### 目标

保持现有前端交互风格不变，只替换数据来源。

### 范围

- `ask_user` 事件结构稳定化
- 复用现有澄清卡片视觉风格
- 前端只认统一的 `ask_user` 协议

### 设计原则

- 前端不感知 legacy / agent 的内部差异
- 前端不解析业务工具私有字段


## 六、推荐实施顺序

建议按下面顺序推进：

1. `doc_search` 领域层收敛
2. `doc_search_handler` 剩余规则迁移
3. `doc_search` 输出模型强类型化
4. `fault_diagnosis` adapter 接入
5. history processors
6. Mem0 只读接入
7. 前端协议稳定化

原因很简单：

- `doc_search` 是当前最成熟、风险最低的业务域
- 它已经完成基础闭环，适合先把模式打磨对
- `fault_diagnosis` 复杂度更高，应复用 `doc_search` 的迁移方法论


## 七、每阶段验收标准

## Phase 1 验收

- `legacy_doc_search_adapter` 变薄
- 核心规则进入 `domain/doc_search`
- adapter 中不再堆复杂业务判断
- 原有检索结果不回退

## Phase 2 验收

- 内部不再依赖松散 dict 传递核心对象
- tool 输出字段稳定
- 测试覆盖结构化字段

## Phase 3 验收

- `fault_diagnosis` 可作为独立 tool 接入 Agent Loop
- 澄清统一走 `ask_user_question`
- 旧 handler 不再承担交互态

## Phase 4 验收

- 长对话 token 明显下降
- ask_user 恢复链路不被压缩破坏

## Phase 5 验收

- Mem0 命中不影响主流程
- Redis / Mem0 职责不混


## 八、本阶段明确禁止的错误做法

1. 把旧 `doc_search_handler` 整体复制进 adapter。
2. 在 `doc_search` 域内重新引入 `pending_clarify_*` 状态。
3. 为了“面向对象”把所有简单函数都硬拆成类。
4. 为了“DDD”先重写数据库访问层。
5. 在 runtime 里硬编码业务规则。
6. 让前端直接消费某个 legacy service 的私有结构。


## 九、建议的下一步执行项

下一步建议直接进入：

**Phase 1：`doc_search` 领域层收敛。**

具体第一批文件建议是：

- `backend/app/agent/domain/doc_search/__init__.py`
- `backend/app/agent/domain/doc_search/models.py`
- `backend/app/agent/domain/doc_search/service.py`
- `backend/app/agent/domain/doc_search/pipeline.py`

第一批只做一件事：

**把 `LegacyDocSearchAdapter` 中已经变重的业务逻辑，往领域层下沉。**

这样后续再迁 `doc_search_handler` 的剩余规则时，结构不会继续变乱。
