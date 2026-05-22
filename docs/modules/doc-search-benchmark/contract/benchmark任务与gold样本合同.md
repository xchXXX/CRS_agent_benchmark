# benchmark 任务与 gold 样本合同

## 1. 文档目的

本文冻结 `doc_search benchmark` 的样本合同，重点覆盖：

- `fixture` 负责什么
- `gold` 负责什么
- 文档命中、页命中、坐标命中的真值如何表达
- `accepted_region_groups` 的正式结构

## 2. fixture 与 gold 分工

### 2.1 fixture

`fixture` 只承载运行输入与用户认知，例如：

- `question_text`
- `question_images`
- `initial_user_message`
- `user_simulation_config`
- `user_profile`

### 2.2 gold

`gold` 只承载评测真值，例如：

- `target_docs`
- `target_match_mode`
- `expected_response_type`
- `accepted_pages`
- `accepted_page_ranges`
- `locator_keywords`
- `accepted_region_groups`

## 3. gold 正式主结构

推荐结构如下：

```json
{
  "target_match_mode": "any_of",
  "target_docs": [
    {
      "file_id": "doc_xxx",
      "title": "东风天锦整车电路图",
      "doc_path": "东风/天锦/整车电路图",
      "facets": {
        "brand": "东风",
        "model": "天锦",
        "doc_type": "电路图"
      },
      "accepted_pages": [12, 13],
      "accepted_page_ranges": [],
      "locator_keywords": ["油门踏板", "APP"],
      "accepted_region_groups": [
        {
          "group_id": "region_001",
          "page_number": 12,
          "label": "油门踏板",
          "boxes_norm": [
            [0.1376, 0.2606, 0.1481, 0.2733],
            [0.1495, 0.2604, 0.1652, 0.2731]
          ],
          "match_mode": "any_box"
        }
      ]
    }
  ],
  "expected_response_type": "documents"
}
```

## 4. 文档级真值

`target_docs` 是正式主真值。

每个目标文档至少允许承载：

- `file_id`
- `title`
- `doc_path`
- `facets`
- `accepted_pages`
- `accepted_page_ranges`
- `locator_keywords`
- `accepted_region_groups`

`target_match_mode` 只允许：

- `any_of`
- `all_of`

默认值：

- `any_of`

## 5. 页级真值

页级真值仍固定为：

- `accepted_pages`
- `accepted_page_ranges`

解释：

- 它们表示“该 case 的正确命中页”
- 它们是坐标判分的前置 gate
- 不再引入 `accepted_locator_pages / accepted_locator_page_ranges`

## 6. 坐标级真值

坐标级真值固定为：

- `target_docs[i].accepted_region_groups`

### 6.1 `accepted_region_groups`

含义：

- 某个目标文档内允许命中的区域组

字段：

- `group_id`
- `page_number`
- `label`
- `boxes_norm`
- `match_mode`

### 6.2 字段解释

`group_id`

- 区域组唯一标识

`page_number`

- 该区域组所属页

`label`

- 人工可读名称
- 可以对应具体文字，但不要求与页面原文逐字一致

`boxes_norm`

- 归一化坐标框
- 格式固定为 `[x1, y1, x2, y2]`
- 取值范围固定在 `[0, 1]`

`match_mode`

- 当前只冻结 `any_box`
- 表示系统输出框命中组内任意合法 box 即视为该组命中

## 7. 多页规则

多页场景下，正式规则如下：

1. 先判断目标文档是否召回。
2. 再判断是否有页命中。
3. 只在命中页集合内比较坐标。
4. 命中页集合内只要任意一页命中任意合法 `accepted_region_group`，即判定坐标成功。

## 8. gold 标注原则

### 8.1 必须预标注

正式 gold 必须提前标好：

- 页真值
- 区域组真值

### 8.2 不允许的做法

- 不允许 case 跑完后再让视觉模型判断通过与否
- 不允许用 LLM 参与正式 gold 标注
- 不允许用像素坐标直接作为长期 gold

### 8.3 推荐标注方式

- 页真值标到 `accepted_pages`
- 坐标真值标到 `accepted_region_groups`
- 一段文字被拆分时放进同一个 group

## 9. V1 / V2 兼容

兼容期内仍允许存在：

- `target_doc`
- `accepted_titles`
- `preferred_title`
- case 级 `accepted_pages`
- case 级 `accepted_page_ranges`

但这些只作为兼容入口，不再是长期正式主真值。

冻结规则：

1. 若存在 `target_docs`，优先按 `target_docs` 读取。
2. 若不存在 `target_docs`，允许从 `target_doc` 回退构造单元素 `target_docs`。
3. 若目标文档级存在 `accepted_region_groups`，不得再回退到 case 级伪坐标字段。

## 10. 真值与运行时字段边界

以下字段属于运行时输出，不属于 gold：

- `body_search.highlight_boxes_px`
- `body_search.viewer_token`
- viewer `metadata`

它们的定位分别是：

- `highlight_boxes_px`
  - 系统返回的命中框
- `viewer_token`
  - 预览令牌
- `metadata`
  - 页尺寸信息

这些字段只用于 benchmark 归一化与判分，不写回样本真值。
