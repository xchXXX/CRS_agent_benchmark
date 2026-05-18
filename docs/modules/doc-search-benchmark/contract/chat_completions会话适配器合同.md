# `/chat/completions` 会话适配器合同

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有会话适配器合同。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文档冻结阶段 3 的会话适配器合同。
这里的“会话适配器”指 benchmark 端负责拼装 HTTP 请求的那一层。

## 2. 阶段 3 的目标

阶段 3 只做一件事：

- 让 benchmark 能正确构造首轮请求和 `ask_user` 恢复轮请求

阶段 3 不负责：

- 决定选哪个选项
- 执行撤回
- 跑完整多轮循环
- 同一个 case 连续重跑 5 次

## 3. 适用边界

阶段 3 会话适配器只负责：

- `/chat/completions` 多轮主线
- `/search` 旧诊断路径

当前明确不负责：

- 新增任何“回退请求”类型
- 假装系统已经支持撤回

## 4. 最小职责

会话适配器必须提供以下能力：

### 4.1 构造首轮请求

输入：

- 题目文本
- 图片上下文
- 运行模式

输出：

- 指向 `/chat/completions` 的 HTTP 请求对象

### 4.2 构造恢复轮请求

输入：

- `session_id`
- `tool_call_id`
- 用户选中的选项文本
- 该选项对应的 `selection_payload`

输出：

- 指向 `/chat/completions` 的 HTTP 请求对象

### 4.3 保留 `/search` 请求构造能力

输入：

- 查询文本
- top k

输出：

- 指向 `/search` 的 HTTP 请求对象

## 5. 首轮请求合同

```json
{
  "message": "<question_text>",
  "context": {},
  "mode": "auto"
}
```

## 6. 恢复轮请求合同

```json
{
  "session_id": "<session_id>",
  "ask_user_answer": {
    "tool_call_id": "<tool_call_id>",
    "answer": "<option_label_or_key>",
    "metadata": {
      "selection_payload": {}
    }
  }
}
```

说明：

- `answer` 可以是用户看到的选项文字，也可以是内部 `key`
- 当前主线必须显式带上 `metadata.selection_payload`

## 7. 当前明确不支持的请求

阶段 3 当前明确不定义：

- `rollback_request`
- `withdraw_request`
- `back_to_round_request`

如果未来真实系统打通了这类请求，应视为新的阶段冻结，而不是在当前合同里偷偷扩展。

## 8. 结果记录责任

阶段 3 冻结以下责任：

- 适配器负责构造请求
- 运行环境负责发请求并拿到响应
- 运行环境负责把请求响应写入内部回合记录

## 8.1 文档结果归一化补充合同

当 `/chat/completions` 最终返回 `documents` 结果时，benchmark 侧需要把真实响应归一化为内部 `PredictedDocument` 结构。

当前冻结口径如下：

- `doc_title` 优先取 `filename`，再回退到 `title / name / file_name / file_id`
- `doc_path` 优先取 `hierarchy_full / path / physical_path / file_path / doc_path`
- 如果真实外部结果缺少上述路径字段，允许回退到稳定文档标识：
  - `file_id`
  - `id`
  - 若仍缺失，再回退到 `filename / title`

补充约束：

- 这里的 `doc_path` 是 benchmark 内部合同字段，不要求一定是本机物理路径
- 当真实外部 `ggzj_*` 响应不提供本地层级路径时，使用稳定文档标识填充 `doc_path` 视为合法适配
- 此规则的目标是避免真实外部结果因缺少本地路径字段而被误判为 `SCHEMA_INVALID`

## 9. 与后续阶段的边界

阶段 3 完成后，应已经具备：

- “如何发首轮”
- “如何发恢复轮”

但还不具备：

- “何时继续下一轮”
- “何时停止”
- “AI 用户何时故意误选”
- “AI 用户何时产生撤回意图”

这些由后续阶段消费。
