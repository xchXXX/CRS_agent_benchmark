# doc_search `/chat/completions` 多轮交互协议

> 文档口径提示：
> 本文是当前施工线的真实协议基线文档。
> 它服务于
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 的 `阶段 0` 到 `阶段 4`。

## 1. 文档目的

本文冻结 `doc_search benchmark` 当前允许适配的真实外部协议。
这里的“真实协议”只以当前已实现代码为真源，不引入尚未打通的理想能力。

## 2. 真源范围

本协议以以下代码为准：

- `modules/crs-agent-upstream/backend/app/schemas/chat.py`
- `modules/crs-agent-upstream/backend/app/agent/runtime/service.py`
- `modules/crs-agent-upstream/backend/app/agent/adapters/doc_search_response_adapter.py`
- `modules/crs-agent-upstream/frontend/user/src/App.tsx`
- `modules/crs-agent-upstream/frontend/user/src/components/ClarifyWizard.tsx`

## 3. 当前稳定主链路

当前只冻结并适配下面这条已打通链路：

1. 用户首轮用自然文本发起资料查询
2. 服务返回以下三类结果之一：
   - `documents`
   - `message`
   - `ask_user`
3. 如果返回 `ask_user`，前端展示结构化单选选项
4. 用户选择一个选项后，前端回传 `session_id + ask_user_answer + metadata.selection_payload`
5. 服务继续收敛，直到返回：
   - `documents`
   - `message`
   - `error`

## 4. 当前协议冻结边界

当前主线强约束如下：

- `ask_user.input_type = single_select`
- `ask_user.allow_free_input = false`
- 恢复请求必须依赖 `session_id + ask_user_answer + metadata.selection_payload`
- benchmark 不得自造新的 rollback 请求
- benchmark 不得假装已经支持 `text / number / multi_select`

## 5. 当前明确不在主协议内的能力

当前代码真源下，以下能力都还没有进入稳定主链路：

- `ask_user` 选错后的协议级撤回
- 滞后撤回
- 单独的“回退请求”类型
- 最终结果出来后的撤回
- `allow_free_input = true` 的自由输入澄清主链路

因此 benchmark 可以表达：

- “用户想撤回”
- “用户曾误选”

但不能伪装成：

- 系统已经支持撤回
- 当前主线已经支持自由输入澄清

## 6. 请求协议

### 6.1 首轮请求

固定请求：

- `POST /chat/completions`

最小请求体：

```json
{
  "message": "帮我找东风电路图",
  "context": {},
  "mode": "doc_search"
}
```

字段约束：

- `message`
  - 必填
  - 为用户首轮自然语言问题
- `context`
  - 选填，但建议显式传空对象或图文上下文对象
- `mode`
  - 召回专项 benchmark 固定为 `doc_search`
  - `auto` 只用于入口意图路由排查，不用于固定召回评测
- `session_id`
  - 首轮可不传
  - 由后端生成并在响应中返回
- `ask_user_answer`
  - 首轮不传

### 6.2 恢复轮请求

当上一轮响应为 `ask_user` 时，恢复轮仍然请求：

- `POST /chat/completions`

最小请求体：

```json
{
  "session_id": "<first_response.session_id>",
  "ask_user_answer": {
    "tool_call_id": "<first_response.ask_user.tool_call_id>",
    "answer": "天锦",
    "metadata": {
      "selection_payload": {
        "filters": {
          "brand": "东风",
          "series": "天锦"
        },
        "file_ids": []
      }
    }
  }
}
```

字段约束：

- `session_id`
  - 必填
  - 必须直接复用上一轮响应返回值
- `ask_user_answer.tool_call_id`
  - 必填
  - 必须直接复用上一轮 `ask_user.tool_call_id`
- `ask_user_answer.answer`
  - 必填
  - 当前主线可使用选项 `label` 或 `key`
- `ask_user_answer.metadata.selection_payload`
  - 必须显式回传
  - 必须来自上一轮可消费选项里的 `selection_payload`

## 7. 响应协议

### 7.1 终态一：`documents`

```json
{
  "type": "documents",
  "session_id": "<session_id>",
  "business": "DOC_SEARCH",
  "content": {
    "query": "东风电路图",
    "results": [],
    "summary": "找到 3 个相关文档"
  }
}
```

### 7.2 终态二：`message`

```json
{
  "type": "message",
  "session_id": "<session_id>",
  "business": "DOC_SEARCH",
  "content": {
    "message": "根据已选择的条件未找到资料。"
  }
}
```

### 7.3 中间态：`ask_user`

```json
{
  "type": "ask_user",
  "session_id": "<session_id>",
  "business": "DOC_SEARCH",
  "ask_user": {
    "tool_call_id": "<tool_call_id>",
    "question": "请选择车型系列",
    "input_type": "single_select",
    "options": [],
    "allow_free_input": false,
    "context": {}
  },
  "clarify_options": [
    {
      "key": "天锦",
      "label": "天锦",
      "selection_payload": {
        "filters": {
          "brand": "东风",
          "series": "天锦"
        },
        "file_ids": []
      }
    }
  ]
}
```

当前 benchmark 关注的稳定字段：

- `type`
- `session_id`
- `business`
- `ask_user.tool_call_id`
- `ask_user.question`
- `ask_user.input_type`
- `ask_user.allow_free_input`
- `ask_user.options`
- `clarify_options[*].selection_payload`

## 8. `selection_payload` 的使用边界

当前冻结口径：

- 适配器与运行态允许消费原始 `selection_payload`
- 诊断资产允许保留原始 `selection_payload`
- 面向标准报告层不得直接暴露原始 `selection_payload`

## 9. 新阶段触发条件

如果未来服务出现以下任一情况，应视为新的施工阶段，而不是继续沿用当前协议冻结：

- `ask_user.allow_free_input = true`
- `input_type != single_select`
- 出现正式落地的撤回请求协议
- 前端新版 `ask_user` 路径打通真实回退

## 10. 当前撤回类 case 的 benchmark 口径

当前撤回类 case 只能做到：

- 记录 AI 用户想撤回
- 记录撤回目标是第几轮
- 记录当前协议不支持执行

当前不能做到：

- 真正向后端发一个可执行的撤回请求
- 真正回到上一轮后重新提交另一选项
