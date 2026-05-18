# 图文 case 传输实现说明

## 1. 目标

说明 benchmark 如何按当前项目真实前后端链路，把 case 自带图片与文字一起传入系统。

## 2. 实现原则

- 无图 case 继续走原有 JSON `/chat/completions`
- 图文 case 改走项目现有 multipart 入口 `/chat/completions-with-images`
- benchmark 不本地复刻图片识别逻辑
- benchmark 复用后端已有图片证据识别与会话注入能力

## 3. 具体流程

1. benchmark 读取 case 的 `question_text`
2. benchmark 读取 case 的 `question_images`
3. 若无图片：
   - 构造 JSON `ChatRequest`
   - POST `/chat/completions`
4. 若有图片：
   - 构造 multipart 请求
   - `request` 字段放 JSON 字符串
   - `images` 字段重复上传每张图片
   - POST `/chat/completions-with-images`
5. 后端内部完成图片证据识别
6. 后端将识别结果注入 `context.image_evidences`
7. 会话继续按正常 `chat_completions` 链路执行

## 4. 运行时合同影响

- 图文 case 不再要求 fixture 预填非空 `request_context`
- `used_image_context` 在图文 case 中可由“走图片上传入口”满足
- 首轮运行记录需要区分：
  - `initial_message`
  - `initial_message_with_images`

## 5. 代码落点

- `benchmark/doc_search_bench/envs/doc_search/adapters.py`
  - 负责首轮分流与 multipart 组装
- `benchmark/doc_search_bench/envs/doc_search/preprocessors.py`
  - 负责图文 case 的图片上下文合同判定
- `benchmark/doc_search_bench/envs/doc_search/env.py`
  - 负责首轮请求类型记录
