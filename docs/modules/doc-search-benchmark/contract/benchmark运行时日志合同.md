# benchmark 运行时日志合同

> 实现同步（2026-05-15）：
> 当前日志已新增用户模拟早停事件与 stop 证据摘要；
> 运行时诊断不再只围绕 `choose_option / rollback` 两类分支。

## 1. 文档目的

本文冻结 `doc_search benchmark` 的运行时日志合同，回答三件事：

1. 全链路哪些环节必须进入主日志
2. 主日志的固定格式是什么
3. `摘要` 如何由程序在运行时自动生成

## 2. 主日志定位

本次新增的运行时日志是 benchmark 侧的主观测入口，不替代以下资产：

- `benchmark/reports/runs/<run_id>/report.actual.json`
- `benchmark/reports/runs/<run_id>/report.score.json`
- `benchmark/reports/runs/<run_id>/raw/*.json`

冻结结论：

- `actual/score` 继续承担正式结果与评分职责
- `raw/*.json` 继续承担原始证据职责
- `runtime.log` 承担“人和 AI 直接阅读的主时间线”职责

## 3. 日志文件落点

单次运行的主观测目录固定为：

- `benchmark/reports/runs/<run_id>/`

其中主日志文件固定为：

- `benchmark/reports/runs/<run_id>/runtime.log`

冻结结论：

- 单次运行的所有观测与报告产物统一写入 `benchmark/reports/runs/<run_id>/`
- `runtime.log` 在运行时实时追加写入 `benchmark/reports/runs/<run_id>/runtime.log`
- `runs/<run_id>/` 目录本身就是历史产物，不再执行二次归档复制
- 标准报告顶层应补充：
  - `runtime_log_path`

## 4. 固定格式

主日志每条主行固定为 6 段：

```text
时间 | 级别 | 事件 | 定位 | 结果 | 摘要
```

说明：

- `时间`
  - 本地时间，精确到毫秒
- `级别`
  - 只允许 `信息`、`警告`、`错误`
- `事件`
  - 必须使用中文事件码
- `定位`
  - 当前事件所处的 run/suite/case/attempt/turn 上下文
- `结果`
  - 当前事件最关键的结构化结果
- `摘要`
  - 由程序在运行时自动生成的一句中文摘要

可选附加行固定为：

```text
  详情: ...
  路径: ...
```

冻结规则：

- 主行必须严格保持 6 段
- 额外信息不得继续向主行加列
- 长文本、原始路径、问题文案、候选项摘要放入 `详情:` 或 `路径:`

## 5. 摘要生成规则

`摘要` 采用程序模板生成，不允许人工逐条填写，也不依赖 LLM 现场总结。

冻结公式：

- `摘要 = 中文事件模板 + 当前事件的结构化字段`

示例：

- `发送请求`
  - `initial_message` -> `开始发送首轮对话请求`
  - `ask_user_resume` -> `开始发送澄清恢复请求`
  - `search_api` -> `开始发送检索请求`
- `收到响应`
  - `ask_user` -> `收到澄清问题，等待用户模拟决策`
  - `documents` -> `收到文档结果，当前轮进入终态`
  - `message` -> `收到普通消息，当前轮进入终态`
  - `error` -> `服务返回错误响应`
- `完成用户模拟决策`
  - `choose_option` -> `用户模拟选择了候选项`
  - `declare_rollback_intent` -> `用户模拟表达了撤回意图`
  - `stop` -> `用户模拟完成本轮决策`
- `用户模拟触发早停`
  - `OPTION_SPACE_CONFLICT` -> `用户模拟因候选空间冲突停止当前 attempt`
  - `INSUFFICIENT_INFORMATION` -> `用户模拟因信息不足停止当前 attempt`
  - `QUESTION_OFF_TRACK` -> `用户模拟因问题跑偏停止当前 attempt`
- `尝试停止`
  - `missing_session_id` -> `因缺少 session_id 停止`
  - `max_turns_exceeded` -> `超过最大轮次仍未收口`
  - `rollback_unsupported` -> `因撤回能力缺口停止`
  - `user_simulation_stop` -> `因用户模拟合法早停停止`

## 6. 必须覆盖的全链路事件

首批实现必须覆盖以下中文事件码：

- `运行开始`
- `套件开始`
- `用例开始`
- `尝试开始`
- `请求预处理`
- `预处理完成`
- `预处理阻断`
- `发送请求`
- `收到响应`
- `识别澄清问题`
- `开始用户模拟决策`
- `用户模拟模型调用`
- `用户模拟输出非法`
- `用户模拟校验失败`
- `用户模拟符号决策`
- `完成用户模拟决策`
- `用户模拟触发早停`
- `用户模拟决策失败`
- `用户选择已提交`
- `发现能力缺口`
- `尝试停止`
- `合同判定完成`
- `文件判定完成`
- `页码判定完成`
- `轨迹分析完成`
- `尝试完成`
- `报告写入完成`
- `运行完成`
- `运行异常`

## 7. 用户模拟 agent 观测范围

主日志必须显式记录用户模拟 agent 的行为，而不只记录最终选择结果。

至少保留：

- 当前 `ask_user` 轮次
- `ask_user.question`
- 可选项数量
- 可选项标签摘要
- `user_strategy`
- `user_model`
- 决策类型
- 决策原因
- `stop_reason_code`
- `supports / conflicts` 摘要
- 选中项 `key/label`
- 是否触发故意误选
- 是否声明撤回意图
- 撤回目标轮次
- 决策耗时
- 决策失败异常
- 内部重试中的非法 JSON 与校验失败

补充冻结：

- `user_model` 必须反映本次 attempt 实际使用的用户模拟模型
- 若未显式传 `--user-model`，日志中的默认模型应与运行器默认解析结果一致
- 当前内建默认值固定为 `openrouter:deepseek/deepseek-chat-v3-0324`，不得再隐式回落到 `gpt-4o`

## 8. 字段边界与脱敏

主日志允许保留：

- 原始响应文件路径
- 选项标签摘要
- 脱敏后的用户决策理由
- 顶层失败码与能力缺口文案

主日志不得直接暴露：

- 原始 `selection_payload`
- `target_doc` 真值全量对象
- 过长的完整 prompt 或完整 transcript

冻结规则：

- `selection_payload` 只保留在内部原始资产
- 主日志中的选项与决策只保留“可读摘要”

## 9. 与报告的关系

标准报告新增运行时日志索引字段后，应满足：

1. 能从 `actual/score` 顶层直接跳到主日志
2. 能从主日志继续跳到 `raw/*.json`
3. 主日志不替代标准报告的正式评分口径

## 10. 完成标准

满足以下条件即可视为本合同落地：

1. benchmark 运行中可以实时看到 `benchmark/reports/runs/<run_id>/runtime.log`
2. 主日志能够串起请求、响应、用户模拟、judge、收口
3. `摘要` 全部由程序模板自动生成
4. 中文事件码稳定可读
5. `actual/score` 顶层能给出日志路径
