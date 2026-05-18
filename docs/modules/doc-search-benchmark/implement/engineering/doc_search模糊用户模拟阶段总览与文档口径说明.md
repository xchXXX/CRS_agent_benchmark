# doc_search 模糊用户模拟阶段总览与文档口径说明

## 1. 文档目的

本文档用于把 `doc_search benchmark` 现有文档体系，统一到
[doc_search真实项目模糊用户模拟施工方案](./doc_search真实项目模糊用户模拟施工方案.md)
定义的阶段口径下。

> 实现同步（2026-05-15）：
> 当前 benchmark 第一阶段实际已落到：
> `known_items / uncertain_items` 为主的用户线索合同、
> 结构化 `stop + stop_reason_code + evidence`、
> 以及 report/trace/review 对合法早停的最小支持。
> 因此旧文档里若仍出现“只依赖 known_facts”或“stop 视为无效决策”的描述，
> 一律按上述新口径解释。

本文档只做三件事：

- 明确当前施工的唯一上位真源
- 给旧文档里的历史阶段编号建立映射口径
- 冻结阶段 0 必须遵守的全局边界

## 2. 上位真源

当前这条施工线的唯一上位真源是：

- `docs/modules/doc-search-benchmark/implement/engineering/doc_search真实项目模糊用户模拟施工方案.md`

凡是本模块内其他文档与该方案冲突，统一按该方案为准。

## 3. 新阶段口径

当前施工统一采用以下阶段：

1. `阶段 0`
   - 冻结边界与非目标
2. `阶段 1`
   - 扩展 case schema，但保持主线兼容
3. `阶段 2`
   - 落地非 oracle 的符号决策内核
4. `阶段 3`
   - 在符号内核上叠加 LLM 模糊人格
5. `阶段 4`
   - 引入全量轨迹记录与新报告字段
6. `阶段 5`
   - 逐步扩展场景库
7. `阶段 6`
   - 等待真实协议扩展后再接入新输入类型

当前第一批实际落地范围冻结为：

- `阶段 0`
- `阶段 1`
- `阶段 2`
- `阶段 4` 的最小版

## 4. 旧文档阶段编号的使用方式

仓库里已有一批文档，仍保留历史“阶段 1 到阶段 8”编号。

从现在开始，这些旧编号只用于：

- 说明历史实现顺序
- 指向已有代码落点
- 帮助回溯旧设计讨论

这些旧编号不再作为当前施工排期真源。

如果旧文档里出现以下情况：

- 阶段边界与当前施工方案冲突
- 样本字段设计与 `user_profile / target_doc` 方案冲突
- 用户模拟能力描述与“非 oracle 决策”边界冲突
- 报告字段暴露范围与真值/`selection_payload` 边界冲突

则应按当前施工方案与本文档回改理解。

## 5. 阶段 0 全局冻结边界

### 5.1 真实协议边界

当前真实主线只覆盖：

- `/chat/completions`
- `ask_user.input_type = single_select`
- `allow_free_input = false`
- 恢复依赖 `session_id + ask_user_answer + metadata.selection_payload`

当前不得由 benchmark 私自扩展：

- `text`
- `number`
- `multi_select`
- 虚构 rollback 成功协议
- 固定点击脚本回退方案

### 5.2 真值与用户认知边界

必须严格区分：

- `world_truth`
  - 只给 judge、failure analyzer、report builder 使用
- `user_state`
  - 只给模拟用户决策使用

模拟用户不得直接读取：

- `target_doc.file_id`
- `target_doc.title`
- `target_doc.doc_path`
- 终点核验真值
- 原始 `selection_payload`

### 5.3 路径真值边界

当前样本不得引入：

- `correct_path`
- `accepted_paths`
- `route_truth`

样本冻结的是必要真值与用户认知范围，而不是固定按钮脚本。

### 5.4 `selection_payload` 保留与暴露边界

`selection_payload` 的口径冻结为：

- 运行时适配器允许消费原始 `selection_payload`
- raw artifact 与诊断资产允许保留原始 `selection_payload`
- 面向标准报告层的输出必须脱敏，不能直接暴露原始 `selection_payload`

标准报告层若需要保留选择痕迹，应优先保留：

- `selected_option_key`
- `selected_option_label`
- 脱敏后的摘要、标记或哈希

## 6. 文档回改原则

阶段 0 文档回改遵守以下原则：

- 能直接改成新口径的合同与流程文档，直接改
- 仍需保留历史描述价值的旧文档，显式标注“历史编号仅作参考”
- 不在阶段 0 借文档口径偷偷提前承诺未实现协议

## 7. 后续使用方式

从阶段 1 开始，所有后续冻结与实施都应同时满足：

- 先提出该阶段疑问
- 用户确认冻结
- 再实施该阶段

如果后续实现需要新增字段、调整报告或放宽协议能力，必须先回到本模块文档中补冻结，再改代码。
