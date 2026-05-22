# 电路图解析后 JSON 文档结构说明

本文说明解析后电路图/维修手册 JSON 的主要结构。该结构用于把一个 PDF 文档拆成页级 OCR 结果，并保留文本块在页面图片中的坐标，后续可支持文档内部搜索、局部截图展示和预览定位。

示例文件：

`/Users/zhangjiexiang/Downloads/CRS共轨之家/文档库智能检索项目/电路图解析后的样例.json`

## 整体形态

一个 JSON 文件对应一个 PDF 文档。

样例文档的基本情况：

- 原始文档名：`福康_F3.8_发动机维修手册-2【CM2620_F137B】【国六】`
- 解析状态：`success`
- 解析模式：`standard`
- 页数：`678`
- OCR 元素总数：`89760`
- 元素类型：样例中全部为 `text`

核心数据在顶层 `pages` 字段中。每个 `pages[]` 表示 PDF 的一页，每页包含页面元数据、OCR 文本元素和可选页面组件。

## 顶层字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `pdf_id` | string | PDF 文档唯一标识，样例中是 hash 字符串。 |
| `source_original_filename_raw` | string | 原始 PDF 文件名，不含或不强调扩展名。 |
| `status` | string | 解析状态，例如 `success`。 |
| `parse_mode` | string | 解析模式，例如 `standard`。 |
| `total_elements` | number | 全文档 OCR 元素总数。 |
| `pages` | array | 页级解析结果，是后续检索和展示的核心字段。 |
| `pdf_level_catalog` | null / object / array | PDF 级目录。样例中为 `null`。 |
| `ocr_runtime_requested` | object | 请求解析时使用的 OCR 配置。 |
| `ocr_runtime_effective_summary` | object | 实际生效的 OCR 配置摘要。 |
| `task_elapsed_seconds` | number | 解析任务耗时，单位秒。 |

## 页级结构 `pages[]`

每一页大致结构如下：

```json
{
  "page_index": 402,
  "page_metadata": {},
  "elements": [],
  "page_components": []
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `page_index` | number | 页序号，从 `0` 开始。展示给用户时通常使用 `page_index + 1`。 |
| `page_metadata` | object | 页面尺寸、DPI、页面图片路径、OCR 配置等元数据。 |
| `elements` | array | 当前页 OCR 识别出的文本块列表。 |
| `page_components` | array | 页面组件列表。样例中为空数组，暂未看到结构化图形/电路组件。 |

## 页面元数据 `page_metadata`

常用字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `page_index` | number | 页序号，与外层 `page_index` 一致。 |
| `original_width_pt` | number | PDF 原始页宽，单位 pt。 |
| `original_height_pt` | number | PDF 原始页高，单位 pt。 |
| `rendered_width_px` | number | 页面渲染图片宽度，单位像素。样例为 `5100`。 |
| `rendered_height_px` | number | 页面渲染图片高度，单位像素。样例为 `6600`。 |
| `dpi` | number | 渲染 DPI。样例为 `600`。 |
| `effective_dpi` | number | 实际生效 DPI。 |
| `padding` | number | OCR 前可能添加的页面 padding。 |
| `image_filename` | string | 页面渲染图片的相对路径。 |
| `image_path` | string | 页面渲染图片的绝对路径。 |
| `ocr_image_filename` | string | OCR 使用的图片相对路径，可能是加 padding 后的图片。 |
| `ocr_image_width_px` | number | OCR 图片宽度。 |
| `ocr_image_height_px` | number | OCR 图片高度。 |
| `ocr_stats` | object | OCR 统计信息，例如耗时、区域数量、字符数量。 |

坐标使用注意：

- `elements[].bounding_box` 坐标与页面渲染图片坐标系一致，通常应按 `rendered_width_px` / `rendered_height_px` 解释。
- 坐标原点在页面左上角。
- 坐标格式为 `[x1, y1, x2, y2]`。
- 样例全文坐标范围大致落在页面图片内，例如最大 `x` 接近 `4957`，最大 `y` 接近 `6484`。

## OCR 文本元素 `elements[]`

样例中的元素全部为文本块：

```json
{
  "type": "text",
  "text_content": "转速表电",
  "reading_order": 1,
  "direction": "horizontal",
  "bounding_box": [288, 166, 600, 278],
  "characters": []
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 元素类型。样例中全部为 `text`。 |
| `text_content` | string | OCR 识别出的文本内容。 |
| `reading_order` | number | OCR 给出的阅读顺序。可用于拼接页文本。 |
| `direction` | string | 文本方向，例如 `horizontal`。 |
| `bounding_box` | number[] | 文本块在页面图片中的坐标，格式 `[x1, y1, x2, y2]`。 |
| `characters` | array | 字符级识别结果，包含每个字符的坐标和置信度。 |

## 字符级结构 `characters[]`

每个文本块下可以包含逐字符坐标：

```json
{
  "char": "转",
  "box_px": [288, 166, 366, 278],
  "confidence": 0.99
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `char` | string | 单个字符。 |
| `box_px` | number[] | 字符在页面图片中的坐标，格式 `[x1, y1, x2, y2]`。 |
| `confidence` | number | OCR 置信度。 |

字符级坐标适合做更精细的高亮，但初版页内搜索可以优先使用文本块级 `bounding_box`。

## 当前样例的结构特点

1. 该 JSON 不是电路图语义结构化结果。
   - 没有看到线束、端子、接插件、元件、导线关系等结构化对象。
   - `page_components` 在样例中为空。
   - `elements[].type` 全部是 `text`。

2. 它更适合被理解为“带坐标的 OCR 页面文本”。
   - 可以做页级全文检索。
   - 可以根据命中文本块坐标生成局部截图。
   - 可以在文档预览中根据页码和坐标定位。

3. OCR 文本可能被拆块。
   - 例如一个词可能被拆成多个相邻元素。
   - 搜索时不应只对单个 `text_content` 做精确匹配。
   - 更稳妥的方式是按 `reading_order` 拼接页文本，并保留文本位置到元素坐标的映射。

4. 页眉、页码和正文都在 `elements` 中。
   - 检索命中标题时，裁剪区域不应只围绕标题，应适当向下扩展以包含正文或图示区域。

## 面向页内搜索的建议索引结构

解析入库时建议把大 JSON 拆成页级索引，避免运行时每次读取完整 JSON。

建议页级索引字段：

| 字段 | 说明 |
| --- | --- |
| `file_id` | 系统内部文档 ID。 |
| `pdf_id` | 解析 JSON 中的 `pdf_id`。 |
| `page_index` | 页序号，从 `0` 开始。 |
| `page_number` | 用户展示页码，通常为 `page_index + 1`。 |
| `page_text` | 按 `reading_order` 拼接后的页面 OCR 文本。 |
| `page_width_px` | 页面图片宽度，对应 `rendered_width_px`。 |
| `page_height_px` | 页面图片高度，对应 `rendered_height_px`。 |
| `image_filename` | 页面图片相对路径。 |
| `image_path` | 页面图片实际路径或对象存储 key。 |
| `elements_compact_json` | 压缩后的文本块列表，至少保留 `text_content`、`reading_order`、`bounding_box`。 |

## 面向局部图展示的命中结果结构

页内搜索命中后，建议返回文档级结果时附带 `body_hits`：

```json
{
  "file_id": "doc_xxx",
  "filename": "福康_F3.8_发动机维修手册-2【CM2620_F137B】【国六】",
  "match_kind": "document_body",
  "body_hits": [
    {
      "hit_id": "doc_xxx_p402_1",
      "page_index": 402,
      "page_number": 403,
      "snippet": "转速表电路 ... ECM ... OEM线束中的转速表信号导线 ...",
      "thumbnail_url": "/chat/api/documents/doc_xxx/pages/402/crop?hit_id=doc_xxx_p402_1",
      "target": {
        "bbox_px": [240, 140, 1800, 1800],
        "bbox_norm": [0.047, 0.021, 0.353, 0.273],
        "highlight_boxes_px": [[288, 166, 699, 278]]
      }
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `hit_id` | 单个页内命中的唯一 ID。 |
| `page_index` | 命中页序号，从 `0` 开始。 |
| `page_number` | 用户展示页码。 |
| `snippet` | 命中文本和上下文摘要。 |
| `thumbnail_url` | 局部截图访问地址。 |
| `target.bbox_px` | 用于裁剪和预览定位的目标区域，像素坐标。 |
| `target.bbox_norm` | 归一化坐标，便于前端不同缩放比例下定位。 |
| `target.highlight_boxes_px` | 命中文本块坐标，可用于高亮。 |

## 局部裁剪策略

初版可采用以下规则：

1. 找到命中 query token 的文本元素。
2. 合并这些元素的 `bounding_box`。
3. 对合并框外扩一定边距。
   - 横向可外扩页面宽度的 5% 到 10%。
   - 纵向可按命中文本高度外扩 6 到 10 倍。
4. 如果命中区域位于页眉，向下扩展更多，避免只截到标题。
5. 裁剪框必须限制在 `[0, 0, page_width_px, page_height_px]` 范围内。
6. 返回裁剪图时可在图上画半透明高亮框，帮助用户快速看到命中位置。

## 文档预览定位

点击局部图后，理想行为是：

1. 打开文档预览。
2. 自动跳转到 `page_index` 对应页面。
3. 自动滚动到 `target.bbox_px` 或 `target.bbox_norm` 对应区域。
4. 显示命中高亮框。

如果预览器是外部 iframe，跨域情况下父页面通常无法精确控制内部滚动位置。要稳定支持坐标级定位，建议后续提供自有预览页，例如：

```text
/document-preview?file_id=doc_xxx&page=402&x=240&y=140&w=1560&h=1660
```

该预览页可基于 PDF 渲染页或解析生成的页面图片实现滚动和高亮。

