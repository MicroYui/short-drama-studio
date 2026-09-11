# 并发图片任务与本地派生的版本编号

状态：2026-09-12 已实现并完成本地回归，于 06:55:53 随第九轮修复加载并恢复原调试 Job，见[实际制作记录](03-real-production-record.md)。本次代码修复与回放没有写入运行数据库或调用真实图片/视频 Provider；后续运行另记，不是新空项目验收，也不作为生成质量或提速结论。

## 实际问题与根因

只读打开 `output/quality-e2e-2026-09-12/runtime/short-drama.sqlite3`（SQLite URI `mode=ro`），确认项目 `project-5b8286dc63e6` 的场景资产 `asset-4024e0462bfc` 有三份不同的已落盘图片，真实 `AssetVersion.version` 均为 3：

| AssetVersion ID | ImageTask ID | 实际完成时间（UTC） |
| --- | --- | --- |
| `asset-version-a8b4587fe910` | `image-task-1e88dd287896` | 2026-09-11 22:24:14.016 |
| `asset-version-08a2aed2206b` | `image-task-552fcfb5601a` | 2026-09-11 22:24:18.643 |
| `asset-version-40e289075a43` | `image-task-b5f301505e08` | 2026-09-11 22:24:22.769 |

各文件名为 `v3-<各自 AssetVersion ID>.png`。UUID 和路径不同，因此不是图片互相覆盖；错误在对外显示和引用的版本数字重复。历史数据、文件、Trace 不改写。

原 `AssetWorkbench.enqueue_image_task` 在短锁内根据已保存 `asset.versions` 求 `max + 1`，随后只保存任务。任务等待或执行期间还没有新增 AssetVersion，因此三个任务都取得 3。`_finalize_image_version` 原样使用任务预先保存的数字，最终把三份记录都登记为 3。仅给完成步骤加锁不足以解决“排队时读到相同已完成集合”的问题。

裁切、参考图集、文字图版也在慢处理前取得数字；原代码用处理后的 `next_version == 原数字` 拒绝并发变化。引入任务预留后，单纯新增一个排队任务就会改变该值，却没有改变本地派生的源图。这需要同步改到最终保存时取号。声线上传的音频归一化同样先慢处理、后保存，使用同一资产版本空间，一并接通。

## 采用的工程依据与实现

[Python RLock 官方文档](https://docs.python.org/3/library/threading.html#rlock-objects)说明同一线程可重入、其他线程在释放后继续；[SQLite 隔离说明](https://sqlite.org/isolation.html)说明数据库只允许一个写者，但不同连接的多个读写步骤不会因此自动变成一个业务上的分配操作。这里采用现有服务共享锁内“读取已占用号并保存预留/版本”的普通工程做法，不把多个慢 Provider 请求放进锁。

本产品 `ShortDramaService` 将同一个 `self._lock` 传给 `AssetWorkbench`。本次保持该单服务实例的并发约定，没有新增跨进程调度或独立状态系统。

- 保留 `_next_version_number(asset)` 调用接口；最大值同时包含该 `project_id + asset_id` 下已保存 AssetVersion 和持久化 ImageTask 的 `output_version_number`。不同资产互不影响。
- 排队操作在既有锁内取号并保存 Task，Task 本身就是预留记录。失败、取消等终态预留也不复用；允许空号，避免旧任务恢复或重试夺取已经暴露过的编号。重启后从同一任务记录恢复，不依赖进程内计数器。
- 正常任务完成保留原预留数字，UUID、最终路径和成功 Tool 回执一致。Tool 只在任务 `SUCCEEDED` 后提供精确 AssetVersion 引用，排队阶段仍交接 Task。
- 兼容旧的冲突在途任务：若本次尚未登记的输出数字已被实际版本使用，在最终短锁内为这份新结果换一个未占用号，并同步新文件路径和 Task；按同一 `output_version_id` 已登记的结果恢复时，复用其原版本，不重复 append。已经存在的三份历史 v3 记录及其文件不改名、不重编号。
- crop、reference sheet、text plate 只在准备阶段取得 UUID/临时文件名；完成源输入检查后，在最终保存短锁内取号并定稿文件名。声线归一化先产临时 WAV，最终保存同样取号。图示与 mask 的两个辅助版本沿用父任务已实现的最终锁内逐个取号，因此也避开在途任务。
- 保留原来对精确源图、源文件状态、当前版本和文字内容的检查。移除的只是“数字变化等于素材内容变化”的条件；并发用户修改不会因此被无条件覆盖。

实际编码仍保留在锁外。旧的本地导入和资产库复制本就于保存锁内取号，自动使用同一预留规则；没有另改其 IO 架构。

横向代码复查另外发现两个直接 `max + 1` 的入口：`media_frame_asset.extract_take_frame` 的最终帧登记，以及 `change_executor.prepare_semantic_change` 的资产文字版本。两者本身没有触发本次三张真实图片故障，但会占用已排队图片的预留号。现在前者在现有 `mutation_lock` 内调用同一公开只读取号方法；后者接受可选取号 callback，由 `ShortDramaService.apply_semantic_change` 在现有共享锁内传入。纯函数离线使用的默认行为不变，没有新增计数器或锁。

## 本地验证与边界

`backend/tests/test_concurrent_image_versions.py` 新增 9 项，另在原取帧测试中新增 1 项：

- 三个线程同时排队，同一 Fake Provider 的三个请求必须都进入后才允许回包，证明没有用串行请求换取唯一号；请求进行时仍能导入本地图片。核对最终数字、路径、UUID 和其他资产独立编号。
- 取消后的预留跨 Service 重建保留，不复用已公开数字。
- 复现两个旧在途任务同号：最终新结果改号、真实 Tool 引用同步；再重建 Service 走既有输出恢复，未再次调用 Provider，也未重复登记。
- crop、reference sheet、text plate 和 voice 四种真实本地处理分别暂停在慢步骤；期间排入新任务，完成后取得避开预留的新号，源文件不变，随后原任务仍使用预留号。图片处理使用实际 Pillow，声线使用实际本地 FFmpeg；只是用事件控制执行顺序。
- 人工构造旧重复版本数据后继续生成，确认旧版本 JSON 和旧文件字节保持原样。
- 实际 Service 语义修改与实际本地视频取帧分别验证：存在排队图片预留时，新资产版本避开该数字，排队任务保持原值，不调用图片 Provider。

执行：

```bash
cd backend
.venv/bin/python -m pytest tests/test_concurrent_image_versions.py tests/test_short_drama_assets.py tests/test_asset_reference_sheets.py tests/test_text_plate_tool.py tests/test_text_plate_append.py tests/test_asset_image_regions.py tests/test_image_edit_guides.py -q
.venv/bin/python -m pytest tests/test_concurrent_image_versions.py tests/test_take_frame_asset.py tests/test_semantic_change_executor.py -q
.venv/bin/pyright short_drama/asset_workbench.py short_drama/media_frame_asset.py short_drama/change_executor.py short_drama/service.py
```

结果：主链 8 项与既有资产回归 **82 passed / 11.61s**；补齐两个旁路后的新增/取帧/语义修改集合 **18 passed / 4.24s**（两组包含重复节点，不相加作为唯一测试数）。4 个源码文件 Pyright **0 errors / 0 warnings**，所改文件 `git diff --check` 通过。首次专测的两个失败分别是测试写错恢复 API 名和视图枚举，修正 fixture 后通过，没有据此修改产品合同。

这是确定性版本分配与本地恢复验证。没有新增真实 Provider 调用，不证明供应商图片质量、长期并发吞吐或整片稳定性。既有历史重复号保留可读，以精确 AssetVersion ID 识别素材。
