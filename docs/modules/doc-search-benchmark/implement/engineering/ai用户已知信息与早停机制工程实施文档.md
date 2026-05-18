# AI用户已知信息与早停机制工程实施文档

> 对应方案：
> [AI用户已知信息与早停决策改造方案](../../implementation/ai用户已知信息与早停决策改造方案.md)

## 1. 文档目的

本文说明如何把最终确认方案落到当前仓库实现中。

本次实施只覆盖 benchmark 侧：

- case 结构
- 用户模拟决策逻辑
- runner 校验与停止处理
- 日志 / trace / review
- 测试

不涉及前后端业务代码。

## 2. 变更范围

本次实际改动范围：

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/judges/trace.py`
- `benchmark/doc_search_bench/chat_export/render_first_attempt_review_html.py`
- `benchmark/doc_search_bench/chat_export/render_case_review_html.py`
- `benchmark/doc_search_bench/envs/doc_search/data/*`
- `benchmark/tests/*`
- `docs/modules/doc-search-benchmark/*`

## 3. 实施目标

工程上需要完成 6 件事：

1. 删除旧字段：
   - `known_facts`
   - `uncertain_facts`
   - `unknown_facts`
   - `wrong_selection_budget`
2. 删除旧逻辑：
   - 维度推断
   - 程序打分收缩候选空间
   - 误选预算与近邻误选控制
3. 切换为 `LLM 结构化全决策`
4. runner 只保留结构化校验和 stop 消费
5. stop 收敛为两类：
   - `OPTION_SPACE_CONFLICT`
   - `INSUFFICIENT_INFORMATION`
6. 同步更新测试、文档和 fixture

## 4. 代码改造说明

### 4.1 `types.py`

需要做的事：

- 从 `UserProfile` 删除：
  - `known_facts`
  - `uncertain_facts`
  - `unknown_facts`
- 从 `UserSimulationConfig` 删除：
  - `wrong_selection_budget`
- 删除从 `known_facts` 自动展平的兼容逻辑
- 让 `resolve_known_items / resolve_uncertain_items` 只读取新字段
- 从 `TaskMetadataRecord` 删除 `wrong_selection_budget`

### 4.2 `user.py`

需要做的事：

- 删除维度推断与程序候选裁剪主链
- 删除误选预算控制
- 删除 `QUESTION_OFF_TRACK`
- 保留并收敛 `stop_reason_code`
- 保留结构化 JSON 解析和校验
- 使用统一提示词驱动 LLM 全决策
- 保留兜底项识别能力
- 证据结构由 runner 记录和 review 消费

### 4.3 `env.py`

需要做的事：

- 删除误选预算相关说明
- `AskUserDecisionContext` 不再注入误选预算和已用预算
- runner 继续：
  - 调用结构化决策
  - 校验结构
  - 消费 `choose_option / stop / declare_rollback_intent`
- `stop` 时结束当前 case，继续后续 case

### 4.4 `trace.py`

需要做的事：

- stop 合法码只保留两类
- 删除围绕 `wrong_selection_used / candidate_count / allow_non_top_choice` 的分析逻辑
- 保留 stop 证据与失败原因归因

### 4.5 review 渲染

需要做的事：

- review 页只展示：
  - `known_items`
  - `uncertain_items`
- 删除：
  - `known_facts`
  - `uncertain_facts`
  - `unknown_facts`

## 5. fixture 迁移规则

所有 fixture 统一迁移为：

- `user_profile` 仅保留：
  - `persona`
  - `goal`
  - `known_items`
  - `uncertain_items`
  - `aliases`
  - `correction_style`
  - `notes`
- `user_simulation_config` 删除 `wrong_selection_budget`

## 6. 测试策略

至少覆盖以下回归点：

1. `stop` 必须带合法 `stop_reason_code`
2. 存在合法兜底项时，应允许选“其他”
3. 无兜底项且用户确实无法回答时，应 stop
4. runner 能正常消费 `stop`
5. trace / review 能展示 stop 详情

## 7. 自捡要求

改造完成后必须检查：

1. `user.py`、`env.py`、`trace.py`、`render*.py` 可通过语法编译
2. 相关 pytest 全通过
3. 真实 fallback 边界 case 不再被 stop 误杀
4. 文档口径与代码口径一致

## 8. 当前落地结果

当前实现完成后，用户模拟主链将变为：

1. runner 收到 `ask_user`
2. 组装用户可见上下文
3. 调用 LLM 输出结构化 JSON
4. runner 校验 JSON
5. 执行选择或停止
6. 写日志并继续 benchmark
