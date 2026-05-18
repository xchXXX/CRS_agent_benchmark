# AI 用户结构化决策合同

## 1. 文档目的

本文冻结当前 benchmark 用户模拟在 `ask_user` 选项轮上的结构化决策合同。

当前合同只服务于 benchmark 内部用户模拟，不对前后端业务协议做要求。

## 2. 输入边界

模拟用户当前只允许消费以下输入：

1. `instruction`
2. 当前交互轨迹
3. 当前 `ask_user.question`
4. 当前 `ask_user.context` 中前端显式暴露的辅助信息
5. 当前选项的：
   - `key`
   - `label`
   - `description`
6. `user_profile`
7. `user_simulation_config`

其中 `user_profile` 只保留：

- `persona`
- `goal`
- `known_items`
- `uncertain_items`
- `aliases`
- `correction_style`
- `notes`

当前明确禁止模拟用户直接读取：

- `target_doc.file_id`
- `target_doc.title`
- `target_doc.doc_path`
- `target_doc.facets`
- 原始 `selection_payload`
- 任意 gold 真值
- 任意程序预先计算的命中分、排序分、候选收缩结果

## 3. 输出合同

模拟用户必须只输出一个 JSON 对象：

```json
{
  "decision_kind": "choose_option | stop | declare_rollback_intent",
  "selected_option_key": "真实选项 key，可为空",
  "selected_option_label": "真实选项 label，可为空",
  "rollback_target_round": 1,
  "stop_reason_code": "OPTION_SPACE_CONFLICT | INSUFFICIENT_INFORMATION",
  "evidence": {
    "supports": ["命中的已知线索"],
    "conflicts": ["冲突点"]
  },
  "reason": "一句短中文理由"
}
```

## 4. 字段说明

- `decision_kind`
  - 当前决策类型
- `selected_option_key`
  - 选中的真实选项 key
- `selected_option_label`
  - 选中的真实选项 label
- `rollback_target_round`
  - 想撤回到第几轮
- `stop_reason_code`
  - 合法 stop 原因码，仅 `stop` 时填写
- `evidence`
  - 供日志、trace、review 使用的结构化证据
- `reason`
  - 简短中文理由

## 5. 决策类型

### 5.1 `choose_option`

要求：

- 必须从当前真实选项中选择
- 不得编造不存在的 `key` 或 `label`

### 5.2 `stop`

要求：

- 必须提供合法 `stop_reason_code`
- 必须提供最小 `evidence`

当前仅允许：

- `OPTION_SPACE_CONFLICT`
- `INSUFFICIENT_INFORMATION`

### 5.3 `declare_rollback_intent`

要求：

- 必须给出 `rollback_target_round`
- 只能表达撤回意图，不能伪造撤回已经成功

## 6. 决策原则

模拟用户必须遵守：

- 只能依据用户当前明确知道的信息做选择
- 不能补充 case 中没有提供的事实
- 只能根据原始输入上下文做判断，不依赖程序预计算的辅助信号
- 如果某个具体选项最符合当前认知，就选择该选项
- 如果具体项都不准确，但 `其他/不确定/不清楚/无法确认/以上都不是` 这类兜底项可以真实表达当前状态，应优先选择该兜底项
- 如果用户确实不知道当前问题要求确认的信息，而且没有任何兜底项，才允许 stop
- 如果当前选项空间与已知信息明显冲突，且兜底项也不能准确表达，才允许 stop
- 不要为了让流程继续而强行选择明显不符合用户认知的选项

## 7. runner 校验要求

runner 必须校验：

1. `decision_kind` 合法
2. `choose_option` 选择了真实存在的选项
3. `stop` 提供了合法 `stop_reason_code`
4. `declare_rollback_intent` 提供了合法 `rollback_target_round`

若不合法，允许重试模型输出；仍不合法时结束当前 case。
