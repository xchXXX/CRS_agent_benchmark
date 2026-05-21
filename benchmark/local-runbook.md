# CRS Benchmark 本地 Runbook

## 1. 目标

`benchmark/run.py --one-click` 负责把 benchmark 跑通，并把过去最常见的 OpenRouter `SSL EOF` 阻断收敛成可预检、可重试、可定位的问题。

当前 one-click 固化了以下稳定性策略：

1. 运行前先检查本地代理监听是否可连
2. 本地代理未就绪时自动启动 `C:\Vpn\SakuraCat\SakuraCat.exe`
3. 运行前先探测 `127.0.0.1:3306`
4. 本地 MySQL 未就绪时自动尝试启动仓库自带 `.local/mysql/.../mysqld.exe`
5. 运行前检查 `.local/benchmark_app_token.txt` 是否存在且非空
6. 运行前检查 backend `.env/.env.runtime` 中 OpenRouter 配置是否可用
7. 运行前先走代理直连 OpenRouter 做一次 TLS/HTTP 预检
8. 用户模拟阶段强制使用 fresh client，不复用长生命周期兼容客户端
9. 用户模拟阶段显式加长超时
10. 用户模拟阶段对 `SSL EOF`、超时、连接中断等传输类异常做自动重试
11. backend 与 benchmark 子进程统一继承同一套代理和模型环境
12. 每次 one-click 都有独立日志目录，避免后续运行覆盖前一次证据
13. image probe 使用轻量化固定提示，不再直接拿复杂真实 case 问题做探测
14. 正式 `/chat/completions-with-images` 请求也带传输级重试，不再因单次图文超时直接掉成 `error_http`
15. 每轮 benchmark 跑完后会自动导出一份 `round_case_review.html` 到对应 `benchmark/reports/runs/<run_id>/` 目录

## 2. 这次真正修了什么

上一版只处理了“不要复用失效客户端连接”，但仍有两个缺口：

1. 代理节点短抖时，`ask_user` 的 OpenRouter 调用第一次报 `UNEXPECTED_EOF_WHILE_READING` 就直接把整条 case 判成 `error_http`
2. one-click 没有在长跑前先验证“代理是否在监听”“OpenRouter 这条 TLS 链路是否已通”

这次修复后，链路变成：

1. `run.py --one-click` 启动前先做 `proxy listener probe`
2. 启动前再做 `mysql_prepare`，确认 `127.0.0.1:3306` 已监听；未监听则尝试拉起仓库自带 `mysqld`
3. 如本轮配置会用到 OpenRouter，再做 `openrouter transport preflight`
4. 只有预检通过才真正启动 backend / benchmark
5. image probe 调用图文接口时：
   - 固定只带 1 张图片
   - 使用轻量探测提示，目标只验证图文入口可用
   - 复用现有 backend 时也按 `probe_retries` 重试
   - 复用现有 backend 只要求“重试窗口内至少成功 1 次”
   - 新启动 backend 默认只要求 1 次成功；如果手动提高 `probe_min_successes`，才启用更严格累计成功判定
6. 用户模拟调用 OpenRouter 时：
   - fresh client
   - 默认超时 `600s`
   - 默认传输重试 `4` 次
   - 默认线性退避 `2s / 4s / 6s ...`
7. OpenRouter preflight 自身也会做短退避重试，不会因为单次瞬时 EOF 就直接误判失败
8. 如果 4 次传输重试后仍失败，才把问题记成真实运行错误，并把最后错误落到日志
9. 正式图文请求遇到 timeout / SSL EOF / remote close / 502/503/504 这类瞬时传输失败时，默认自动重试 `3` 次，按 `2s / 4s / 6s` 退避
10. 每个 round 成功结束后，`run.py` 会调用 `render_round_case_review_html.py`，把 `report.actual.json` 渲染成 `round_case_review.html`

## 3. 固定默认值

one-click 现在会给子进程显式注入：

- `http_proxy`
- `https_proxy`
- `HTTP_PROXY`
- `HTTPS_PROXY`
- `all_proxy`
- `ALL_PROXY`
- `no_proxy`
- `NO_PROXY`
- `BENCHMARK_USER_OPENAI_COMPAT_FRESH_CLIENT=1`
- `BENCHMARK_USER_TIMEOUT_SECONDS=600`
- `BENCHMARK_OPENROUTER_TIMEOUT_SECONDS=600`
- `BENCHMARK_OPENROUTER_RETRY_ATTEMPTS=4`
- `BENCHMARK_OPENROUTER_RETRY_BACKOFF_SECONDS=2`
- `BENCHMARK_OPENROUTER_PREFLIGHT_URL=https://openrouter.ai/api/v1/models`
- `BENCHMARK_IMAGE_REQUEST_RETRY_ATTEMPTS=3`
- `BENCHMARK_IMAGE_REQUEST_RETRY_BACKOFF_SECONDS=2`

默认代理地址仍是：

```text
http://127.0.0.1:7897
```

如果你的代理端口不是这个值，运行时显式传：

```powershell
python benchmark\run.py --one-click --proxy-url http://127.0.0.1:你的端口
```

## 4. 一键运行

单个 case 冒烟：

```powershell
python benchmark\run.py `
  --one-click `
  --split train `
  --suite real_world_wecom_train `
  --case-id real_train_0006 `
  --rounds 1 `
  --round-retries 0 `
  --max-attempts-per-case 1
```

完整 `train`：

```powershell
python benchmark\run.py `
  --one-click `
  --split train `
  --rounds 1 `
  --round-retries 0 `
  --max-attempts-per-case 1
```

## 5. 运行前检查

至少确认这几项：

1. SakuraCat 安装路径默认是 `C:\Vpn\SakuraCat\SakuraCat.exe`
2. 仓库自带 `.local/mysql/my.ini` 与 `basedir/bin/mysqld.exe` 存在且可启动
3. `.local/benchmark_app_token.txt` 已生成
4. backend `.env` / `.env.runtime` 中的 OpenRouter key 可用
5. 若你在国内环境，`openrouter.ai` 必须被代理接管，建议全局代理或强规则代理

其中 1-4 现在都会由 `run.py --one-click` 自动检查；代理未就绪时会先尝试自动启动 SakuraCat，MySQL 未监听时会先尝试启动仓库自带 `mysqld`。

## 6. 输出里新增要看的字段

one-click 输出里现在重点关注：

- `proxy_bootstrap`
- `proxy_probe`
- `mysql_prepare`
- `openrouter_preflight`
- `openrouter_preflight.attempts`
- `user_runtime_env`
- `token_status`
- `backend_openrouter_status`
- `one_click_run_id`
- `logs_dir`
- `run_summaries`
- `probe_logs[*].required_successes`

判读方式：

- `proxy_probe.ok=true`
  - 本地代理监听正常
- `mysql_prepare.ready=true`
  - 本地 MySQL 已就绪；如果 `attempted=true`，说明是 one-click 刚拉起来的
- `openrouter_preflight.ok=true`
  - 到 OpenRouter 的 TLS/HTTP 链路正常
- `openrouter_preflight.attempts`
  - preflight 每次尝试的 HTTP 状态或异常详情
- `probe_logs[*].attempts[*].success_count`
  - 当前 image probe 已累计成功次数
- `run_summaries[*].selected_attempt.execution_complete=true`
  - 这一轮执行完整，没有被 HTTP / runtime error 打断

## 7. 失败分流

### 7.1 `proxy_not_ready`

说明本地代理端口根本没监听，先修代理，不要继续跑 benchmark。

### 7.2 `openrouter_transport_not_ready`

说明代理虽然在，但到 OpenRouter 的 TLS/HTTP 链路没打通。优先排查：

1. 节点是否刚切换
2. 节点是否频繁断流
3. `openrouter.ai` 是否真的走了代理
4. 当前代理是否对终端进程生效

### 7.3 `mysql_not_ready`

说明 one-click 在进入 `_sync_backend_model_configs()` 前没有把本地 MySQL 准备好。优先排查：

1. `.local/mysql/my.ini` 是否存在
2. `my.ini` 里的 `basedir/bin/mysqld.exe` 是否存在
3. `127.0.0.1:3306` 是否已被别的进程占用或被防火墙拦截
4. `mysql_prepare.start_result` 里的 `reason` 是启动失败，还是“已启动但端口迟迟未 ready”

先看本次输出里的：

- `mysql_prepare.host`
- `mysql_prepare.port`
- `mysql_prepare.defaults_file`
- `mysql_prepare.mysqld_path`
- `mysql_prepare.errors`
- `mysql_prepare.start_result`

### 7.4 `error_http` 里仍出现 `OpenrouterException`

说明一次请求已经做完预检且做完多次传输重试，仍然失败。这时通常不是“偶发短抖”，而是：

1. 代理持续不稳定
2. 当前节点对 OpenRouter 质量差
3. 上游响应异常持续存在

先看本次运行输出里的：

- `proxy_probe`
- `openrouter_preflight`
- `run_summaries[*].selected_attempt.stderr`

再结合运行日志定位。

### 7.5 `error_http` 里出现 `image request exhausted ...`

说明正式图文请求已经做过传输级重试，但在整个重试窗口内仍然连续失败。优先排查：

1. 当前 backend 图文入口是否持续超时
2. 当前图片 case 是否触发异常慢查询
3. 上游服务是否返回连续 `502/503/504`
4. 当前 `--timeout-ms` 是否明显偏短

### 7.6 `existing_backend_probe_failed`

这类失败现在通常只会出现在：

1. 轻量 image probe 在整个重试窗口内一次都没成功
2. 现有 backend 虽然健康接口可用，但图文入口持续卡死

先看：

1. `probe_logs[0].required_successes`
2. `probe_logs[0].attempts[*].detail`
3. `probe_logs[0].attempts[*].success_count`

如果 `required_successes=1` 但所有 attempt 都是 `timed out`，说明问题已经不是“单次冷启动慢”，而是现有 backend 的图文链路本身不稳定。

## 8. 日志位置

backend 日志：

```text
benchmark/reports/one_click_logs/<one_click_run_id>/
```

benchmark 结果：

```text
benchmark/reports/runs/<run_id>/
```

每轮 HTML 审阅页：

```text
benchmark/reports/runs/<run_id>/round_case_review.html
```

优先排查顺序：

1. 看 one-click stdout 里的 `reason`
2. 看 `proxy_probe` / `mysql_prepare` / `openrouter_preflight`
3. 看 `.local/mysql/run/mysqld.stderr.log`
4. 看 `benchmark/reports/one_click_logs/<one_click_run_id>/*.stderr.log`
5. 看 `benchmark/reports/runs/<run_id>/runtime.log`
6. 看 `benchmark/reports/runs/<run_id>/report.score.json`

## 9. 结论边界

这套修复的目标不是对外部网络做“绝对不会失败”的承诺，而是把已知的短时代理抖动、TLS EOF、临时连接中断，从“直接阻断 benchmark”改成：

1. 先预检
2. 再自动重试
3. 最后明确归因

在正常代理节点下，这已经足够支持稳定的一键运行；如果代理本身持续断流，runbook 会尽早失败并把问题定位到代理链路，而不是让 benchmark 中途随机炸掉。
