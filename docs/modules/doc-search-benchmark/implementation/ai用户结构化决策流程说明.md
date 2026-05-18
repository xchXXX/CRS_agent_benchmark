# AI 用户结构化决策流程说明

> 文档口径提示：
> 本文按
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 的 `阶段 3` 口径说明决策流程。
>
> 实现同步（2026-05-15）：
> 当前流程已切到“纯原始上下文 + LLM 结构化决策”口径：
> `known_items / uncertain_items` 是主输入，不再向 LLM 注入程序辅助信号或候选命中评分。

## 1. 文档目的

本文说明 `阶段 3` 的 AI 用户结构化决策流程。
当前流程是在 `阶段 2` 的非 oracle 符号骨架上，叠加受控的 LLM 模糊人格决策。

## 2. 基本流程

当前结构化决策流程冻结如下：

1. 读取用户认知与当前交互轨迹
2. 读取当前轮真实 `ask_user.question`
3. 读取当前轮真实 `ask_user.context`
4. 读取当前轮真实选项的 `key/label/description`
5. 把原始上下文直接交给 LLM 做结构化决策
6. 对 LLM 输出做合法性校验与必要重试
7. 输出结构化决策

## 3. 首轮口径

当前真实主线下，首轮仍由样本提供自然文本：

- `TaskCase.initial_user_message`

因此本阶段的结构化决策主流程，重点不在生成首轮文本，而在消费 `ask_user` 选项轮。

## 4. 选项轮流程

### 输入

- 当前交互轨迹
- 当前 `ask_user.question`
- 当前真实选项列表
- `user_profile`
- `user_simulation_config`

### 处理

当前阶段建议至少做：

- 优先消费 `ask_user.context` 中前端显式暴露的信息
- 优先消费 `known_items / uncertain_items / aliases`
- 允许合理推断，但选择具体项前必须先做一次“支撑线索自检”
- `ECU / 发动机 / 电路图 / 控制器 / 板子 / 针脚` 这类泛词，不能单独当作某个具体品牌、型号、厂商、针数选项的充分支撑
- 按自然语言规则区分“选具体项 / 选其他 / 合法早停”
- 对输出做结构校验与真实选项存在性校验

### 输出

- `decision_kind = choose_option | stop`
- `selected_option_key`
- `selected_option_label`
- `stop_reason_code`
- `evidence`
- `reason`

## 5. 可见性约束

当前阶段必须遵守：

- 决策时只看得到 `ask_user.context + key/label/description`
- 看不到 `target_doc` 真值
- 看不到原始 `selection_payload`
- 不允许通过运行时 payload 反查答案
- 若当前场景是 `image_parsing_required`，则用户视角不能从图片中获得新信息
- 若当前场景不是 `image_parsing_required`，则当前上下文里已经明确给出的、图片中清晰可读且稳定的线索可以作为用户已知信息

## 6. 阶段 3 的人格决策层

### 输入

- `user_profile.persona`
- `user_profile.correction_style`
- 当前真实选项空间
- 当前对话与问题上下文

### 处理

- 不同 persona 只影响表达和保守程度
- 不再使用程序候选排序、误选预算、近邻放宽
- LLM 可以做合理推断，但必须把推断落回到可区分候选项的具体线索；否则应走“其他/不确定”或 stop
- 最终仍由 LLM 在真实选项中直接做结构化决策

### 输出

- `decision_kind = choose_option`
- `selected_option_key`
- `selected_option_label`
- `reason`

## 7. 撤回意图流程

### 输入

- 当前交互轨迹
- 当前场景配置

### 输出

- `decision_kind = declare_rollback_intent`
- `rollback_target_round`
- `reason`

### 当前阶段处理

当前阶段一旦产出该决策，运行器应：

1. 记录撤回意图
2. 标记 `rollback_supported = false`
3. 写入能力缺口说明
4. 结束当前 attempt

## 8. stop 流程

### 输入

- 当前交互轨迹
- 用户主观完成感知

### 输出

- `decision_kind = stop`
- `stop_reason_code`
- `evidence`

说明：

- `stop` 依据是用户认知上的“当前无法真实继续作答”
- 不是依据真值命中器
- 当前最小 stop 枚举：
  - `OPTION_SPACE_CONFLICT`
  - `INSUFFICIENT_INFORMATION`

## 9. 与后续阶段的边界

当前阶段已覆盖：

- `normal / cooperative_vague / term_confused`
- `immediate / delayed` 的纠错风格注入
- `verify / reflect` 约束下的自然波动

仍未覆盖：

- `阶段 4` 的全量 trace 字段和失败分类报告
- `阶段 5` 的大规模人格场景库
- `阶段 6` 之后的 `text / number / multi_select`

## 10. 代码映射

- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`

## 11. 阶段 3 完成标准

满足以下条件即可视为 `阶段 3` 决策流程完成：

1. 能基于真实选项做结构化选择
2. 不读取 `target_doc`
3. 不读取原始 `selection_payload`
4. 不向 LLM 注入程序辅助信号、命中评分或候选空间收缩结果
5. runner 只做结构与真实选项校验
6. 人格、纠错风格已进入稳定执行逻辑
7. 决策理由可用于复盘
