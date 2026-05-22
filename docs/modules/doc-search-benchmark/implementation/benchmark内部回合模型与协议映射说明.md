# benchmark 内部回合模型与协议映射说明

## 1. 文档目的

本文说明 benchmark 内部模型如何映射现有后端协议中的文档定位结果，尤其是页级与坐标级字段。

## 2. 映射总原则

1. 外部协议按真实 `/chat/completions` 响应承载。
2. benchmark 内部模型按标准结果结构承载。
3. 不修改后端协议，只做读取与归一化。

## 3. `documents` 响应中的定位映射

当外部协议返回：

- `type = documents`
- `content.results[i].body_search`

内部模型需要映射两层信息。

### 3.1 页级映射

外部字段：

- `body_search.status`
- `body_search.best_hit.page_number`
- `body_search.top_hits[].page_number`

内部字段：

- `prediction.locator_status`
- `prediction.locator_best_page`
- `prediction.locator_top_pages`

### 3.2 坐标级映射

外部字段：

- `body_search.best_hit.highlight_boxes_px`
- `body_search.top_hits[].highlight_boxes_px`
- `body_search.viewer_token`

内部字段：

- `prediction.coord_predicted_page_numbers`
- `prediction.coord_predicted_boxes_px`
- `prediction.coord_viewer_token`

若 viewer 链路可补到页元数据，还需映射：

- `prediction.coord_predicted_boxes_norm`
- `prediction.coord_metadata_present`

## 4. 字段解释

### 4.1 `body_search.highlight_boxes_px`

表示：

- 系统在命中页上的高亮框
- 单位是页像素坐标

benchmark 映射动作：

- 读取
- 归一化
- 与 gold 比较

### 4.2 `viewer_token`

表示：

- 后端签发的预览令牌

benchmark 映射动作：

- 记录存在性
- 用于恢复页尺寸上下文
- 不作为通过条件

### 4.3 `metadata`

表示：

- viewer 页元数据

benchmark 映射动作：

- 取 `width_px / height_px`
- 把像素框换成 `boxes_norm`

## 5. gold 映射

gold 中的：

- `accepted_pages`
- `accepted_page_ranges`
- `accepted_region_groups`

分别映射到内部：

- 页级真值
- 页级区间真值
- 坐标级区域组真值

## 6. 判分映射

内部 judge 顺序固定为：

1. `judge_contract`
2. `judge_file`
3. `judge_page`
4. `judge_locator`
5. `judge_coord`

其中：

- `judge_locator`
  - 负责页级 body_search 定位结果
- `judge_coord`
  - 负责坐标级命中
