# AI用户已知信息与早停决策改造方案

## 1. 文档目的

本文冻结 `doc_search benchmark` 当前确认的用户模拟改造方案。

本次方案只做 benchmark 内部改造，不修改前后端业务代码。

目标是把当前 `ask_user` 选项轮的用户模拟简化为一条清晰主链：

- 删除 `known_facts / uncertain_facts / unknown_facts`
- 删除 `wrong_selection_budget / 误选`
- 删除程序侧按维度评分、候选空间收缩、误选预算控制
- 改为 `LLM 结构化全决策 + runner 严格校验 + 可观测日志`

## 2. 现状问题

当前旧实现存在三类问题：

1. 用户已知信息依赖人工维度映射  
   `known_facts` 需要先把线索映射到 `brand/model/doc_type/component`，这一步本身不稳定。

2. 逻辑混合过重  
   程序先做维度推断和符号打分，LLM 再做最后选择，导致“到底是谁决定的”很难解释。

3. stop 与“其他”边界混乱  
   一部分场景应该选“其他”，另一部分场景应该停止当前 case，旧逻辑经常互相吞掉。

## 3. 改造目标

本次改造只保留以下设计目标：

1. 用户模拟只依据用户当前明确知道的信息做决策
2. 选项选择完全由 LLM 输出结构化 JSON 决定
3. runner 只做合法性校验，不再替用户评分
4. prompt 只保留原始上下文与明确规则，不再注入程序辅助信号
5. stop 只保留两类：
   - `OPTION_SPACE_CONFLICT`
   - `INSUFFICIENT_INFORMATION`
6. 存在合法“其他/不确定”兜底项时，优先选兜底项，不要滥用 stop
7. 运行日志必须可复盘本轮为什么选、为什么停

## 4. Case 结构

`user_profile` 最终只保留：

```json
{
  "user_profile": {
    "persona": "normal | cooperative_vague | term_confused | image_parsing_required",
    "goal": "用户要完成的目标",
    "known_items": ["用户明确知道的词或短语"],
    "uncertain_items": ["用户可能知道但不完全确定的词或短语"],
    "aliases": {
      "电脑板": ["ECU", "控制板"]
    },
    "correction_style": "immediate | delayed",
    "notes": "可选备注"
  }
}
```

删除字段：

- `known_facts`
- `uncertain_facts`
- `unknown_facts`
- `wrong_selection_budget`

## 5. 决策主链

每当后端返回 `ask_user`，runner 执行以下流程：

1. 收集上下文  
   包括：
   - 当前 `ask_user.question`
   - 当前选项 `key/label/description`
   - 当前历史 transcript
   - 当前 `known_items / uncertain_items`
   - `persona / correction_style`
   - `initial_user_message`
   - `ask_user.context`

2. 调用模拟用户 LLM  
   直接基于原始上下文和明确规则做选择，要求其只输出一个结构化 JSON。

3. 校验 JSON  
   runner 只校验：
   - `decision_kind` 是否合法
   - `choose_option` 是否选择了真实存在的选项
   - `stop` 是否填写合法 `stop_reason_code`
   - `declare_rollback_intent` 是否给出合法轮次

   说明：
   - runner 不额外做“直接支持”硬拦截
   - “是否足够支持某个具体项”由提示词要求 LLM 自检，不由程序侧改写为硬规则

4. 执行动作  
   - `choose_option`：提交选择，继续当前 case
   - `stop`：结束当前 case，继续下一个 case
   - `declare_rollback_intent`：若 benchmark 不支持，则记录能力缺口并结束当前 case

5. 记录日志  
   必须记录：
   - 当前问题
   - 当前选项
   - 发给 LLM 的 prompt
   - LLM 原始输出
   - 解析后的结构化决策
   - 校验结果
   - 最终执行动作

## 6. 输出合同

模拟用户必须只输出：

```json
{
  "decision_kind": "choose_option | stop | declare_rollback_intent",
  "selected_option_key": "可为空",
  "selected_option_label": "可为空",
  "rollback_target_round": 1,
  "stop_reason_code": "OPTION_SPACE_CONFLICT | INSUFFICIENT_INFORMATION",
  "reason": "简短中文理由",
  "evidence": {
    "supports": ["支持本次决策的已知线索"],
    "conflicts": ["冲突点或无法判断原因"]
  }
}
```

约束如下：

- `choose_option` 时必须选真实存在的选项
- `stop` 时必须填写合法 `stop_reason_code`
- `reason`、`supports`、`conflicts` 尽量用中文
- `stop_reason_code` 保持英文枚举

## 7. stop 定义

本次方案只保留两种 stop：

### 7.1 `INSUFFICIENT_INFORMATION`

触发条件：

- 用户确实不知道当前问题要求确认的信息
- 且当前没有可表达“不确定/其他”的兜底选项

### 7.2 `OPTION_SPACE_CONFLICT`

触发条件：

- 当前选项空间与用户已知信息明显不相容
- 且“其他/不确定”也不能准确表达

## 8. “其他/不确定”边界

兜底项不做严格相等匹配，而是做“兜底语义识别”。

识别来源：

- `option.label`
- `option.description`

识别关键词包括但不限于：

- `其他`
- `其它`
- `不确定`
- `不知道`
- `不清楚`
- `无法确认`
- `以上都不是`
- `无合适`

规则：

- 如果当前问题方向合理，但现有具体枚举项都不准确，而兜底项可以真实表达当前状态，应优先选兜底项
- 只有当兜底项也不能表达时，才允许 stop

## 9. 模拟用户选择原则

模拟用户每轮只按以下原则选择：

1. 只能依据用户当前明确知道的信息做选择
2. 不能补充 case 中没有提供的事实
3. 如果某个具体选项最符合当前认知，就选它
4. 允许合理推断，但推断前必须自检：支撑点要能回指到当前对话、`known_items / uncertain_items / aliases / ask_user.context` 或当前上下文里已明确给出的信息
5. `ECU / 发动机 / 电路图 / 控制器 / 板子 / 针脚` 这类泛词，不能单独作为某个具体品牌、型号、厂商、针数选项的充分支撑
6. 如果具体项都不准确，但“其他/不确定”能真实表达，就选兜底项
7. 如果用户确实不知道且没有兜底项，才 stop
8. 如果当前选项空间与已知信息明显冲突且无法用兜底项表达，才 stop
9. 不要为了跑完 case 瞎选

## 10. 提示词口径

模拟用户提示词必须显式强调：

- 只按用户视角做选择
- 不能脑补
- 不能编造不存在的选项
- 允许合理推断，但必须先做一次“支撑线索自检”
- 泛词或行业大类词不能单独支撑具体项
- 只能使用原始上下文，不要依赖程序评分、命中统计或候选收缩结果
- `image_parsing_required` 只限制用户视角不能从图片中补线索；其他带图 case 若上下文已明确给出图片可读信息，则可纳入用户已知信息
- “其他/不确定”优先于滥用 stop
- stop 只允许两类
- 只输出 JSON

## 11. 验收标准

满足以下条件即可视为本次方案完成：

1. `known_facts / uncertain_facts / unknown_facts / wrong_selection_budget` 从主链删除
2. 用户模拟主链切为 `LLM 结构化全决策`
3. prompt 中不再出现程序辅助信号、候选命中评分
4. runner 只做结构校验和执行
5. stop 仅保留两类
6. 存在兜底项时不再被 stop 提前吞掉
7. 测试和文档已同步更新
