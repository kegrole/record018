# 表单自动化录入 (rpa.formfill)

针对本地模拟门户的两步表单自动录入程序。只用 Python 标准库，不访问外部网络。

## 运行

```bash
# 跑测试
python -m unittest tests.test_formfill -v   # 或在 tests/ 目录下 python -m unittest test_formfill -v

# demo：启动门户、登录、录完 demo/records.jsonl、打印摘要
python -m rpa.formfill --site mock_portal --records demo/records.jsonl --db out/rpa.db
```

输出摘要一行：`summary: submitted=N success=N skipped=N failed=N requests=N`。

## 结构

- `mock_portal/` — 本地模拟门户。登录页下发会话 cookie；两步录入表单（第一步单号+金额，第二步备注+提交）；提交成功跳结果页。配置在 `portal.json`（账号、金额上下限、备注上限、已有种子记录），验收时可按同样规则重新生成。
- `rpa/session.py` — 带 cookiejar 的 HTTP 客户端，登录后每个请求自动携带会话 cookie，并统计请求次数。
- `rpa/form.py` — 用 html.parser 抽取表单、隐藏字段（CSRF token）、字段属性（min/max/maxlength）与错误回显（哪个字段、哪个值、错误原文）。
- `rpa/rules.py` — 错误分类与修正规则。
- `rpa/store.py` — sqlite：`records`（单号唯一）与 `outcomes`（record_id、status、attempts、last_error）。
- `rpa/runner.py` — 编排：登录、逐条走两步表单、错误处理、写库、打印摘要。
- `rpa/formfill.py` / `rpa/__main__.py` — 命令行入口。

## 会话与 CSRF

- 登录：从站点配置 `portal.json` 的 `users` 读取账号，POST /login 拿到 `portal_session` cookie，存入 cookiejar，之后所有请求自动携带；未登录访问表单页会被 403 拒绝。
- CSRF：每个表单页含隐藏 `csrf_token`，一次性使用。POST 前先 GET 表单取 token；若 POST 因 token 失效被 403 拒绝，错误页里带有新 token，客户端解析后自动重试（最多 3 次）。字段错误重试时同样使用错误页里的新 token。

## 错误分类规则

服务端字段级错误以 `<p class="error" data-field=... data-value=...>` 回显，按错误原文分类：

- **单号重复** → 视为已存在，标记 `skipped`，不再重试（attempts=1）。
- **金额超出范围** → 按表单 amount 字段的 min/max 属性把金额钳制进合法区间，重试一次（attempts≥2）。
- **备注过长** → 按 remark 字段的 maxlength 属性截断到门户上限，重新提交。
- 其他错误 → 标记 `failed`，记录错误原文。单条记录最多 5 轮，防止死循环。

## 幂等与断点续录

- 清单按单号幂等：同一单号在清单里出现多次（或多次跑同一份清单）只提交一次，不会产生第二条记录，也不会重复写 outcome。demo 清单共 12 行，其中 1 行是同一正常记录的重复，用于验证该语义。
- 门户侧按单号去重（重复提交返回"单号重复"）；本地 `records.order_no` 有 UNIQUE 约束，`INSERT OR IGNORE`，同一条记录重复提交不会产生第二条。
- 每条记录处理完后，record 行与 outcome 行在同一个 sqlite 事务里提交：崩溃要么都落盘要么都不落盘，不会出现"门户已录、本地没记"的窗口。
- `outcomes` 表即进度：重启时跳过已有终态（success/skipped）的单号，从断点继续；整份清单重跑不会新增 records 行。
