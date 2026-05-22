# 电路图内搜索 Viewer 使用说明

本文记录当前图内搜索 viewer 的接入方式，便于后续尝试新 viewer 方案时对照替换。

## 当前模块

当前 viewer 是前端弹层组件，不是独立路由页面。

- 组件：`frontend/user/src/components/CircuitDocumentViewer.tsx`
- 样式：`frontend/user/src/components/CircuitDocumentViewer.css`
- API 封装：`frontend/user/src/services/api.ts`
- 打开入口：`frontend/user/src/App.tsx` 的 `openSearchResultDocument`

外部结果列表中，如果某个搜索结果包含 `body_search.status === "hit"` 且能解析出 `viewer_token`，点击“定位查看”会打开 `CircuitDocumentViewer`。如果没有图内 viewer token，会降级打开原始文档 viewer。

## 打开链路

外部搜索结果卡片调用：

```ts
openSearchResultDocument(result, hit)
```

当前逻辑：

1. 从 `hit.viewer_token`、`result.body_search.viewer_token` 或 `preview_image_url` 中提取 `viewerToken`。
2. 如果 `body_search.status === "hit"` 且存在 `viewerToken`，设置 `circuitViewerDoc`。
3. `App.tsx` 根据 `circuitViewerDoc` 渲染 `CircuitDocumentViewer`。
4. viewer 内部加载 metadata、页图，并执行初始关键词搜索。

挂载代码形态：

```tsx
{circuitViewerDoc && (
  <CircuitDocumentViewer
    key={circuitViewerDoc.closeToken}
    title={circuitViewerDoc.title}
    token={circuitViewerDoc.token}
    initialKeyword={circuitViewerDoc.initialKeyword}
    initialHitId={circuitViewerDoc.initialHitId}
    initialPageIndex={circuitViewerDoc.initialPageIndex}
    fallbackPdfUrl={circuitViewerDoc.fallbackPdfUrl}
    fallbackPage={circuitViewerDoc.fallbackPage}
    closeToken={circuitViewerDoc.closeToken}
    onClose={(token) => {
      if (!circuitViewerDoc || (token && token !== circuitViewerDoc.closeToken)) return
      setCircuitViewerDoc(null)
    }}
  />
)}
```

## Props 约定

`CircuitDocumentViewer` 当前接收：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `title` | `string` | 顶部标题，metadata 未返回 filename 时兜底显示 |
| `token` | `string` | 后端 viewer token，用于 metadata、页图、搜索接口 |
| `initialKeyword` | `string | undefined` | 打开后自动搜索的关键词 |
| `initialHitId` | `string | undefined` | 初始希望选中的命中 ID |
| `initialPageIndex` | `number | undefined` | 初始页码，0-based |
| `fallbackPdfUrl` | `string | undefined` | viewer 加载失败时打开原始 PDF |
| `fallbackPage` | `number | undefined` | 打开原始 PDF 时使用的页码，1-based |
| `closeToken` | `string` | 防止旧关闭事件误关新 viewer |
| `onClose` | `(token?: string) => void` | 关闭弹层 |

注意：`token` 是后端签发的 viewer token，不是用户登录 token。

## 后端接口

前端通过 `services/api.ts` 调用以下接口。

### 获取 metadata

```http
GET /chat/api/circuit-body-search/viewer/{token}/metadata
```

返回核心字段：

```ts
interface CircuitViewerMetadata {
  pdf_id: string
  filename: string
  keyword: string
  initial_hit_id?: string
  initial_page_index: number
  initial_page_number: number
  initial_highlight_boxes_px?: number[][]
  total_pages: number
  pages: Array<{
    page_index: number
    page_number: number
    width_px: number
    height_px: number
  }>
  has_result_json?: boolean
  has_source_pdf_url?: boolean
}
```

用途：

- 初始化页码。
- 获取页面尺寸，用于 bbox 到页面坐标的换算。
- 没有搜索结果时，用 `initial_highlight_boxes_px` 兜底绘制初始红框。

### 获取页图

```http
GET /chat/api/circuit-body-search/viewer/{token}/page/{page_index}/image
```

返回 PNG 图片。前端直接作为 `<img src>` 使用。

注意：

- `page_index` 是 0-based。
- 后端可能从解析结果页图或原始 PDF 渲染。
- 后端当前返回 `Cache-Control: private, max-age=86400`。

### viewer 内搜索

```http
POST /chat/api/circuit-body-search/viewer/{token}/search
Content-Type: application/json

{
  "keyword": "油门踏板",
  "limit": 200
}
```

返回核心字段：

```ts
interface CircuitViewerSearchResponse {
  keyword: string
  pdf_id?: string
  initial_hit_id?: string
  total_matches: number
  positioned_match_count: number
  truncated: boolean
  results: CircuitViewerHit[]
  page_summary: Array<{
    page_index: number
    page_number: number
    match_count: number
  }>
}

interface CircuitViewerHit {
  hit_id: string
  page_index: number
  page_number: number
  bbox_px: [number, number, number, number]
  matched_text: string
  context?: string
  reading_order?: number
  element_index?: number
  char_start?: number
}
```

用途：

- `results` 用于底部“上一项/下一项”和页面红框绘制。
- `page_summary` 用于左侧页码栏显示每页命中数量。
- `bbox_px` 是文档坐标系下的框，前端按当前页尺寸换算为百分比绘制。

## token 来源

viewer token 由后端 `CircuitBodySearchEnhancer._attach_preview_fields` 生成。

后端会为 `summary.top_hits` 中有 `highlight_boxes_px` 的 hit 签发 token，并写入：

- `hit.viewer_token`
- `hit.preview_image_url`
- 如果命中是 `best_hit`，还会写入 `summary.viewer_token`

前端解析顺序：

1. `hit.viewer_token`
2. `bodySearch.viewer_token`
3. 从 `preview_image_url` 的 `/circuit-body-search/preview/{token}` 中截取 token

因此新方案如果想复用打开链路，至少要继续提供 `viewer_token` 或可解析 token 的 `preview_image_url`。

## 前端结果列表和 viewer 的关系

外部结果列表仍以“文档”为一级单位。图内命中作为文档下方子列表展示。

当前展示策略：

- `body_search.top_hits` 优先展示。
- 如果没有 `top_hits`，退回展示 `body_search.best_hit`。
- 外部子列表最多展示 3 个位置。
- 同页坐标中心相近的候选在外层合并显示，只保留排序靠前项。
- 其余命中通过“还有 X 处命中，进入文档内查看”进入 viewer。

这些压缩只影响外部列表，不影响 viewer 内重新搜索和完整结果浏览。

## viewer 内部行为

### 初始化

1. 挂载后请求 metadata。
2. 设置当前页：
   - 优先使用 `initialPageIndex`
   - 否则使用 metadata 的 `initial_page_index`
3. 如果 metadata 有 `initial_highlight_boxes_px`，默认放大到 `180%`。
4. 如果 `initialKeyword` 或 metadata 的 `keyword` 非空，自动调用 viewer 搜索接口。
5. 搜索返回后，优先选中：
   - `initialHitId` 对应结果
   - 当前初始页上的第一个结果
   - 搜索结果中的第一个结果

### 页图和红框

页图通过 `getCircuitViewerPageImageUrl(token, currentPageIndex)` 获取。

红框绘制流程：

1. 读取当前页命中 `currentPageHits`。
2. 使用 metadata 当前页尺寸、文档已知页尺寸或图片自然尺寸确定坐标系。
3. 如果 bbox 超出当前尺寸，会扩展坐标系避免框被绘制到页面外。
4. 将 bbox 换算为百分比：

```ts
left = x1 / effectivePageWidth
top = y1 / effectivePageHeight
width = (x2 - x1) / effectivePageWidth
height = (y2 - y1) / effectivePageHeight
```

红框严格按 bbox 绘制，不额外放大。

### 定位和切换

打开或切换命中时：

- 如果命中在其他页，先切换页。
- 页图加载完成后调用 `focusOnHit`。
- `focusOnHit` 会把 bbox 中心滚动到 viewer 主视口中心。

底部按钮：

- `上一项`
- `下一项`
- 命中数量显示
- 原文 fallback 按钮

### 搜索框

顶部搜索框调用 viewer search 接口，不走外部文档搜索链路。

搜索成功后：

- 更新 `searchResponse`
- 选中最合适的第一个命中
- 切换到命中页
- 如果当前缩放低于 `180%`，提升到 `180%`

搜索失败显示 `图内搜索失败，请稍后重试`。

## 手势和缩放

当前 viewer 支持：

- 单指拖动页图平移。
- 双指缩放。
- 缩放按钮。
- 缩放滑条。
- 重置缩放。

缩放范围：

- 最小：`75%`
- 最大：`400%`
- 初始：`100%`
- 命中定位默认：`180%`

缩放锚点：

- 按钮和滑条以当前屏幕中心为锚点。
- 双指缩放以两指中点为锚点。

注意：

- viewer 不再修改 `document.body.style.overflow`。
- 应用主页面本身使用 `.messages-container` 滚动，退出 viewer 时不应改 body overflow。
- viewer 卸载时会释放 pointer capture，避免手势状态影响外层页面。

## 失败降级

metadata 加载失败：

- 显示 `图内查看信息加载失败`
- 如果有 `fallbackPdfUrl`，展示打开原文按钮

页图加载失败：

- 显示 `页图加载失败`
- 如果有 `fallbackPdfUrl`，展示打开原文按钮

搜索失败：

- 显示内联错误
- 不关闭 viewer

无搜索结果：

- 显示 `当前文档未找到“xxx”`

## 当前方案的关键契约

后续替换新 viewer 时，建议保留以下契约，减少对外层列表的改动：

1. `App.tsx` 仍通过 `setCircuitViewerDoc` 打开内部 viewer。
2. viewer 入参继续支持：
   - `token`
   - `initialKeyword`
   - `initialHitId`
   - `initialPageIndex`
   - `fallbackPdfUrl`
   - `fallbackPage`
3. 后端 viewer token 仍能换取：
   - metadata
   - page image
   - document-internal search
4. viewer 内部搜索结果继续使用 `bbox_px` 表示定位框。
5. 页码继续使用：
   - API 内 `page_index` 为 0-based
   - 展示用 `page_number` 为 1-based
6. 关闭 viewer 时只清理 viewer 自己的状态，不修改主页面滚动容器。

如果新方案改为独立页面或 iframe，外层最好仍保留一个小适配层，把当前 `circuitViewerDoc` 转换成新方案 URL 或 props。

## 新方案替换建议

替换时可以按三层拆：

1. 外部列表不动：继续产生 `circuitViewerDoc`。
2. 适配层：新增一个 `CircuitDocumentViewerAdapter`，接收当前 props。
3. 新 viewer：内部可以换成独立页面、iframe、canvas viewer 或第三方 PDF viewer。

这样可以保证：

- 外部搜索结果列表无需重写。
- 后端 token 和搜索接口可以逐步替换。
- 当前 fallback 原文逻辑还能继续复用。

