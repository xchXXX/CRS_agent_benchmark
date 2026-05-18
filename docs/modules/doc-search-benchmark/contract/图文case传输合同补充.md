# 图文 case 传输合同补充

## 1. 目的

本补充合同冻结 benchmark 图文 case 在当前 CRS 项目中的运行时传输协议，避免出现“case 自带图片但运行时只传文字”的偏差。

## 2. 适用范围

- 仅适用于 `benchmark/` 内 `doc_search` benchmark
- 不修改项目前后端业务合同
- 只约束 benchmark 如何复用项目现有图片入口

## 3. 首轮请求规则

- 当 `question_images` 为空时，benchmark 首轮继续调用 `/chat/completions`
- 当 `question_images` 非空时，benchmark 首轮必须调用 `/chat/completions-with-images`

## 4. 图文 case 请求体规则

图文 case 首轮请求必须使用 `multipart/form-data`。

固定字段：

- `request`
  - `ChatRequest` 的 JSON 字符串
- `images`
  - 重复文件字段
  - 逐张上传 `question_images` 指向的本地图片

`request` 中至少应包含：

- `message`
- `context`
- `mode`
- `client_type=benchmark`

## 5. 图片证据责任边界

- benchmark 不负责在本地预先把图片转成 `context.image_evidences`
- benchmark 只负责把图片文件和文字问题一起发到 `/chat/completions-with-images`
- 图片证据识别由项目后端在该接口内部完成
- 后端完成识别后，会自动把结果注入会话 `context.image_evidences`

## 6. used_image_context 判定

图文 case 的 `used_image_context=true` 允许通过以下任一条件成立：

- 首轮前已具备非空 `request_context`
- 首轮通过 `/chat/completions-with-images` 成功发送了 `question_images`

因此图文 case 不应再因 fixture 中 `request_context={}` 而被一律判定为 `OCR_CONTEXT_MISSING`

## 7. 运行记录要求

当 `question_images` 非空时，首轮 `request_kind` 应允许记录为：

- `initial_message_with_images`

对应请求接口应记录为：

- `/chat/completions-with-images`
