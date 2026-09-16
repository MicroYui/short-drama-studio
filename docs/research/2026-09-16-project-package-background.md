# 大项目收集、导入与后台任务验收

2026-09-16。当前修改改善了等待、取消与结果找回；本次实测不能宣称总耗时加速。保留完整内容和制作记录，不通过删除历史缩小项目。

## 实现与边界

- 收集与导入使用 `studio_project_transfers` 独立 SQLite 记录（迁移 13），包含阶段、实际文件数/字节数、取消状态和最终结果。进度写入不重写 `ProjectDocument`。
- 新 HTTP 入口返回 202：`POST /studio/projects/{id}/package-jobs` 与 `POST /studio/projects/import-package-jobs`。`GET /studio/project-transfers` 按创建时间与 ID 分页；单条 `GET` 与 `POST .../{id}/cancel` 使用同一记录。旧同步接口保持兼容。
- 关闭页面、重新连接后可从记录列表找回进度和结果。同一工作区的本地收集/导入 IO 串行，等待中的任务也可取消；不影响 Provider 队列。
- 上传仍须先完成，之后才有后台导入记录。上传期间应保留页面；创建任务成功后可以关闭窗口。上传耗时与解包耗时分别可见，不把后台解包状态冒充上传进度。
- 媒体复制和解包每 1 MiB 检查取消；Trace 写入分块检查。较大的单条 JSON 读取/序列化先完成当前处理再检查。取消返回 `cancelling`，清理结束才记录 `cancelled`。
- 发布 ZIP 或提交导入前进入 `committing` 并关闭取消。导入的正文、Take 来源、视频编辑记录与“已完成”任务记录在同一 SQLite 事务内保存。提交前取消不留下导入内容；提交完成后返回已完成结果。
- 进度计数属于当前阶段。媒体/解包阶段有已知总量；Trace 未预先遍历和展开完整历史，因此提供实际已写文件数和未压缩字节数，不显示虚构百分比。最终阶段保留完整 IO 计数。
- 重启把未完成的本地收集/导入标为中断并清理其临时文件，不自动续跑。已完成的项目包和导入结果保持可找回。分页恢复覆盖超过 100 条历史任务。
- Trace 通过同一 writer 直接写入项目 ZIP；独立 Trace 导出仍使用该 writer。取消、失败不会留下可下载的半包。

实现位置：

- [任务生命周期](../../backend/short_drama/studio_project_transfers.py)
- [包收集与导入](../../backend/short_drama/studio_project_package.py)
- [实际 IO 进度](../../backend/short_drama/studio_package_progress.py)
- [共享 Trace writer](../../backend/short_drama/studio_trace_export.py)
- [模型合同](../../backend/short_drama/studio_package_models.py)
- [产品路由](../../backend/_routes/studio.py)

## 131 秒真实历史案例

原始数据库只读，通过 SQLite backup 制作副本。副本启动前确认无自动恢复状态的 Studio job，没有复制 Provider 配置。测量应用只挂产品路由，生命周期关闭，真实 Provider factory 由禁止调用的本地函数替代。既有导入副本和原数据库没有覆盖。

项目 `project-11b4f7a637c3`：21 镜、42 个时间线片段、7 个剧本版本、15 份生产文档共 161 个版本、12 个 Take。项目包包含 80 个本地媒体；完整 Trace 10,141 个条目、未压缩 2,381,174,070 字节。媒体未压缩 426,768,878 字节。

| 项目 | 旧同步入口，单次 | 新后台入口，单次 |
| --- | ---: | ---: |
| 收集请求返回 | 109.183 秒，完成时返回 | 0.336 秒，202/475 字节 |
| 收集从开始到完成 | 109.183 秒 | 124.951 秒 |
| 下载包 | 773,506,249 字节 | 773,710,689 字节 |
| 上传和导入请求返回 | 11.918 秒，完成时返回 | 1.742 秒，202/439 字节 |
| 上传、解包和导入到完成 | 11.918 秒 | 15.871 秒 |
| 收集期间状态查询 P50/P95 | 无后台状态入口 | 8 / 270.2 毫秒，161 个样本 |
| 导入期间状态查询 P50/P95 | 无后台状态入口 | 22.6 / 134 毫秒，22 个样本 |
| 状态响应最大体积 | 无后台状态入口 | 1,025 字节 |
| 整次 Python 进程峰值 RSS | 4,534,894,592 字节 | 2,667,085,824 字节 |

这不是隔离的速度对比实验：每种入口仅完成一次，机器同时承担其他验收；新流程还执行了状态轮询、一次取消及新旧包逐条比较。RSS 是各自整个 Python 进程的峰值，测量 client/server 在同一进程，不是 Electron/浏览器内存，也不能单凭该数字归因某项优化。总时长没有改善，状态查询最长一次为 3.099 秒；大 JSON 的处理仍可能造成短时等待。

真实取消在 Trace 已写入约 22.6 MB 时请求，3.319 秒后观察到清理完成（包含 HTTP 等待和轮询间隔），部分 ZIP 与临时文件均不存在。任务记录保留 `cancelled`，可以随后新建完整收集。

验证结果：

- 新旧包 10,141 个 Trace 文件名集合完全相等，每个文件解压后的内容逐字节相等。
- 导入后，路径归一化的 `script_versions`、`bible_versions`、`shots`、`assets`、`production_documents`、`editorial_timeline`、`reference_packs` 全部相等。
- 80 份媒体、全部 Trace 文件存在。导入没有创建 Studio job、Agent run 或 Provider run，也没有启动旧任务。
- 关闭发起请求的 HTTP 连接后，用新连接可以找回最终结果并下载项目包。
- 原始数据库的大小和纳秒修改时间没有变化；Provider factory 调用数为 0；测量服务器已停止。

原始记录与可复用脚本位于 [migration-131s](../../output/commercial-delivery-2026-09-16/migration-131s/)：`prepare.py`、`measure.py`、`measure-background.py`、`measurement.json`、`after-measurement.json`、`after-progress.jsonl`。旧 `import-profile` 保留；新结果位于 `after-import-profile`。脚本拒绝覆盖已有测量结果。首次后台测量的输出函数参数重名导致早退，相关材料单独保留在 `after-attempt-1-harness-error`，该次不算正式样本；其孤立任务已通过恢复逻辑清理。

## 视频编辑记录迁移

`video_edit_runs` 独立表使用迁移 14，提供单条保存、按 run/task 查询与项目列表，不嵌入项目正文。原始完整记录进入 `providers/video-edit/{id}.json`；项目包携带源文件、准备后的画面/声音、候选媒体，以及 `target_clip`、`audio_clip`、`baseline` 内的媒体路径。

导入时，成功/失败/取消/输出缺失记录保留终态，其他运行设为 `PAUSED`，清理可执行的远端任务 ID 与下一次轮询时间；保留真实远端返回内容和原 Trace。迁移后的 `source_files` 大小和修改时间重新读取，避免仅因换路径/复制时间而把候选标为来源已变。对应本地测试覆盖已完成候选、活动运行、嵌套路径和重新打开后的暂停状态，不代表新 Provider 画面质量验收。

## 定向验证

从 `backend` 运行现有 `.venv/bin/python -m pytest`，覆盖项目包、后台任务、独立记录与无 mocking 库检查。包含真实 FFmpeg 短片收集、搬走原目录后导入/编辑/导出；直接 writer 逐条等价；媒体/Trace/解包取消；提交完成记录失败时整份导入回滚；105 条中断任务恢复；完成结果保护；排队任务不开始第二份大型 IO 且能取消；进度写入不修改正文。定向 Pyright 通过。OpenAPI 由本地 Fake 应用生成，没有运行真实 Provider。

仍须区分：后端 HTTP/文件验收已完成；前端关闭与重开、原生文件取得，以及最终候选应用重打包由集成验收记录报告，不以本报告替代。
