# doc_search真实项目模糊用户模拟施工方案

> 实现同步（2026-05-15）：
> 当前仓库的 benchmark 落地已进入“内容线索优先 + 合法早停”的执行口径：
> `known_items / uncertain_items` 是用户侧主输入，
> `known_facts / uncertain_facts` 保留兼容；
> `stop` 已不是无效分支，而是可进入 runner、trace、review、report 的合法结果。
> 本文后续仍保留大量历史设计展开，若局部段落与当前代码不一致，以本说明和模块内最新合同文档为准。

## 1. 文档目的

本文档用于冻结一条面向真实 `CRS_agent` 项目的施工路线：在不泄露真值、不伪造固定点击脚本的前提下，把当前 `doc_search benchmark` 的 AI 用户，从“能消费真实 `ask_user` 选项的结构化决策器”，推进为“符合普通用户逻辑、支持渐近披露、可表达局部模糊与纠错”的真实项目用户模拟 agent。

本文档关注的是：

- 如何把方案落到当前仓库已经存在的 benchmark 主线。
- 如何把“真实存在的目标文档”与“用户可见认知”分层。
- 如何让用户 agent 基于实时返回的选项自主选择，而不是依赖写死轨迹或 oracle 规则。
- 如何分阶段落地，避免同时改协议、样本、运行器、judge 导致边界失控。

本文档不做以下事情：

- 不修改被测业务主链路本身。
- 不要求文档预览 URL 或外部路径在运行时一定可达。
- 不把 benchmark 改成浏览器 DOM 点击自动化。
- 不把 case 设计成“预先写死第几轮点第几个按钮”。
- 不要求 case 预定义“唯一正确路径”。

## 2. 当前基线

### 2.1 当前 benchmark 已经具备的能力

当前仓库里的 benchmark 主线已经不是自由文本对话脚本，而是围绕真实 `ask_user` 协议闭环运行：

1. 首轮用 `initial_user_message` 发起 `POST /chat/completions`。
2. 若响应为 `ask_user`，从真实响应里提取 `ask_user.question`、`ask_user.options` 与 `tool_call_id`。
3. benchmark 侧 AI 用户生成一个结构化决策。
4. 运行器将该决策翻译为 `ask_user_answer + metadata.selection_payload` 发回后端。
5. 若后端继续返回 `ask_user`，则继续下一轮；若返回 `documents/message/error`，则 attempt 结束。

当前已存在的主要代码落点：

- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/envs/doc_search/adapters.py`
- `benchmark/doc_search_bench/types.py`

### 2.2 当前主线的真实约束

按当前最新 benchmark 与协议文档，主线约束是：

- 当前主线围绕 `/chat/completions`，而不是 `/search`。
- `ask_user` 的当前正式主线仍以 `single_select` 为主。
- `allow_free_input = false` 仍是当前冻结主线。
- 恢复会话必须依赖 `session_id + ask_user_answer + metadata.selection_payload`。
- “想撤回”可以表达为场景，但当前协议并不真正支持撤回成功。

因此，当前施工方案必须以“真实 `single_select` 渐近披露链路”为第一落点，而不是一开始就设计 `text/number/multi_select` 全覆盖。

### 2.3 当前实现距离真实项目用户模拟还差什么

当前 benchmark 已经能消费真实选项，但它距离“真实项目用户模拟”还有四个关键缺口：

- case 只冻结了输入与终点 gold，尚未冻结“用户认知画像”。
- case 只关心最后是否命中目标文档，尚未把“完整交互轨迹”变成一等评测资产。
- `wrong_selection_budget / rollback_*` 目前主要还是场景描述，尚未变成稳定的策略执行器。
- 当前决策主线本质上仍以 `choose_option` 为核心，还没有形成“普通用户如何理解选项、何时犹豫、何时纠错”的显式模型。

这意味着：

- 当前 benchmark 能判断“最后有没有命中文档”。
- 但还不能稳定分析“用户是如何一步步做出这些选择的”。

## 3. 目标设计原则

### 3.1 真值与用户认知严格分层

必须区分两类信息：

- `world_truth`
  - 给评测器、离线诊断器、报告生成器使用。
  - 包含真实目标文档身份，以及终点核验所需的最小真值。
- `user_state`
  - 只给模拟用户使用。
  - 包含用户知道什么、不确定什么、不知道什么、会用什么俗称、会在哪些近邻选项之间混淆。

硬约束：

- 模拟用户看不到 `target_doc.title/path/file_id/selection_payload`。
- 模拟用户只看得到自己首轮说过的话、历史对话、当前问题、当前选项的 `key/label/description`。

### 3.2 不写死轨迹，只冻结必要真值

真实项目的 `ask_user.options` 是运行时动态生成的，不能把 case 写成：

- 第 1 轮点“选项 2”
- 第 2 轮点“品牌=三一”
- 第 3 轮点“55C”

因为真实返回的选项文案、顺序、分组方式都可能变化。

因此 case 中应该冻结的是：

- 用户目标文档真值。
- 用户真实记忆范围。
- 用户首轮自然表达方式。

不应该冻结的是：

- 固定按钮序号。
- 固定按钮文案。
- 固定轮次脚本。
- 预定义“唯一正确路径”。

### 3.3 “正确路径”未知，不作为样本前置字段

在真实项目里，很多 case 并不存在一条提前就能写死的“正确路径”：

- 真实选项是运行时生成的。
- 同一目标文档可能存在多条都合理的收敛链路。
- 有些路径是否合理，本来就需要跑出来之后结合真实轨迹再分析。

因此本方案不要求 case 增加：

- `correct_path`
- `accepted_paths`
- `route_truth`

benchmark 第一原则是先把“非 oracle 的真实用户选择”跑起来，而不是先把路径真值编进去。

### 3.4 模糊用户不是随机用户

“模糊用户”不等于随机乱选。合理的模糊只允许来自：

- 术语不稳定
- 记忆不完整
- 近邻选项混淆
- 延迟纠错

不允许出现：

- 完全随机点击
- 看不见的真值信息反向指导选择
- 明显冲突选项仍反复被选中
- 每次都像专家一样稳定最优

### 3.5 真值用于终点核验，不用于决策

真实目标文档真值的作用是：

- 判断最终是否命中目标文件。
- 为失败样本提供可解释诊断。
- 为多轮轨迹回放提供终点参照。

真实目标文档真值不用于：

- 直接告诉用户该选哪个选项。
- 让用户模拟器通过 payload 反查答案。
- 在运行时替用户完成收敛。

## 4. 总体架构

建议在当前 benchmark 主线上显式拆出五层职责。

### 4.1 World Truth 层

职责：

- 冻结目标文档身份。
- 冻结终点核验所需的最小真值。
- 只保留评测真正需要的信息。

只对以下模块可见：

- judge
- failure analyzer
- report builder

### 4.2 User State 层

职责：

- 表达一个“普通真实用户”知道什么。
- 表达哪些信息不确定。
- 表达哪些俗称、缩写、错称可以接受。
- 表达是否存在局部混淆、延迟纠错、想撤回等行为风格。

只对模拟用户可见。

### 4.3 Decision Engine 层

职责：

- 消费实时 `ask_user` 问题与选项。
- 消费用户认知状态。
- 产出结构化决策，而不是直接走真值。

输出仍沿用当前主线：

- `choose_option`
- `declare_rollback_intent`
- `stop`
- 未来可扩展 `free_text/number/multi_select`

### 4.4 Runtime Adapter 层

职责：

- 把结构化决策翻译为当前真实后端协议。
- 继续复用当前 `ask_user_answer + metadata.selection_payload`。
- 不引入新的虚构接口。

### 4.5 Trace Recorder / Analyzer 层

职责：

- 不干预用户选择。
- 记录每一轮真实问题、选项、选择结果与最终返回。
- 为离线复盘提供完整轨迹。
- 基于目标文档真值生成终点命中与失败解释。

## 5. 数据模型设计

### 5.1 目标：在现有 `fixture + gold -> TaskCase` 装配链路上增量扩展

当前 `merge_suite_from_paths()` 已经是统一装配入口，因此建议继续保持：

- `fixture`
  - 输入、首轮消息、运行配置、用户画像。
- `gold`
  - 目标文档真值、终点判断真值。

### 5.2 fixture 侧新增字段

建议在现有 `fixture` case 上增量引入：

```json
{
  "user_profile": {
    "persona": "cooperative_vague",
    "goal": "找三一55C挖机电路图",
    "known_facts": {
      "brand": ["三一"],
      "model": ["55C", "SY55"],
      "doc_type": ["电路图", "线路图"]
    },
    "uncertain_facts": {
      "doc_type": ["整车电路图", "仪表针脚图"]
    },
    "unknown_facts": ["file_id", "full_title", "page"],
    "aliases": {
      "电路图": ["线路图", "整车电路图"],
      "55C": ["SY55", "55C"]
    },
    "correction_style": "delayed",
    "notes": "用户不是维修资料专家，术语可能不稳定"
  }
}
```

字段语义：

- `persona`
  - 当前轮廓名称。
- `goal`
  - 用户首轮真正想完成的事情。
- `known_facts`
  - 用户确定记得的信息。
- `uncertain_facts`
  - 用户印象中可能对，但不能强断言的信息。
- `unknown_facts`
  - 用户确实不知道的信息。
- `aliases`
  - 用户会使用的俗称、缩写、同义表达。
- `correction_style`
  - 偏离后多久会尝试修正。

### 5.3 gold 侧新增字段

建议在 `gold` case 上增量引入：

```json
{
  "target_doc": {
    "file_id": "doc_123",
    "title": "三一_SY55_SY60_SY65_SY75-9_仪表显示器针脚定义",
    "doc_path": "三一/挖机/SY55/仪表显示器针脚定义",
    "facets": {
      "brand": "三一",
      "series": "SY",
      "model": "55C",
      "doc_type": "仪表针脚图"
    }
  }
}
```

字段语义：

- `target_doc`
  - 目标文档身份，供终点评测使用。
- `target_doc.file_id`
  - 优先级最高的文档真值标识。
- `target_doc.title`
  - 当返回结果不稳定时，用于辅助匹配。
- `target_doc.doc_path`
  - 可选辅助元数据；如果真实 case 拿得到，就可保留；拿不到也不影响主流程。
- `target_doc.facets`
  - 供离线失败分析使用，不对模拟用户暴露。

### 5.4 运行态必须记录完整交互轨迹

本方案不预定义“正确路径”，但要求运行态完整记录真实轨迹。

至少应记录：

- 首轮用户自然语言。
- 每轮 `ask_user.question`。
- 每轮 `ask_user.options` 快照。
- 用户最终选择了哪个选项。
- 发回后端时的 `ask_user_answer` 与 `selection_payload`。
- 后端回来的类型：
  - `ask_user`
  - `documents`
  - `message`
  - `error`
- 最终返回的文档列表或错误信息。

### 5.5 为什么不要求路径实时可达

本方案关注的是：

- 选项链路是否最终命中正确文档。
- 返回结果里的标题、文件身份或可用元数据是否匹配目标。

因此：

- 可以保留 `doc_path` 作为辅助元数据。
- 不要求运行时一定能打开预览链接。
- 不要求外部 URL 一定可访问。
- 即使没有稳定路径字段，也不影响第一阶段 benchmark 成立。

## 6. 决策策略设计

### 6.1 首轮：只表达目标，不泄露全部信息

首轮仍然沿用真实项目当前模式：

- 用户先发一条自然语言。
- 后续全靠 `ask_user` 渐近披露。

因此首轮策略应冻结为：

- 只表达目标任务。
- 只暴露最自然、最先会说出的那部分信息。
- 不在首轮一次性补完全部 facet。

例如：

- `老师，想找一下三一55C的电路图`
- 而不是把品牌、型号、文档类型、部件、页面需求一次性说完

### 6.2 选项轮：先做符号过滤，再做有限模糊选择

建议每轮 `ask_user` 采用“两段式决策”。

第一段，符号层：

- 基于 `key/label/description` 做归一化。
- 从问题中识别当前大致在问什么维度。
- 过滤和 `known_facts` 明显冲突的选项。
- 给剩余选项打分。

建议基础打分项：

- `exact_match`
  - 选项是否和已知事实完全吻合。
- `alias_match`
  - 是否命中用户俗称或缩写。
- `query_overlap`
  - 是否和首轮目标表述高重叠。
- `contradiction_penalty`
  - 是否与已知事实冲突。
- `over_specific_penalty`
  - 是否比用户已知范围更具体，从而不应轻易确认。
- `familiarity_bonus`
  - 是否使用更像普通用户会认得的词。

第二段，模糊层：

- 若只有一个高分候选，直接选。
- 若存在多个近邻高分候选，允许按 `confusion_profile` 在近邻内误选。
- 若全部都难以判断，优先选“其他/不确定/都不是”之类保守项。
- 若当前主线不支持自由输入，则不得伪造用户并未明确知道的新信息。

### 6.3 模糊逻辑的约束

模糊只允许发生在“合理近邻”中。

允许：

- `55C` 与 `60C` 混淆
- `整车电路图` 与 `线路图` 混淆
- `仪表针脚图` 与 `仪表电路图` 混淆

不允许：

- `三一` 与 `红岩` 混淆
- `挖机` 与 `牵引车` 混淆
- 明知是 `55C` 却直接选 `旋挖` 类无关项

### 6.4 纠错逻辑

真实项目用户不是每次都一步选对，但往往会在后续发现不对时纠偏。

建议引入两类纠错：

- `immediate_correction`
  - 下一轮就意识到不对。
- `delayed_correction`
  - 继续跟一轮后才发现偏了。

纠错不等于撤回协议。

在当前主线下，纠错只能通过：

- 继续消费下一轮真实 `ask_user`
- 或进入错误结果后重新发起新查询

不得伪造“系统已成功撤回上一轮”。

### 6.5 stop 逻辑

用户停止条件应基于“自己觉得已经拿到正确资料”，而不是基于真值判断。

当前 stop 建议依据：

- 助手最终返回的标题或文档元信息与用户目标足够接近。
- 用户不再需要继续澄清。

例如：

- 返回标题里明确包含目标品牌、型号、资料类型。
- 或返回结果的文档身份与用户需求显著一致。

## 7. 轨迹记录与评测设计

### 7.1 不预定义正确路径，但必须保留完整轨迹

这套方案不要求提前写出“正确路径”，但每次运行必须能回放：

- 用户一开始说了什么。
- 系统每轮问了什么。
- 当时提供了哪些选项。
- 用户为什么会选那个选项。
- 最后为什么命中或没命中。

这样 benchmark 至少能稳定回答两件事：

- 非 oracle 的模糊用户，最终能不能找到目标文件。
- 一次失败大概是卡在术语理解、选项文案、纠错失败，还是协议能力不够。

### 7.2 推荐新增的运行报告字段

建议后续在报告里新增以下字段：

- `final_hit`
  - 最终是否命中目标文档。
- `turn_count`
  - 总轮次。
- `decision_trace`
  - 每轮问题、选项、选择、原因摘要。
- `correction_count`
  - 发生了多少次显式纠错。
- `ambiguous_turn_count`
  - 有多少轮属于近邻犹豫后决策。
- `stop_reason`
  - 为什么停止。
- `failure_reason`
  - 若失败，主要失败类型是什么。

### 7.3 推荐的失败分类口径

不依赖预定义路径时，失败分析建议采用以下分类：

- `target_miss`
  - 最终没有命中目标文档。
- `option_understanding_error`
  - 用户对选项文案理解错了。
- `reasonable_ambiguity_miss`
  - 近邻项过于相似，误选后未纠回。
- `insufficient_clarification`
  - 系统澄清链路不足，用户正常认知下无法继续收敛。
- `protocol_capability_gap`
  - 真实协议能力限制导致流程停止。

## 8. 分阶段施工方案

### 阶段 0：冻结边界与非目标

目标：

- 先把“真实项目用户模拟”与“oracle 决策”边界彻底冻结。

主要动作：

- 补齐本工程文档。
- 明确当前主线只覆盖真实 `single_select`。
- 明确真值只给 judge，不给模拟用户。
- 明确不引入固定点击脚本回退方案。
- 明确不要求样本预写正确路径。

涉及文件：

- `docs/modules/doc-search-benchmark/implement/engineering/*.md`
- `docs/modules/doc-search-benchmark/implementation/*.md`
- `docs/modules/doc-search-benchmark/contract/*.md`

完成标准：

- 团队对“用户可见信息”和“评测可见信息”达成统一。
- 团队对“不写 correct_path”达成统一。

### 阶段 1：扩展 case schema，但保持主线兼容

目标：

- 在不破坏当前运行器的前提下，把用户画像与目标文档真值结构补齐。

主要动作：

- 在 `benchmark/doc_search_bench/types.py` 新增：
  - `UserProfile`
  - `TargetDocumentTruth`
- 扩展 `TaskCase`，但新增字段全部可选。
- 扩展 `merge_suite_from_paths()`，支持读取新增字段。
- 先只给少量 `dev/smoke` case 补齐新字段。

涉及文件：

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/data/dev/*.json`
- `benchmark/doc_search_bench/envs/doc_search/data/test/*.json`

完成标准：

- 老 case 不受影响。
- 新 case 能在运行时拿到 `user_profile/target_doc`。

### 阶段 2：落地非 oracle 的符号决策内核

目标：

- 让当前 AI 用户先具备“像普通人一样理解选项”的硬骨架。

主要动作：

- 在 `benchmark/doc_search_bench/user.py` 新增：
  - 选项归一化
  - facet 粗识别
  - hard contradiction 过滤
  - option scoring
  - 近邻混淆判定
- 保持当前结构化输出合同不变：
  - `choose_option`
  - `declare_rollback_intent`
  - `stop`
- 决策时不暴露 `selection_payload`。

涉及文件：

- `benchmark/doc_search_bench/user.py`
- 如有必要新增：
  - `benchmark/doc_search_bench/utils/option_norm.py`

完成标准：

- 在不读取真值 payload 的情况下，用户 agent 能基于实时选项做稳定选择。
- 决策结果可解释，且不依赖写死脚本。

### 阶段 3：在符号内核上叠加 LLM 模糊人格

目标：

- 在不失控的前提下，把用户从“规则机”推进为“普通模糊用户”。

主要动作：

- 让 LLM 只在通过符号过滤后的候选空间内做最后决策。
- 引入 persona 风格：
  - `normal`
  - `cooperative_vague`
  - `term_confused`
- 引入有限混淆预算：
  - `wrong_selection_budget`
- 引入有限纠错风格：
  - `immediate`
  - `delayed`
- 保留 `verify/reflect` 流程，防止不合理输出。

涉及文件：

- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/types.py`

完成标准：

- 用户既不是 oracle，也不是随机机。
- 多次运行中行为有一定自然波动，但不应出现无意义乱选。

### 阶段 4：引入全量轨迹记录与新报告字段

目标：

- 让 benchmark 不只知道“命中没有”，还知道“过程发生了什么”。

主要动作：

- 在 `env.py` 里追加完整 trace 记录。
- 在结果中写入：
  - `final_hit`
  - `turn_count`
  - `decision_trace`
  - `correction_count`
  - `ambiguous_turn_count`
  - `stop_reason`
  - `failure_reason`
- 如有必要，新增独立报告模块，而不是污染现有 `file judge`。

涉及文件：

- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/judges/`
- `benchmark/doc_search_bench/run.py`

完成标准：

- 能解释为什么用户最终 miss。
- 能区分是选项理解问题、合理模糊误选，还是协议能力不足。

### 阶段 5：逐步扩展场景库

目标：

- 把“正常用户”之外的近邻真实场景逐步纳入，而不是一次放开。

建议场景顺序：

1. `normal`
2. `cooperative_vague`
3. `term_confused`
4. `wrong_once_then_correct`
5. `delayed_correction`
6. `rollback_intent`

主要动作：

- 每新增一种场景，只补少量高价值 case。
- 每种场景都要求附带：
  - 用户认知画像
  - 目标文档真值
  - 失败解释口径

涉及文件：

- `benchmark/doc_search_bench/envs/doc_search/data/*/*.json`
- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/user.py`

完成标准：

- 场景扩展是渐进的、可回归的。
- 没有因为一次性引入大量复杂人格而失去可控性。

### 阶段 6：等待真实协议扩展后再接入新输入类型

目标：

- 保持 benchmark 与真实项目协议同步，而不是超前发明接口。

触发条件：

- 真实 `ask_user` 主线开始稳定支持：
  - `allow_free_input = true`
  - `text`
  - `number`
  - `multi_select`

在此之前：

- 当前 benchmark 主线不应假装已经支持这些输入类型。

涉及文件：

- `benchmark/doc_search_bench/user.py`
- `benchmark/doc_search_bench/envs/doc_search/adapters.py`
- `docs/modules/doc-search-benchmark/contract/*.md`

完成标准：

- 新输入类型的 benchmark 能力始终晚于或等于真实协议，不早于真实协议。

## 9. 第一批建议落地范围

若以“最小可执行真实项目施工”作为目标，建议先只做以下范围：

- 阶段 0
- 阶段 1
- 阶段 2
- 阶段 4 的最小版

也就是先做到：

- case 里有用户画像与目标文档真值。
- 模拟用户不再是 oracle，也不再是脚本。
- 仍然只跑当前真实 `single_select` 主线。
- 报告里能看到基本过程留痕与失败原因。

暂时不要一开始就追求：

- 正确路径真值建模
- 全场景人格库
- 大规模误选与纠错样本
- 自由输入型 ask_user
- 页码回合主线

## 10. 代码落点总表

建议按以下文件边界施工：

- 数据模型
  - `benchmark/doc_search_bench/types.py`
- 用户决策
  - `benchmark/doc_search_bench/user.py`
- 多轮运行与 trace 记录
  - `benchmark/doc_search_bench/envs/doc_search/env.py`
- 会话协议适配
  - `benchmark/doc_search_bench/envs/doc_search/adapters.py`
- 文件评测与报告
  - `benchmark/doc_search_bench/judges/file.py`
  - `benchmark/doc_search_bench/judges/` 下新增报告或分析模块
- 样本资产
  - `benchmark/doc_search_bench/envs/doc_search/data/*/*.json`
- 文档
  - `docs/modules/doc-search-benchmark/contract/*.md`
  - `docs/modules/doc-search-benchmark/implementation/*.md`
  - `docs/modules/doc-search-benchmark/implement/engineering/*.md`

## 11. 验收口径

这套方案应用到真实项目后，验收不应只看最终 recall，还应同时满足：

- 用户决策未读取真值 payload。
- benchmark 没有退回固定点击脚本。
- 首轮是自然文本，后续完全依赖真实 `ask_user` 渐近披露。
- 模糊只发生在合理近邻内。
- case 不要求预定义正确路径。
- 报告能区分：
  - 最终命中
  - 最终 miss
  - 合理模糊导致的误选
  - 纠错失败
  - 协议能力缺口导致停止

## 12. 最终结论

把这套方案应用到真实 `CRS_agent` 项目的关键，不是让 benchmark “总能选对”，而是让它具备下面三个性质：

- 用户只能凭自己记得的碎片与实时可见选项做选择。
- 评测只拿目标文档真值做终点核验与失败解释，不提前编造正确路径。
- 所有能力扩展都严格服从当前真实项目协议，而不是 benchmark 自己发明捷径。

只有这样，`doc_search benchmark` 才是在测：

- 真实项目的澄清设计是否符合普通用户认知。
- 真实选项文案是否足够可消费。
- 真实多轮链路是否能把一个模糊但正常的用户，逐步带到正确文档。
