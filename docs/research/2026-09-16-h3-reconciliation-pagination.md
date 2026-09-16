# H3 未知提交对账：有界分页与验收

日期：2026-09-16。范围：`SUBMIT_UNKNOWN` 的远端候选读取，不新增生成、自动绑定或自动重发行为。

原实现只读取供应商首批 50 条；兼容候选排在第 51 条之后时，调用方无法继续查找。现在 HTTP 和 Agent Tool 都可传入 `offset`、`page_size`、`max_pages`；默认从 0 开始，每页请求 50 条，一次最多 10 页。单页请求上限为 100 条，一次页数上限始终为 10。

## 返回结果与停止条件

响应保留 `tasks`，新增 `query` 描述本次范围：起始偏移、每页请求数、页数限制、已完成页数、返回条数、去重条数、停止原因，以及达到页数上限时可继续的 `next_offset`。

- `empty_page`：读到有效空页；只表示供应商在本次偏移返回空页，不证明从未提交。
- `repeated_page`：整页 ID 均已读取，分页没有新增任务，停止循环。
- `page_limit`：达到本次页数上限；下一次请求传入 `query.next_offset` 继续。
- 读取失败：返回错误，包含失败偏移与已完成范围，不返回伪造的空结果；本地未知提交状态保持不变。缺少 `items` 的 HTTP 200 响应也会作为无效响应报错。

偏移按供应商实际返回条数递增，短页继续查下一页；重复 ID 去重，有重叠但仍新增任务的页面继续读取。按本地任务筛选候选时，保留既有模型、分辨率、时长、比例、任务类型和输入模态兼容检查。候选发现本身不证明身份，不自动绑定。明确提供 `provider_task_id` 的对账仍直接查询该 ID，再做兼容检查；不依赖列表可见性。

Agent Tool `route.list_h3_reconciliation` 的 schema 版本为 3。HTTP 为 `GET /api/short-drama/providers/minimax-h3/reconciliation-candidates`。项目服务候选方法和 Tool 均透传分页参数；HTTP/Tool schema 拒绝超出界限的参数。

## 供应商合同依据

2026-09-16 查阅供应商公开的 [MiniMax 任务 API 指南](https://github.com/AceDataCloud/MiniMaxAPI/blob/main/docs/tasks.md)。指南将 `POST /minimax/tasks` 的 `retrieve_batch` 定义为读取操作，接受 `limit`、`offset`，响应包含 `items`、`total`；单项读取使用 `retrieve` 与 `id`。本次沿用该合同，没有根据旧资料中的时间窗口假设裁掉候选。

## 本地验证

所有测试使用临时数据库、状态 Fake 或本地 HTTP transport，不运行真实生成。新测试验证第 51、151 条兼容候选可被发现；第 551 条在第一次达到 500 条范围上限后，由 Tool 的下一次请求继续找到。也覆盖空页、短页、重复页、重叠页、第一页与中间页错误、HTTP 参数边界、错误响应以及明确 ID 的直接对账。

查询前后逐字段比较本地任务，确认仍为 `SUBMIT_UNKNOWN`，没有额外 submit 或 cancel。已有取消、输入失效、输出几何与 H3 任务回归也通过。

在 `backend` 下执行：

```sh
.venv/bin/python -m pytest tests/test_h3_reconciliation_pagination.py tests/test_minimax_h3_client.py -q
# 23 passed
.venv/bin/python -m pytest tests/test_short_drama_h3.py tests/test_h3_generation_unit_worker.py tests/test_h3_cancellation.py tests/test_h3_output_geometry.py tests/test_no_mock_usage.py -q
# 56 passed
.venv/bin/pyright short_drama/h3_worker.py short_drama/h3_models.py short_drama/providers/minimax_h3_client.py short_drama/tools/route/__init__.py _routes/short_drama.py tests/test_h3_reconciliation_pagination.py tests/test_minimax_h3_client.py tests/test_short_drama_h3.py tests/test_h3_generation_unit_worker.py
# 0 errors, 0 warnings
```

最后将分页错误包装改为基础 `H3ClientError`，避免将明确拒绝也描述成暂时错误；再次运行分页测试，10 项通过。相关改动的 `git diff --check` 通过。以上是定向验收，不是全量后端或真实生成稳定性结论。

## 两页真实只读检查

使用已授权的本地验收配置，仅运行两次 `retrieve_batch`。收据时间为 2026-09-16 03:16:08 UTC。

| limit | offset | HTTP | 耗时 | 有效任务数 |
| --- | --- | --- | --- | --- |
| 1 | 0 | 200 | 867.23 ms | 0 |
| 1 | 1 | 200 | 124.81 ms | 0 |

请求成功且响应可解析，但两页均为空。因此线上只确认了这两个参数请求被接受，尚未用真实的不同任务验证偏移效果。没有继续扫描账号，也没有生成、取消、删除远端任务或更新本地任务状态。脚本与收据不包含凭据、任务 ID、完整响应或媒体 URL。

- [只读检查脚本](../../output/mature-product-acceptance/h3-reconciliation/read_only_listing.py)
- [实际收据](../../output/mature-product-acceptance/h3-reconciliation/live-listing-receipt.json)

复用该脚本需要明确授权访问相应账号；本次授权只执行上面的两次读取。脚本使用已存在的配置文件，没有改写该文件。

## 局限

供应商列表并非稳定快照；分页期间新增、删除或排序变化仍可能导致重复或遗漏。当前按 ID 去重、遇无新增页面停止，并说明实际范围；没有把有界列表或空结果当成远端完整历史证明。已知 ID 的直接查询仍是独立恢复入口。真实账号本次返回空列表，线上偏移选项的实际位移效果仍未证实，不能将本地 Fake 的覆盖结论写成供应商线上全量验证。
