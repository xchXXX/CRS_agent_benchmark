# `/chat/completions` 会话适配器流程说明

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有会话适配器流程。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文档说明阶段 3 的会话适配器在 benchmark 中的工作流程。

## 2. 基本流程

会话适配器流程冻结为：

1. 根据题目构造首轮请求
2. 发到 `/chat/completions`
3. 收到响应后记录第一轮
4. 如果后续拿到了一个真实可提交的选项结果，则构造恢复轮请求
5. 再发到 `/chat/completions`
6. 收到恢复轮响应后记录新的一轮

## 3. 首轮请求流程

### 输入

- `question_text`
- `request_context`
- `mode=doc_search`

说明：

- 召回专项 benchmark 固定使用 `mode=doc_search`，只评测资料搜索链路。
- `mode=auto` 只用于单独排查入口意图路由，不作为召回专项固定运行方式。

### 输出

- 一个 HTTP 请求对象

### 记录内容

首轮回合记录至少包含：

- 这是第 1 轮
- 请求类型是 `initial_message`
- 发出的请求体
- 收到的响应体
- 收到的响应类型

## 4. 恢复轮请求流程

### 输入

- 上一轮的 `session_id`
- 上一轮 `ask_user.tool_call_id`
- 当前用户实际提交的选项文本
- 当前选项对应的 `selection_payload`

### 输出

- 一个恢复轮 HTTP 请求对象

### 记录内容

恢复轮回合记录至少包含：

- 当前是第几轮
- 请求类型是 `ask_user_resume`
- 发送时使用的 `session_id`
- 当前使用的 `tool_call_id`
- 选中的选项 label 或 key
- 选中的 `selection_payload`
- 响应体
- 响应类型

## 5. 当前不进入流程的动作

阶段 3 当前明确不进入流程的动作：

- 回退到上一轮
- 滞后撤回
- 构造任何撤回请求

如果后续 AI 用户场景里出现“想撤回”，当前阶段只能把这个意图交给内部记录层，不能由适配器发出新请求类型。

## 6. `/search` 的处理

阶段 3 不改变 `/search` 的原有行为：

- `/search` 仍然是单次请求
- 不进入会话状态
- 只用于诊断或历史兼容

## 6.1 文档结果归一化实现口径

当 `/chat/completions` 返回 `documents` 时，benchmark 侧会把 `content.results` 归一化成内部文档列表。

当前实现口径：

1. `doc_title` 优先读取：
   - `filename`
   - `title`
   - `name`
   - `file_name`
   - `file_id`
2. `doc_path` 优先读取：
   - `hierarchy_full`
   - `path`
   - `physical_path`
   - `file_path`
   - `doc_path`
3. 若真实外部 `ggzj_*` 结果没有路径字段，则回退到稳定标识：
   - `file_id`
   - `id`
4. 若连稳定标识也缺失，最后再回退到：
   - `filename`
   - `title`

这样做的原因是：

- 外部 `ggzj_*` 结果经常没有本地 `hierarchy_full / path`
- benchmark contract 又要求规范化后的 `PredictedDocument.doc_path` 非空
- 因此需要用稳定文档标识补位，保证 contract judge 评测的是“真实返回是否可识别”为一条文档，而不是误把外部协议差异判成 schema 错误

边界说明：

- 这只解决 `doc_path missing -> SCHEMA_INVALID` 的适配问题
- 不代表该文档一定命中 gold
- 文件命中与否仍由后续 file judge 按 `accepted_titles` 匹配决定

## 7. 阶段 3 完成标志

以下条件满足时，可视为阶段 3 完成：

1. benchmark 端已存在显式的首轮请求构造函数
2. benchmark 端已存在显式的恢复轮请求构造函数
3. 两类请求都能落成统一 HTTP 请求对象
4. 运行环境已能把首轮请求响应写成一条内部回合记录
5. 当前合同未伪造任何撤回能力
