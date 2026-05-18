# benchmark 任务与 gold 样本合同

## 1. 文档目的

本文冻结当前 `doc_search benchmark` 的样本合同，重点覆盖：

- `fixture` 侧的用户认知结构
- `gold` 侧的评测真值结构

## 2. 样本分层

### 2.1 fixture

`fixture` 负责运行输入与用户可见认知。

它承载：

- 首轮自然语言
- 运行配置
- 会话限制
- `user_simulation_config`
- `user_profile`

### 2.2 gold

`gold` 负责评测真值与终点核验。

它承载：

- `accepted_titles`
- `preferred_title`
- `expected_response_type`
- 页码相关真值
- `target_doc`

## 3. fixture 中的 `user_profile`

当前冻结结构如下：

```json
{
  "user_profile": {
    "persona": "cooperative_vague",
    "goal": "找三一55C挖机电路图",
    "known_items": ["三一", "55C", "挖机", "电路图"],
    "uncertain_items": ["整车电路图"],
    "aliases": {
      "电路图": ["线路图", "整车电路图"],
      "55C": ["SY55", "55C"]
    },
    "correction_style": "delayed",
    "notes": "用户不是维修资料专家，术语可能不稳定"
  }
}
```

字段含义：

- `persona`
  - 用户画像名
- `goal`
  - 用户真正想完成的事情
- `known_items`
  - 用户明确知道的线索片段
- `uncertain_items`
  - 用户不完全确定但可能会提到的线索片段
- `aliases`
  - 用户常用俗称、缩写、近义表达
- `correction_style`
  - 偏离后多久会纠偏
- `notes`
  - 额外说明

## 4. fixture 中删除的字段

当前不再使用以下字段：

- `known_facts`
- `uncertain_facts`
- `unknown_facts`
- `wrong_selection_budget`

这些字段不再进入当前 benchmark 主链。

## 5. gold 中的 `target_doc`

当前 `gold` 侧仍允许：

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

但 `target_doc` 只用于评测，不对模拟用户暴露。

## 6. 可见性边界

样本合同必须服从以下边界：

- `user_profile` 是模拟用户可消费认知
- `target_doc` 是评测真值
- 模拟用户不得直接读取 `target_doc`
- 模拟用户不得直接读取原始 `selection_payload`

## 7. train 组织口径

train 数据按 case 类型组织，不按品牌组织。

品牌信息应直接留在 case 内容中，例如：

- `user_profile.known_items`
- `question_text`

而不再依赖 `known_facts.brand` 这类旧结构。
