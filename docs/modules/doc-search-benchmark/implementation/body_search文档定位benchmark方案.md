# body_search 文档定位 benchmark 方案

## 1. 文档目的

本文冻结 `doc_search benchmark` 的文档内文字坐标定位方案。

本方案只说明 benchmark 如何消费现有后端返回，完成“文档命中 -> 页命中 -> 坐标命中”的三层定位判定与统计，不允许修改任何前后端业务代码。

## 2. 范围与边界

### 2.1 本次范围

- 只改 `benchmark/`
- 只改 `docs/modules/doc-search-benchmark/`
- 正式接入文档内文字坐标定位 benchmark
- gold 必须预设，不允许运行后再靠人工主观补坐标

### 2.2 明确非目标

- 不改 `modules/crs-agent-upstream/backend/**`
- 不改 `modules/crs-agent-upstream/frontend/**`
- 不让 LLM 参与 gold 标注
- 不让 LLM 参与正式判分
- 不让 LLM 参与后期审核与复审
- 不把像素坐标作为正式真值长期保存

## 3. 方案结论

本方案冻结以下结论：

1. 坐标定位 benchmark 必须建立在现有后端 `body_search` 返回之上。
2. 坐标定位统计采用三层判定链路：
   - 文档命中
   - 页命中
   - 命中页上的坐标命中
3. `official gate` 仍只看文件召回；页级与坐标级不进入最终 pass/fail 主门。
4. 坐标 gold 必须预设，字段位置固定在 `target_docs[i].accepted_region_groups`。
5. 坐标真值以归一化坐标 `boxes_norm` 保存，不以像素坐标作为正式 gold。
6. 多页场景下，只要命中页集合中任意一页命中任意合法区域组，即判定坐标成功。
7. 不要求 gold 里的 `label` 与页面原文逐字相等，`label` 只是人工可读名称。
8. `accepted_region_groups` 是正式坐标真值；`accepted_pages / accepted_page_ranges` 继续承担页级真值。

## 4. 为什么必须预设坐标 gold

不采用“case 跑完后再让视觉模型判断是否通过”，原因如下：

1. 正式 benchmark 需要可重复、可回放、可审计，运行后由模型判分会破坏确定性。
2. 视觉模型判分本质上又引入新的模型误差，无法区分到底是被测系统错了，还是审核模型错了。
3. 坐标定位属于几何判定问题，应该由可计算规则收口，而不是交给主观模型判断。

因此本方案固定为：

- gold 预标注
- 运行时只做规则比较
- 人工复核只处理标注本身，不介入正式判分主链

## 5. 术语解释

### 5.1 `body_search.highlight_boxes_px`

含义：

- 后端在命中文档页后返回的高亮框
- 单位是当前页渲染坐标系下的像素
- 它表示“系统最终返回的定位结果”

注意：

- 它不是“机器学习预测框”这个狭义术语
- 这里的“预测”如果出现，只是 benchmark 里对“系统输出结果”的统称
- 真正做 benchmark 比较时，它扮演的是“被测输出”

### 5.2 `viewer_token`

含义：

- 后端签发的预览令牌
- 令牌里封装了命中页、命中文本框、源 PDF 与结果路径等预览所需参数

benchmark 中的作用：

- 只作为预览链路与辅助取数入口
- 可用于恢复 `initial_highlight_boxes_px`
- 可用于拿到页面 `metadata`
- 不作为正式通过条件
- 不作为二次检索输入

### 5.3 `metadata`

本文中的 `metadata` 指 viewer 页元数据，核心是：

- `width_px`
- `height_px`

benchmark 中的作用：

- 把后端返回的 `highlight_boxes_px` 从像素坐标换算为归一化坐标
- 让不同分辨率、不同渲染来源下的结果能和同一份 gold 比较
- 运行时当前只要求保留“是否存在可用 metadata”与归一化后的结果，不强制把宽高数值写入标准报告

### 5.4 `accepted_region_groups`

含义：

- 一个“业务上可接受的命中区域组”
- 一个组内可以有一个或多个 box
- 适合表达一段文本被 OCR / 渲染拆成多个框的情况

### 5.5 `label`

含义：

- 某个 `accepted_region_group` 的人工可读名称
- 可以对应定位的具体文字，也可以是业务短名

约束：

- 用于标注、报告、人工理解
- 不作为严格逐字匹配依据

## 6. gold 结构

推荐结构如下：

```json
{
  "target_match_mode": "any_of",
  "target_docs": [
    {
      "file_id": "doc_xxx",
      "title": "东风天锦整车电路图",
      "doc_path": "东风/天锦/整车电路图",
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
        },
        {
          "group_id": "region_002",
          "page_number": 13,
          "label": "油门踏板",
          "boxes_norm": [
            [0.4221, 0.3310, 0.4688, 0.3664]
          ],
          "match_mode": "any_box"
        }
      ]
    }
  ],
  "expected_response_type": "documents"
}
```

## 7. 判分口径

### 7.1 第一层：文档命中

只要目标文档未召回，直接失败：

- `document_hit = false`
- `coord_eligible = false`
- `coord_failure_reason = DOC_RECALL_MISS`

### 7.2 第二层：页命中

页级结果在实现上作为独立定位统计维度产出；它本身不再额外依赖文档命中 gate。

坐标层在正式判定时，仍然只会消费“文档已命中且页已命中”的 case。

- `body_search.best_hit.page_number`
- `body_search.top_hits[].page_number`

只要没有任何命中页落入 gold 页集合，直接失败：

- `page_hit_at_k = false`
- `coord_eligible = false`
- `coord_failure_reason = PAGE_RECALL_MISS`

### 7.3 第三层：坐标命中

只在“文档已命中且至少有页命中”时启用。

判定逻辑：

1. 先按 `target_docs/accepted_titles` 解析目标文档结果，再取这些目标文档里系统实际返回的命中页集合。
2. 在这些页上读取 `highlight_boxes_px`。
3. 用页 `metadata.width_px / height_px` 转成 `boxes_norm`。
4. 只与该页的 `accepted_region_groups` 比较。
5. 只要任意一页存在任意一个合法 `group` 命中，即 `coord_hit = true`。

## 8. 为什么使用 `group`

一个目标文字区域经常会出现以下情况：

- 同一段文本被拆成多个 OCR box
- 同一个业务词在图纸上跨两行
- 同一个命中区域需要允许多个小框共同表达

因此真值不能只存一个 box，而应存一个 group：

- `group_id`
- `page_number`
- `label`
- `boxes_norm`
- `match_mode`

当前默认冻结：

- `match_mode = any_box`

表示：

- 系统返回框只要命中这个组内任意一个合法 box，就算这个组命中

## 9. 坐标比较规则

本期实现采用保守规则：

1. 先把系统返回的像素框归一化为 `boxes_norm`。
2. 与 gold `boxes_norm` 做几何重叠判定。
3. 默认判定单元为“预测框命中任一 gold box”。
4. 组级成功条件为：
   - `match_mode=any_box` 时，任意 box 命中即成功

重叠阈值在施工阶段固定进 benchmark judge，不由样本逐条自定义。

## 10. viewer 数据在 benchmark 中的使用方式

### 10.1 使用

- 用 `viewer_token` 恢复预览上下文
- 用 viewer metadata 获取命中页尺寸
- 用于把 `highlight_boxes_px` 归一化

### 10.2 不使用

- 不拿 `viewer_token` 做成功判分
- 不拿 metadata 做检索真值
- 不把 viewer 页面内容再喂给 LLM 审核

## 11. 报告口径

标准报告至少新增以下字段：

- `coord_eligible`
- `coord_status`
- `coord_hit`
- `coord_hit_page_numbers`
- `coord_hit_group_ids`
- `coord_predicted_boxes_norm`
- `coord_failure_reason`
- `coord_viewer_token_present`
- `coord_metadata_present`

并保留以下辅助字段：

- `locator_status`
- `locator_best_page`
- `locator_top_pages`
- `locator_viewer_token_present`
- `locator_preview_present`

## 12. 风险与控制

### 12.1 风险

- 后端返回框没有页尺寸，导致无法归一化
- 命中文本被拆成多个框，单框 gold 表达不全
- 多页文档同一关键词在多个页重复出现

### 12.2 控制

- 坐标真值固定使用 `accepted_region_groups`
- 运行时只在页命中集合内比较坐标
- metadata 缺失时明确记失败码，不做隐式通过
- 多页只要任意合法页命中任意合法 group 即可成功

## 13. 冻结结论

本次 `body_search` 文档定位 benchmark 方案最终冻结如下：

1. benchmark 正式补齐三层定位判定链路：文档命中、页命中、坐标命中。
2. `official gate` 仍只看文件召回，不把页级与坐标级直接升级为最终 pass/fail 主门。
3. 坐标 gold 必须预设，不允许 LLM 参与标注、判分、审核。
4. 坐标真值字段固定为 `target_docs[i].accepted_region_groups`。
5. 组内允许多个 box，默认 `match_mode=any_box`。
6. 多页场景下，命中页集合内任意一页命中任意合法 group 即算成功。
7. `highlight_boxes_px` 是系统返回的定位结果；`viewer_token + metadata` 只用于归一化与可视化，不直接决定通过与否。
