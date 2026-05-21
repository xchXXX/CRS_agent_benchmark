# benchmark 任务与 gold 样本合同

## 1. 文档目的

本文冻结当前 `doc_search benchmark` 的样本合同，重点覆盖：

- `fixture` 侧的用户认知结构
- `gold` 侧的评测真值结构
- V1 / V2 样本兼容读取规则
- 多目标文档真值与页码真值口径

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

- `target_docs`
- `target_match_mode`
- `expected_response_type`
- 页码相关真值
- V1 兼容字段

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

## 5. gold 正式合同

### 5.1 V2 正式字段

从“多目标文档 benchmark”第一阶段开始，`gold` 正式真值以 V2 结构为准。

冻结字段如下：

- `target_docs`
  - 该 case 的全部合法目标文档集合
- `target_match_mode`
  - 多目标命中策略
- `expected_response_type`
  - 当前文档召回类 case 仍应为 `documents`

推荐结构如下：

```json
{
  "target_match_mode": "any_of",
  "target_docs": [
    {
      "file_id": "doc_123",
      "title": "三一_SY55_SY60_SY65_SY75-9_仪表显示器针脚定义",
      "doc_path": "三一/挖机/SY55/仪表显示器针脚定义",
      "facets": {
        "brand": "三一",
        "series": "SY",
        "model": "55C",
        "doc_type": "仪表针脚图"
      },
      "accepted_pages": [],
      "accepted_page_ranges": []
    }
  ],
  "expected_response_type": "documents"
}
```

### 5.2 `target_docs` 正式口径

`target_docs` 是正式主真值，不再以单个 `target_doc` 作为长期主路径。

每个目标文档对象至少应包含：

- `file_id`
  - 文档唯一标识
- `title`
  - 文档正式标题
- `doc_path`
  - 便于人工核对的路径快照
- `facets`
  - 结构化属性快照
- `accepted_pages`
  - 仅属于当前目标文档的页码真值
- `accepted_page_ranges`
  - 仅属于当前目标文档的页码区间真值

冻结规则：

- `target_docs` 允许为单元素数组
- 多目标 case 必须把全部合法目标写入 `target_docs`
- 不允许把多个合法目标继续压平到 `accepted_titles` 冒充正式主真值

### 5.3 `target_match_mode` 正式口径

`target_match_mode` 用于声明多目标 case 的正式判定策略。

当前冻结两类取值：

- `any_of`
  - 命中任意一个目标文档即可通过
- `all_of`
  - 必须命中全部目标文档才可通过

冻结规则：

- 未显式声明时，V2 默认值为 `any_of`
- 单目标 case 即使只有一个 `target_docs` 元素，也允许显式写 `any_of`
- 若 case 业务语义要求“必须覆盖全量目标”，必须显式写 `all_of`

### 5.4 V1 兼容字段

兼容期内，`gold` 仍允许保留以下 V1 字段：

- `target_doc`
- `accepted_titles`
- `preferred_title`
- case 级 `accepted_pages`
- case 级 `accepted_page_ranges`

这些字段的定位统一为：

- 兼容读取入口
- 迁移过渡期冗余字段
- 人工对照辅助字段

它们不再是 V2 的长期正式主真值。

### 5.5 V1 / V2 兼容读取规则

兼容读取顺序冻结如下：

1. 若存在 `target_docs`，必须优先按 V2 读取
2. 若不存在 `target_docs`，允许从 `target_doc` 回退构造单元素 `target_docs`
3. 若 `accepted_titles` 存在，可作为 V1 标题别名集合并入兼容判定，但不得覆盖 V2 的 `target_docs`
4. 若 `preferred_title` 存在，只能作为兼容展示字段，不得高于 `target_docs[].title`
5. 若未显式提供 `target_match_mode`，默认回退为 `any_of`

回退构造单元素 `target_docs` 时：

- `file_id / title / doc_path / facets` 优先继承 `target_doc`
- 页码字段优先继承 case 级 `accepted_pages / accepted_page_ranges`

## 6. gold 中的 `target_doc`

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

但从 V2 合同开始，`target_doc` 的定位降级为：

- V1 兼容读取入口
- 旧报告与旧样本的过渡字段

它不再单独代表完整正式真值，也不对模拟用户暴露。

## 7. 页码真值合同

### 7.1 页码真值下沉

多目标场景下，页码真值必须下沉到目标文档维度。

正式字段位置固定为：

- `target_docs[i].accepted_pages`
- `target_docs[i].accepted_page_ranges`

冻结原因：

- 不同目标文档可能对应不同页码
- 若仍保留 case 级页码主真值，会出现“文件命中 A、页码却拿 B 的真值比较”的合同歧义

### 7.2 当前阶段口径

本阶段只冻结页码真值结构，不升级页码 official gate。

当前阶段要求：

- 文档级多目标为正式改造主线
- 页码继续保持 `shadow`
- case 级页码字段仅用于 V1 兼容读取，不作为 V2 长期正式结构

## 8. 可见性边界

样本合同必须服从以下边界：

- `user_profile` 是模拟用户可消费认知
- `target_docs` / `target_doc` 是评测真值
- 模拟用户不得直接读取 `target_docs`
- 模拟用户不得直接读取 `target_doc`
- 模拟用户不得直接读取原始 `selection_payload`

## 9. train 组织口径

train 数据按 case 类型组织，不按品牌组织。

品牌信息应直接留在 case 内容中，例如：

- `user_profile.known_items`
- `question_text`

而不再依赖 `known_facts.brand` 这类旧结构。
