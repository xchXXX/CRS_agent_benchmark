# Ask User v2 完整设计方案

> 编写日期：2026-04-01  
> 状态：设计评审稿  
> 适用范围：通用 ask_user、维修追问、文档澄清、参数补充

## 一、这份文档要解决什么问题

当前项目里已经有 `ask_user_question`，但它本质上还是一个比较扁平的协议：

- 有一个问题 `question`
- 有一组顶层选项 `options`
- 允许或不允许自由输入 `allow_free_input`
- 某些场景通过 `context.field_groups` 自己扩展结构

这套设计能跑通基础流程，但已经暴露出一批稳定的交互问题：

1. 状态问题和内容问题被混在一个字段里。
2. 字段之间没有条件分支，无法表达“先问状态，再决定要不要问内容”。
3. 互斥关系没有被协议表达，前端只能靠“有值就算答了”。
4. 通用 ask_user 与维修 ask_user 实际上是两套半独立逻辑，协议层没有真正统一。
5. `multi_select` 在协议里存在，但通用前端实际上不支持真正的多选确认。
6. 自由输入开关过于粗糙，很多纯选择题也被强行变成“选项 + 填空”的混合题。
7. LLM 可以生成候选项，但 harness 没有足够强的结构约束，导致生成结果容易和 UI 语义冲突。

这份 Ask User v2 设计要做的不是“继续修某一个字段”，而是把 ask_user 升级成一个带条件逻辑、可校验、可扩展、可统一渲染的协议。

## 二、设计目标

Ask User v2 的目标如下：

1. 统一协议。
2. 明确字段语义。
3. 支持条件分支。
4. 支持字段级输入类型和校验。
5. 支持 LLM 生成候选项，但由 harness 做确定性约束。
6. 兼容当前 `AskUserQuestion` 外壳，降低迁移成本。
7. 让前端明确区分“字段答案”“快捷动作”“补充输入”“系统兜底”。
8. 降低用户做填空题的比例。
9. 让日志、恢复、回放、A/B 分析都能落到字段级。

## 三、非目标

Ask User v2 不解决以下问题：

1. 不替代 Agent Loop 的终止治理。
2. 不替代维修领域的知识生成或故障诊断逻辑。
3. 不要求一步到位把所有场景都迁移为复杂动态表单。
4. 不要求前端一次性支持所有富输入控件。

## 四、核心设计原则

### 4.1 先分状态，再问内容

凡是存在“是否有 / 是否已知 / 是否已测量 / 是否已读取”这类语义的问题，都必须拆成两个独立字段：

- 状态字段
- 内容字段

不能把 `无故障码`、`有故障码`、`P0087`、`U0100` 放进同一个字段。

### 4.2 LLM 负责提候选，程序负责定规则

Ask User v2 中：

- LLM 可以生成候选项
- Harness 必须负责字段结构、条件逻辑、互斥规则、必填规则、格式校验

不能把“什么时候显示字段、什么时候允许跳过、什么时候要求补充”完全交给 LLM。

### 4.3 快捷动作与字段答案必须分离

像“先给我通用排查思路”“上传数据流”“我已经补充完信息”这类行为，不是字段答案，而是动作。

Ask User v2 必须把它们定义为 `actions`，不能再混进字段选项。

### 4.4 默认优先选择，不默认优先填空

对大多数场景：

- 优先给用户点选
- 长尾情况再开放手动补充

自由输入不再是全局默认能力，而是字段级能力。

### 4.5 协议必须支持动态显示和跳过

用户选了某个状态后：

- 某些字段应出现
- 某些字段应隐藏
- 某些字段应跳过
- 某些字段应取消必填

这必须由协议表达，而不是靠前端硬编码猜。

## 五、Ask User v2 总体架构

Ask User v2 采用“兼容旧壳，升级内核”的方案。

外层仍然复用当前 `AskUserQuestion`：

```json
{
  "tool_call_id": "ask_xxx",
  "question": "请先补充以下关键信息",
  "input_type": "text",
  "options": [],
  "allow_free_input": false,
  "context": {
    "schema_version": "2.0",
    "card_type": "ask_form_v2",
    "scene": "repair_knowledge_followup",
    "form": {}
  }
}
```

升级点全部放进 `context.form`。

这样做的目的：

1. 后端返回结构不需要整体推翻。
2. 前端可以按 `schema_version` 判断走 v1 还是 v2。
3. 日志、延迟恢复、tool_call_id、上下文恢复逻辑都能直接复用。

## 六、Ask User v2 协议模型

### 6.1 顶层结构

`context.form` 建议结构：

```json
{
  "form_id": "repair_followup_001",
  "version": "2.0",
  "mode": "progressive",
  "title": "维修补充信息",
  "description": "补充后继续诊断",
  "ask_reason": "当前仍缺少故障现象、报码与工况",
  "sections": [],
  "actions": [],
  "ui_policy": {},
  "validation_policy": {}
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `form_id` | 表单实例 ID，用于日志、回放、去重 |
| `version` | 固定为 `2.0` |
| `mode` | `progressive` 或 `single_page` |
| `sections` | 表单内容区域 |
| `actions` | 非字段类动作 |
| `ui_policy` | 前端渲染策略 |
| `validation_policy` | 提交时的全局规则 |

### 6.2 Section 模型

Section 只负责展示分组，不承担复杂语义。

```json
{
  "id": "repair_core",
  "title": "故障基本信息",
  "description": "先补充最关键的诊断条件",
  "fields": []
}
```

### 6.3 Field 模型

Field 是 Ask User v2 的核心单元。

```json
{
  "id": "fault_code_status",
  "key": "fault_code_status",
  "label": "故障码状态",
  "field_type": "single_select",
  "answer_mode": "select_only",
  "required": true,
  "required_level": "hard",
  "visible_if": null,
  "required_if": null,
  "skip_if": null,
  "placeholder": null,
  "hint": "先确认当前是否已读取到具体报码",
  "options": [],
  "manual_input": null,
  "validation": {},
  "summary_policy": {}
}
```

### 6.4 Field 类型

Ask User v2 第一版建议支持以下字段类型：

| `field_type` | 含义 |
|------|------|
| `single_select` | 单选 |
| `multi_select` | 多选 |
| `text` | 文本输入 |
| `number` | 数值输入 |
| `code_list` | 代码列表输入，如故障码 |
| `file` | 文件上传 |

### 6.5 字段回答模式

`answer_mode` 用于控制“选项”和“补充输入”的关系：

| `answer_mode` | 含义 |
|------|------|
| `select_only` | 只能选，不能手填 |
| `text_only` | 只能填，不能选 |
| `select_or_text` | 二选一 |
| `select_and_text` | 可先选，再补充细节 |
| `number_only` | 纯数值输入 |
| `file_only` | 纯文件输入 |

注意：

Ask User v2 不建议再支持“一个字段同时承担状态和内容”这种复合语义。  
像 `fault_code_status -> fault_code_values` 应拆为两个字段，而不是通过某种“status_then_value”魔法模式强行塞进一个字段。

### 6.6 选项模型

```json
{
  "key": "known_codes",
  "label": "已读取到具体故障码",
  "description": "手上已有报码编号",
  "option_source": "system",
  "evidence_level": "confirmed",
  "effects": {
    "show_fields": ["fault_code_values"],
    "require_fields": ["fault_code_values"],
    "clear_fields": [],
    "skip_fields": []
  }
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `option_source` | `system` / `rule` / `llm_predicted` / `user_history` |
| `evidence_level` | `confirmed` / `predicted` / `weak_hint` |
| `effects` | 选中后触发的确定性 UI 和校验变化 |

### 6.7 条件表达式

Ask User v2 需要支持字段级条件逻辑。

建议使用简单 DSL：

```json
{
  "all": [
    { "field": "fault_code_status", "op": "eq", "value": "known_codes" }
  ]
}
```

支持的操作符建议只保留最小集合：

- `eq`
- `neq`
- `in`
- `not_in`
- `contains_any`
- `exists`
- `not_exists`

条件只允许引用当前表单内字段，不允许写任意表达式。

### 6.8 Manual Input 模型

```json
{
  "enabled": true,
  "mode": "append",
  "placeholder": "例如：P0087、U0100",
  "parser": "dtc_list",
  "max_length": 120
}
```

说明：

- `mode=append` 表示手填内容与点选内容并存
- `mode=replace` 表示手填优先覆盖选择项
- `parser=dtc_list` 表示后端恢复时按报码列表解析

## 七、推荐的字段建模规范

### 7.1 状态字段命名规范

凡是“是否有 / 是否已知 / 是否完成 / 是否可提供”的字段，统一命名为：

- `*_status`

例如：

- `fault_code_status`
- `data_evidence_status`
- `repair_history_status`
- `ecu_info_status`

### 7.2 内容字段命名规范

凡是“具体值是什么”的字段，统一命名为：

- `*_values`
- `*_detail`
- `*_files`

例如：

- `fault_code_values`
- `data_evidence_values`
- `repair_history_detail`
- `ecu_info_detail`

### 7.3 预测候选字段命名规范

如果某一类内容可以给出预测候选，但又不能当成真实值，建议独立为：

- `*_candidates`
- `*_direction`

例如：

- `fault_code_direction`
- `suspected_system_candidates`

它们只能作为弱证据，不能与已确认内容混用。

## 八、维修场景下的标准建模方式

### 8.1 故障码相关字段

推荐拆为三个字段：

1. `fault_code_status`
2. `fault_code_values`
3. `fault_code_direction`

含义如下：

| 字段 | 作用 |
|------|------|
| `fault_code_status` | 先确认当前有没有真实报码信息 |
| `fault_code_values` | 只有在已读取到具体报码时才出现 |
| `fault_code_direction` | 只有在未读取具体报码但希望继续收敛时才出现，可选 |

推荐状态选项：

- `known_codes`：已读取到具体故障码
- `unread_but_suspected`：疑似有报码或故障灯，但还没读到具体码
- `no_codes`：当前无故障码
- `unknown`：暂不确定

分支规则：

1. 选 `known_codes`：显示 `fault_code_values`，并设为必填。
2. 选 `unread_but_suspected`：不显示 `fault_code_values`，可选显示 `fault_code_direction`。
3. 选 `no_codes`：跳过所有故障码内容字段。
4. 选 `unknown`：不强制报码内容，但保留后续诊断。

### 8.2 `fault_code_values` 的交互规则

`fault_code_values` 推荐配置：

- `field_type = code_list`
- `answer_mode = select_or_text` 或 `select_and_text`
- `options` 由 LLM 或规则生成 3 到 5 个最可能报码候选
- `manual_input` 始终开启

这里的关键规则是：

1. 候选报码是辅助输入，不是真实报码。
2. 用户点选后，应记录为“用户确认选择了某个候选码”。
3. 用户手填的报码，应记录为“用户直接提供真实报码”。
4. 后续推理必须区分“预测候选被用户选中”和“用户直接输入真实报码”这两种证据强度。

### 8.3 数据流相关字段

同样建议拆成状态和值：

- `data_evidence_status`
- `data_evidence_values`
- `data_evidence_files`

推荐状态选项：

- `available_now`
- `can_upload`
- `not_available`
- `unknown`

这样就不会再出现“系统要求必须提供轨压数据，但用户实际上没有数据流”的冲突。

### 8.4 维修历史相关字段

维修历史不要默认作为长文本必填项。

推荐建模：

- `repair_history_status`
- `repair_history_detail`

状态选项：

- `recent_repairs_exists`
- `no_recent_repairs`
- `unknown`

### 8.5 车型与系统信息

车型、品牌、发动机型号、ECU，不建议继续混成一个巨字段。

建议优先拆为：

- `vehicle_brand`
- `vehicle_series`
- `engine_model`
- `ecu_info_detail`

如果第一阶段不想拆得太细，也至少拆为：

- `vehicle_info_status`
- `vehicle_info_detail`

## 九、通用文档澄清场景的建模方式

Ask User v2 不只给维修追问用，通用澄清也应该统一到同一套协议。

### 9.1 Doc Search 场景

文档澄清推荐使用：

- `mode = progressive`
- 每一轮只显示一个字段
- `field_type = single_select`
- 无自由输入，除非明确存在 “其他”

示例：

1. `brand`
2. `series`
3. `year_range`
4. `engine_model`

### 9.2 ECU 补充场景

ECU 选择建议建成：

- `ecu_candidate`
- `ecu_manual_input`

而不是继续使用前端硬编码的 `other -> 手输 ECU` 特殊分支。

这能把 ECU 补充从“前端特判”改为“协议显式表达”。

## 十、前端交互设计

### 10.1 渲染模式

Ask User v2 建议支持两种渲染模式：

| 模式 | 适用场景 |
|------|------|
| `progressive` | 维修补充、文档澄清、逐步收敛 |
| `single_page` | 信息量较小且字段独立的场景 |

### 10.2 progressive 模式规则

1. 仅渲染当前可见字段。
2. 当前字段回答后，重新计算可见字段列表。
3. 被条件隐藏的字段自动标记为 `skipped_by_rule`。
4. 如果某个字段被选项效果标记为 `skip`，前端不再要求用户回答。
5. 进度条基于当前可见且未完成字段计算，不基于原始字段总数。

### 10.3 single_page 模式规则

1. 所有当前可见字段同时显示。
2. 表单底部统一提交。
3. 必填字段未完成时给出字段级错误。

### 10.4 多选规则

通用 ask_user 前端必须真正支持 `multi_select`：

1. 点击选项只切换状态，不立即提交。
2. 必须有明确的“确认”按钮。
3. 支持最小和最大选择数量校验。

### 10.5 自由输入规则

自由输入不再由顶层 `allow_free_input` 决定，而由字段级 `manual_input.enabled` 决定。

前端规则：

1. `select_only` 字段不渲染输入框。
2. `text_only` 字段不渲染选项按钮。
3. `select_or_text` 字段任意一种方式完成即可。
4. `select_and_text` 字段允许先选后补充。

### 10.6 回退规则

Ask User v2 要区分两类回退：

1. 表单内字段回退  
这个前端必须支持。

2. 已提交 ask_user 后再回到上一轮 ask_user  
第一版可以不支持，但 UI 不能再展示误导性的“可回退上一轮”能力。

### 10.7 快捷动作规则

`actions` 与 `fields` 分离。

例如：

```json
{
  "id": "repair_general_guide",
  "label": "先给我通用排查思路",
  "action_type": "quick_reply",
  "confirm_text": "确认按当前信息直接给出思路"
}
```

前端展示规则：

1. 快捷动作单独展示。
2. 点击快捷动作时，不应污染字段值。
3. 快捷动作提交的 payload 结构必须和字段提交区分开。

## 十一、后端生成与校验流程

Ask User v2 推荐采用两阶段生成。

### 11.1 阶段 A：生成候选表单

来源可以是：

1. 规则生成
2. LLM 生成
3. 规则 + LLM 混合生成

推荐职责：

- 规则负责决定必须有哪些状态字段
- LLM 负责给值字段生成更贴场景的候选项

### 11.2 阶段 B：Harness 校验与修正

Harness 必须做以下事情：

1. 校验字段 key 是否合法。
2. 校验是否存在状态字段和内容字段混用。
3. 校验 `visible_if` / `required_if` 语法。
4. 校验 `multi_select` 是否真的有确认逻辑。
5. 校验 `select_only` 字段不能默认给输入框。
6. 校验必填链路是否闭环。
7. 校验字段数和选项数不要过多。
8. 自动补系统兜底项。

### 11.3 系统兜底项策略

对某些字段类型，系统必须自动补兜底项。

例如：

- 故障码状态字段补 `no_codes`、`unknown`
- 数据状态字段补 `not_available`、`unknown`
- 文件上传字段补 `暂时无法提供`

注意：

系统兜底项应只补在状态字段里，不补在内容字段里。

## 十二、LLM 在 Ask User v2 中的参与边界

LLM 可以参与：

1. 判断当前缺哪些槽位
2. 生成值字段的候选项
3. 生成人类更自然的字段提示语

LLM 不应参与：

1. 定义条件逻辑真值
2. 决定某个字段是否必须出现状态拆分
3. 决定互斥规则
4. 决定最终校验结果

一个推荐原则是：

**LLM 只生成内容，Harness 只决定结构。**

## 十三、用户提交结果的标准格式

推荐提交 payload：

```json
{
  "schema_version": "2.0",
  "scene": "repair_knowledge_followup",
  "action": "submit",
  "form_id": "repair_followup_001",
  "fields": {
    "fault_code_status": {
      "value": "known_codes",
      "display": "已读取到具体故障码",
      "source": "user_selected"
    },
    "fault_code_values": {
      "selected": ["P0615 起动继电器控制电路"],
      "manual_text": "P0335",
      "normalized_values": ["P0615", "P0335"],
      "source": ["predicted_option", "manual_input"]
    }
  },
  "skipped_fields": {
    "fault_code_direction": "hidden_by_condition"
  },
  "summary_text": "已读取到具体报码：P0615、P0335"
}
```

### 13.1 恢复时的后端处理规则

恢复 Agent Loop 时：

1. 只把可见且已回答字段注入主上下文。
2. 被规则跳过的字段不应再作为“缺失信息”触发下一轮 ask_user。
3. `predicted_option` 与 `manual_input` 要保留来源标记，供后续推理区分证据强弱。

## 十四、日志与观测设计

Ask User v2 至少要记录以下事件：

1. `ask_user_rendered`
2. `ask_user_field_answered`
3. `ask_user_validation_failed`
4. `ask_user_submitted`
5. `ask_user_resumed`
6. `ask_user_branch_skipped`
7. `ask_user_quick_action_triggered`

建议日志粒度为“字段级 + 整体表单级”双层：

- 表单级看整体完成率、放弃率、平均字段数
- 字段级看哪个字段最容易卡住、哪个分支最常被跳过

## 十五、与 Ask User v1 的主要差异

| 项目 | v1 | v2 |
|------|------|------|
| 协议中心 | 问题 + 顶层选项 | 表单 + 字段 + 条件 |
| 条件逻辑 | 基本没有 | 协议内显式表达 |
| 输入能力 | 粗粒度 | 字段级输入类型 |
| 自由输入 | 顶层全局开关 | 字段级开关 |
| 状态/内容拆分 | 没有规范 | 强制规范 |
| 快捷动作 | 常和字段混淆 | 单独 `actions` |
| 多选 | 协议有、通用前端弱支持 | 协议和前端都显式支持 |
| 证据来源 | 基本不区分 | 可标记 `predicted` / `confirmed` / `manual` |

## 十六、分阶段迁移方案

### Phase 1：协议兼容层

目标：

1. 保留 `AskUserQuestion` 外壳。
2. 新增 `context.schema_version = 2.0`。
3. 新增 `context.form`。
4. 前端按版本号双栈渲染。

### Phase 2：维修追问优先迁移

先迁移以下高风险字段：

1. `fault_codes`
2. `data_evidence`
3. `repair_history`
4. `ecu_or_system`

其中 `fault_codes` 作为第一优先级试点：

- `fault_code_status`
- `fault_code_values`
- 可选的 `fault_code_direction`

### Phase 3：通用澄清统一迁移

迁移以下通用 ask_user 场景：

1. 文档澄清
2. ECU 手输选择
3. 参数补充

### Phase 4：清理旧协议

当 v2 稳定后：

1. 逐步停止在新场景使用 v1 `options + allow_free_input`
2. 保留向下兼容解析一段时间
3. 最终下线维修场景中的 v1 渲染路径

## 十七、验收标准

Ask User v2 设计落地后，至少应满足以下标准：

1. 不再出现“状态选项”和“具体值选项”混在同一字段的问题。
2. 用户选择“无故障码”后，不会再被要求填写具体报码。
3. 用户选择“已读取到具体故障码”后，一定能看到报码输入位置。
4. 通用 ask_user 的 `multi_select` 能真实工作，而不是点一下就直接提交。
5. 快捷动作不会再污染字段值。
6. 前端回退能力与真实能力一致，不再出现“UI 显示能回退，实际不能”的误导。
7. 后端恢复时能区分预测候选、用户确认、手动输入三类证据。

## 十八、推荐的第一批落地顺序

如果按性价比排序，建议这样做：

1. 先实现 v2 协议兼容壳。
2. 先迁移维修场景的 `fault_codes`。
3. 再迁移 `data_evidence`。
4. 再补通用 ask_user 的真正多选支持。
5. 最后统一 ECU/DocSearch 等历史特判场景。

## 十九、一句话总结

Ask User v2 的核心不是“把卡片做得更复杂”，而是：

**把 ask_user 从“一个问题 + 一堆选项”的扁平交互，升级成“有字段语义、有条件逻辑、有确定性校验”的受控表单协议。**

对当前项目来说，这一步是必要的。  
如果不做，后面你会在 `fault_codes`、`data_evidence`、`ECU`、`维修历史`、`文档澄清` 上反复遇到同一类交互问题。
