# Ask User v2 实施任务清单

> 编写日期：2026-04-01  
> 状态：实施拆解稿  
> 关联文档：[AskUser_v2完整设计方案.md](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/设计方案/AskUser_v2完整设计方案.md)

## 一、目标

这份清单用于把 Ask User v2 从设计稿拆成可执行任务，要求满足：

1. 覆盖当前代码中所有使用 `ask_user` 的位置。
2. 兼容现有 `AskUserQuestion` 外壳，不破坏当前延迟恢复机制。
3. 前端交互从“场景特判组件”升级为“协议驱动渲染”。
4. 保持高扩展性，避免后续继续把逻辑堆进 `App.tsx` 和单个 adapter。
5. 支持优秀的前端体验，包括分支显示、字段级校验、移动端与可访问性。

## 二、实施原则

### 2.1 协议优先，不再继续堆前端特判

禁止继续通过以下方式扩展 ask_user：

- 在 `App.tsx` 增加新的场景 `if/else`
- 在某个组件里硬编码某个字段的特殊交互
- 通过 `allow_free_input` 粗暴表达字段级复杂逻辑

统一原则：

- 协议表达结构
- 前端解释协议
- harness 校验协议

### 2.2 双栈兼容，渐进迁移

迁移顺序必须是：

1. 保留 Ask User v1
2. 引入 Ask User v2 兼容层
3. 优先迁移维修场景
4. 再迁移通用澄清和参数补充

### 2.3 高扩展性目录结构

后端和前端都不要把 v2 继续揉进已有大文件里。

建议新增结构如下：

后端建议新增：

- `backend/app/agent/ask_user_v2/schema.py`
- `backend/app/agent/ask_user_v2/validator.py`
- `backend/app/agent/ask_user_v2/normalizer.py`
- `backend/app/agent/ask_user_v2/conditions.py`
- `backend/app/agent/ask_user_v2/summary.py`
- `backend/app/agent/ask_user_v2/builders/`

前端建议新增：

- `frontend/user/src/modules/ask-user-v2/types.ts`
- `frontend/user/src/modules/ask-user-v2/registry.ts`
- `frontend/user/src/modules/ask-user-v2/conditionEngine.ts`
- `frontend/user/src/modules/ask-user-v2/validation.ts`
- `frontend/user/src/modules/ask-user-v2/summary.ts`
- `frontend/user/src/modules/ask-user-v2/components/AskUserFormV2.tsx`
- `frontend/user/src/modules/ask-user-v2/components/AskUserShell.tsx`
- `frontend/user/src/modules/ask-user-v2/components/fields/`

## 三、当前代码覆盖范围

下面这些位置都属于 Ask User v2 实施范围。

### 3.1 后端协议与运行时

- [ask_user.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/models/ask_user.py)
- [chat.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/schemas/chat.py)
- [factory.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/factory.py)
- [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py)
- [frontend_visibility.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/frontend_visibility.py)
- [manager.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/context/manager.py)
- [task_log_service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/observability/task_log_service.py)
- [admin_logs.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/admin_logs.py)
- [admin_feedback.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/admin_feedback.py)
- [config.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/core/config.py)
- [registry.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/tools/registry.py)

### 3.2 后端业务适配器与场景规则

- [doc_search_response_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/adapters/doc_search_response_adapter.py)
- [response_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/domain/parameter_query/response_adapter.py)
- [repair_knowledge_followup_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/adapters/repair_knowledge_followup_adapter.py)
- [review.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/domain/repair_knowledge/review.py)

### 3.3 用户前端

- [index.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/shared/types/index.ts)
- [App.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/App.tsx)
- [ClarifyWizard.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/components/ClarifyWizard.tsx)
- [RepairFollowupCard.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/components/RepairFollowupCard.tsx)

### 3.4 管理后台

- [logs.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/services/logs.ts)
- [feedback.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/services/feedback.ts)
- [Logs/index.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/pages/Logs/index.tsx)
- [Feedback/index.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/pages/Feedback/index.tsx)

## 四、实施阶段总览

建议按 7 个阶段推进。

| 阶段 | 目标 |
|------|------|
| Phase 0 | 建协议兼容壳，冻结现有行为基线 |
| Phase 1 | 落 Ask User v2 协议、校验器、条件引擎 |
| Phase 2 | 重构前端渲染层，建立统一 AskUser 宿主组件 |
| Phase 3 | 维修场景迁移为 v2 |
| Phase 4 | Doc Search / 参数查询 / 通用澄清迁移为 v2 |
| Phase 5 | 日志、后台、观测链路升级 |
| Phase 6 | 清理旧特判，补测试、灰度与回滚能力 |

## 五、Phase 0：基线与兼容壳

### T001 建立 Ask User v2 协议壳

目标：

- 保留 `AskUserQuestion`
- 新增 `context.schema_version = "2.0"`
- 新增 `context.form`

涉及文件：

- [ask_user.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/models/ask_user.py)
- [chat.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/schemas/chat.py)
- [index.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/shared/types/index.ts)

输出：

- v2 协议类型定义
- 旧协议兼容解析策略

验收标准：

- v1 ask_user 不受影响
- v2 ask_user 能通过序列化/反序列化

### T002 建立模块化目录

目标：

- 后端新增 `ask_user_v2` 模块目录
- 前端新增 `modules/ask-user-v2` 目录

涉及位置：

- 新目录，不改业务逻辑

验收标准：

- 后续 v2 逻辑不再继续塞进 [App.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/App.tsx) 和 [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py)

### T003 建立回归基线

目标：

- 冻结当前 ask_user 相关交互的回归基线

测试覆盖应包括：

1. 通用单选 ask_user
2. 参数补充 ask_user
3. Doc Search 澄清 ask_user
4. 维修追问 ask_user
5. ask_user 恢复继续执行
6. ask_user 日志入库与后台显示

验收标准：

- 新增回归测试文件或补充现有测试集
- 后续每个阶段都能验证是否破坏旧链路

## 六、Phase 1：协议、条件引擎、校验器

### T101 落地 Ask User v2 schema

目标：

- 定义 `Form / Section / Field / Action / Option / Condition / ManualInput`

建议新增文件：

- `backend/app/agent/ask_user_v2/schema.py`
- `frontend/user/src/modules/ask-user-v2/types.ts`

验收标准：

- 前后端类型能一一对应
- 字段支持 `field_type`、`answer_mode`、`visible_if`、`required_if`

### T102 落地后端协议校验器

目标：

- 在 ask_user 出口前统一校验 v2 payload

建议新增文件：

- `backend/app/agent/ask_user_v2/validator.py`

规则至少包括：

1. 非法 field key 拦截
2. `select_only` 字段不允许挂手输配置
3. 条件表达式合法性校验
4. 状态字段与内容字段不能混用
5. 多选字段必须声明提交模式
6. action 与 field 不得同 key

联动文件：

- [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py)
- [factory.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/factory.py)

### T103 落地条件解析与字段显隐计算

目标：

- 后端和前端共享同一套条件语义

建议新增文件：

- `backend/app/agent/ask_user_v2/conditions.py`
- `frontend/user/src/modules/ask-user-v2/conditionEngine.ts`

验收标准：

- 同一份回答数据在前后端得出一致的字段显隐结果

### T104 落地答案摘要器

目标：

- 摘要不再由页面字符串拼接临时生成，而是由协议驱动

建议新增文件：

- `backend/app/agent/ask_user_v2/summary.py`
- `frontend/user/src/modules/ask-user-v2/summary.ts`

验收标准：

- 不同字段类型的摘要一致
- 状态字段和内容字段摘要不会混乱

## 七、Phase 2：前端统一渲染层

### T201 建立 AskUser 宿主组件

目标：

- 前端不再直接在 `App.tsx` 里判断多个 ask_user 组件分支

建议新增：

- `AskUserHost.tsx`
- `AskUserFormV1Renderer.tsx`
- `AskUserFormV2Renderer.tsx`

联动文件：

- [App.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/App.tsx)

验收标准：

- `App.tsx` 只负责分发，不负责具体 ask_user 场景交互逻辑

### T202 建立字段渲染注册表

目标：

- 用字段类型注册表替代场景组件硬编码

建议新增：

- `registry.ts`
- `fields/SingleSelectField.tsx`
- `fields/MultiSelectField.tsx`
- `fields/TextField.tsx`
- `fields/NumberField.tsx`
- `fields/CodeListField.tsx`
- `fields/FileField.tsx`

验收标准：

- 新字段类型扩展时不需要改宿主组件核心逻辑

### T203 建立统一的表单状态机

目标：

- 管理当前字段、回答、显隐、跳过、提交流程

建议新增：

- `useAskUserFormState.ts`
- `validation.ts`

必须支持：

1. progressive 模式
2. single_page 模式
3. 字段级回退
4. 条件隐藏字段自动跳过
5. 字段冲突时自动清理

### T204 优化前端交互设计

这是 Ask User v2 的关键任务，不是 UI 润色项。

必须实现以下体验：

1. 统一外壳  
顶部明确显示：
- 当前步骤
- 缺失原因
- 预计还需几步

2. 条件分支可感知  
用户选中某个状态后，后续字段出现或消失应有明显过渡，不是突兀跳变。

3. 预测候选可解释  
LLM 生成的候选项要显示轻提示，例如：
- `预测候选`
- `根据当前故障现象推测`

4. 手动补充不压迫用户  
只有在字段允许手动输入时才显示，不再全局开放。

5. 多选要有确认动作  
不能再像当前通用澄清一样，点一下就直接提交。

6. 移动端体验  
维修卡片必须在手机上可用，不能靠 hover、不能要求过密点击。

7. 可访问性  
键盘切换、焦点、错误提示、只读状态都要明确。

建议 UI 设计要点：

- 状态字段使用紧凑单选卡片
- 内容字段使用可编辑 chips + 输入框
- 条件跳过字段使用“已自动跳过”浅标签
- 动作用单独 action 区
- 预测选项和确认选项在视觉上分层

### T205 清理旧组件边界

目标：

- [ClarifyWizard.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/components/ClarifyWizard.tsx)
- [RepairFollowupCard.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/components/RepairFollowupCard.tsx)

需要重构为：

1. 保留为 v1 场景兼容层
2. 或逐步下沉为 v2 renderer 的子组件

禁止继续：

- 在这两个组件里叠加更多场景特判

## 八、Phase 3：维修场景迁移

### T301 先迁移 `fault_codes`

目标：

- 将 `fault_codes` 拆为：
  - `fault_code_status`
  - `fault_code_values`
  - 可选 `fault_code_direction`

核心规则：

1. 选 `无故障码` 后跳过报码内容
2. 选 `已读取具体报码` 后显示报码输入位
3. 候选报码由 LLM 或规则生成，但只能放到 `fault_code_values`

涉及文件：

- [repair_knowledge_followup_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/adapters/repair_knowledge_followup_adapter.py)
- [review.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/domain/repair_knowledge/review.py)

### T302 迁移 `data_evidence`

目标：

- 拆为状态和值：
  - `data_evidence_status`
  - `data_evidence_values`
  - `data_evidence_files`

避免问题：

- 用户没有数据流时还被卡成必填
- “数据项候选”和“文件上传”混成一个字段

### T303 迁移 `repair_history`

目标：

- 拆为：
  - `repair_history_status`
  - `repair_history_detail`

### T304 迁移 `ecu_or_system`

目标：

- 至少拆成：
  - `vehicle_info_status`
  - `vehicle_info_detail`

进阶目标：

- 后续支持 `brand / series / engine_model / ecu_model` 精细拆分

### T305 维修追问生成器改造

目标：

- 将维修追问由“平铺字段组生成器”改为“表单 builder”

建议新增：

- `backend/app/agent/ask_user_v2/builders/repair_followup_builder.py`

要求：

1. builder 产出 v2 表单
2. repair adapter 只负责领域判断，不直接拼前端结构

## 九、Phase 4：Doc Search、参数查询、通用澄清迁移

### T401 Doc Search ask_user 迁移

目标：

- 把 [doc_search_response_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/adapters/doc_search_response_adapter.py) 的输出改为 v2 form

要求：

1. 纯筛选题走 `single_select`
2. 有“其他”时显式建一个补充字段，不再用前端 ECU 特判模式
3. 快捷文档确认入口移入 `actions`

### T402 参数查询 ask_user 迁移

目标：

- 将 [response_adapter.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/domain/parameter_query/response_adapter.py) 从“单选 + 自由输入总是开启”改为字段级控制

要求：

1. selection payload 继续保留
2. 参数补充是字段，不再混成“选项 + 任意文本”

### T403 通用 clarify ask_user 迁移

目标：

- 将 [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py#L3464) `_build_ask_user_from_clarify_envelope` 升级为：
  - 支持 v2 form
  - 不再默认 `allow_free_input=True`

这是一个高优先级修复项。  
因为当前大量纯选择题会被误变成“选择 + 填空”混合题。

### T404 ECU 手输特殊逻辑下沉到协议

目标：

- 移除 [ClarifyWizard.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/user/src/components/ClarifyWizard.tsx#L281) 里 `other -> ECU 输入模式` 的前端硬编码特判

改造方向：

- 用一个标准字段表达“候选 ECU + 手动输入 ECU”

## 十、Phase 5：运行时、上下文、日志、后台

### T501 ask_user 出口统一接入 v2 normalizer

目标：

- 无论 ask_user 从哪个场景来，都先过 v2 normalizer / validator

涉及文件：

- [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py)
- [factory.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/factory.py)

### T502 ask_user 恢复态解析升级

目标：

- 后端恢复时解析 v2 answer payload

涉及文件：

- [service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/runtime/service.py)

要求：

1. 能识别字段值
2. 能识别被规则跳过的字段
3. 能区分预测候选与手工输入来源

### T503 Case Context 升级

目标：

- 将 ask_user 的字段级回答沉淀为结构化 artifact，而不是只保留摘要字符串

涉及文件：

- [manager.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/context/manager.py)

要求：

1. `record_user_answer` 存字段级结构
2. `record_pending_action` 存 form id / field ids / action ids

### T504 前端可见性过滤升级

目标：

- `frontend_visibility` 要能针对 v2 form 做字段级 source_refs 隐藏，而不是只会剥顶层 key

涉及文件：

- [frontend_visibility.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/frontend_visibility.py)

### T505 任务日志与后台显示升级

目标：

- ask_user 日志从“只看 question/summary”升级为“表单级 + 字段级”

涉及文件：

- [task_log_service.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/agent/observability/task_log_service.py)
- [admin_logs.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/admin_logs.py)
- [admin_feedback.py](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/backend/app/api/admin_feedback.py)
- [logs.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/services/logs.ts)
- [feedback.ts](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/services/feedback.ts)
- [Logs/index.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/pages/Logs/index.tsx)
- [Feedback/index.tsx](/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent/frontend/admin/src/pages/Feedback/index.tsx)

日志最低要求：

1. 当前 ask_user 版本
2. scene
3. form_id
4. 字段完成数
5. 被跳过字段
6. quick action 是否触发
7. 用户最终输入来源分布

## 十一、Phase 6：清理旧逻辑与测试补齐

### T601 清理 `App.tsx` 中的 ask_user 场景耦合

目标：

- `App.tsx` 不再维护：
  - repair followup 特判
  - clarify wizard 特判
  - 选项即提交的通用 ask_user 特判

### T602 通用 `multi_select` 真正打通

目标：

- 前端支持：
  - 选中
  - 取消
  - 最终确认提交

当前这个问题是硬缺陷，不是优化项。

### T603 回退策略统一

目标：

- 同一轮 ask_user 内部可回退
- 跨轮 ask_user 如果不支持回退，UI 不展示误导性能力

### T604 自动化测试补齐

必须新增测试集：

后端：

1. 协议校验器测试
2. 条件表达式测试
3. 维修 v2 builder 测试
4. ask_user 恢复态解析测试
5. 日志与后台数据结构测试

前端：

1. progressive 表单渲染测试
2. 条件显隐测试
3. 多选确认测试
4. 状态字段驱动跳过内容字段测试
5. 手机端布局快照测试

### T605 灰度与回滚

目标：

- Ask User v2 必须具备开关

建议配置项：

- `ask_user_v2_enabled`
- `ask_user_v2_repair_enabled`
- `ask_user_v2_doc_search_enabled`
- `ask_user_v2_param_query_enabled`

## 十二、优秀前端交互设计的明确验收标准

Ask User v2 前端设计必须满足以下验收项：

1. 用户能一眼看懂当前是在“补充信息”还是“做选择”。
2. 状态字段和内容字段视觉上明显分层。
3. 预测候选有来源标识，不冒充真实信息。
4. 需要手动输入时，输入位出现时机合理，不抢占页面。
5. 必填错误是字段级提示，不是全局一句空话。
6. 多选题不自动提交。
7. 动作按钮和字段答案不会混排混义。
8. 手机上单手可操作，不需要精细点击。
9. 键盘可操作，焦点可追踪。

## 十三、建议的实施顺序

按真实工程依赖，建议顺序如下：

1. T001
2. T002
3. T003
4. T101
5. T102
6. T103
7. T201
8. T202
9. T203
10. T204
11. T301
12. T302
13. T303
14. T304
15. T305
16. T401
17. T402
18. T403
19. T404
20. T501
21. T502
22. T503
23. T504
24. T505
25. T601
26. T602
27. T603
28. T604
29. T605

## 十四、第一阶段建议先做什么

如果只做第一批高收益任务，我建议先做：

1. T001 Ask User v2 协议壳
2. T101 v2 schema
3. T102 validator
4. T201 AskUserHost
5. T202 字段渲染注册表
6. T301 `fault_codes` 正式拆分
7. T403 通用 ask_user 不再默认自由输入

这一批做完，当前最明显的交互问题就会显著下降。

## 十五、一句话总结

Ask User v2 的实施，不是“再做一个新卡片”，而是一次面向协议、运行时、前端渲染、日志后台的全链路重构。

必须把它当成系统能力来做，而不是当成维修追问的局部补丁来做。
