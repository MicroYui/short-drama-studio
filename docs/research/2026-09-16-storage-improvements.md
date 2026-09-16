# 项目存储改进与验收

2026-09-16 已按用户确认的顺序完成实现与验收。保留 SQLite 管理项目内容、关系和运行状态，媒体保存为文件，JSON 用于复杂内容和项目交换。已有工作树改动保留；本轮没有调用模型或恢复已停止的制作任务。

## 实施范围

1. 跨集素材重新定位：项目、Take 与资产更新事件在同一事务提交，异常时一起回滚；复制和媒体检查留在事务外。
2. 共享资产：子集保存时只在共享内容实际变化后写根项目与发出资产事件。
3. 导出与修改审阅记录：按条存 SQLite，旧项目兼容迁移，记录完整保留，列表支持按需查询。审阅接受的正文修改和决定共同提交。
4. 使用已有作品的隔离副本测量保存成本，依据结果决定是否拆分每集时间线；不预先拆到逐字段、逐片段。

## 存储边界与已实现改动

| 数据 | 当前采用方式 |
| --- | --- |
| Schema 定义、字段校验、接口契约 | 保留在代码及生成的 OpenAPI 中，随代码更新 |
| 剧本、分镜、素材关系、本集时间线 | SQLite 中的 ProjectDocument；复杂内容仍使用 JSON 字段，不逐句、逐 clip 拆表 |
| 导出状态、修改审阅记录 | 独立 `studio_exports` / `studio_changes` 表，一条记录一行；常用身份、项目、状态、时间和运行范围建索引，其余内容为 JSON |
| 图片、视频、音频 | 文件系统，数据库保存具体引用、采用关系和元数据 |
| 项目交换、制作记录、交付包 | JSON / ZIP / Markdown 等文件；导入回同一项目存储，不建立第二份可编辑内容真相 |

跨集素材重新定位先在事务外复制并探测文件，再在一个 SQLite 事务中读取最新项目族，更新各集、Take、独立记录中的路径和资产事件。事务失败全部回滚，并清理未采用的新副本；复制期间新建的集也纳入提交。共享资产保存先比较归属归一化后的内容，未变化时跳过根项目写入及资产事件。

旧项目的嵌入记录通过第 12 次数据库迁移原子移入两张表。已有独立记录优先，不会被旧嵌入状态覆盖；重复初始化不会再次迁移。正式状态路径按条更新，普通 ProjectDocument 保存只导入尚不存在的旧记录。默认读取仍回填完整导出和审阅字段，保持旧 API、项目包、ArtifactRef 和 Trace 兼容；已解决的审阅和历史导出不会截断删除。

导出列表与新增审阅分页接口使用 `(created_at, id)` 游标处理相同创建时间；原审阅列表接口保留原数组响应。运行时待审查询直接使用状态及 run_id 索引，无待审项时无需载入整份正文。接受审阅在同一短事务中完成正文修改、Take 失效、事件和决定；拒绝只保存审阅记录。页面审阅入口也已脱离活动制作任务，旧项目或导入项目没有活动任务时仍可处理待审修改。

内容保存复用读取时已经建立的独立快照。存储内容仍等于原快照时，直接验证本次新序列化的内容，省去重复整份复制；存在并行变化时继续使用原三方合并和旧剧本保护。公共合并函数、数据校验和保存后的独立快照语义保持。

## 验收条件与结果

| 范围 | 验收结果 |
| --- | --- |
| 跨集重新定位 | 通过。真实 SQLite 触发器在最后的资产事件写入处中止，确认此前项目、Take 和独立记录路径全部回滚，无部分提交；新副本清理，重开后可重试 |
| 并行修改 | 通过。复制期间新增的集、已保存字幕和镜头修改保留；跨 Repository 的人工修改和旧剧本保护继续生效 |
| 共享资产 | 通过。SQL 触发器观察确认单集正文/时间线及归属归一化的无效变化不写根项目；实际共享变化仍写根项目并发事件 |
| 旧数据迁移 | 通过。ID、内容、状态完整保留，迁移中途失败保留旧 JSON；重复初始化、独立新终态优先、包导入导出和完整 Trace 均通过 |
| 独立记录 | 通过。SQL 跟踪确认导出状态查询/进度无需读正文；提案和拒绝不写正文；旧快照无法覆盖或删除记录；同时间分页无重复遗漏 |
| 审阅原子性 | 通过。场次和镜头接受时故意使决定写入失败，正文、Take、事件和决定一起回滚；拒绝及重复处理保留既有结果；被捕获的嵌套异常也使外层回滚 |
| 产品链路 | 通过。真实页面打开旧作品、直接保存场次、接受/拒绝审阅、播放和导出；后端重启、页面重开后当前内容及记录保持 |
| 测量 | 通过。同一现有案例、各 15 次正式采样与 2 次预热；另行核对新旧路径每种修改的完整内容及历史保留。结果与局限见下文 |

## 同案例性能结果

从既有原生校园案例数据库只读提取当前作品，原数据库没有打开写入或迁移；收尾时再次只读比较，原项目与提取输入完全一致。副本包含 21 个镜头、42 个剪辑片段；当前项目 JSON 为 14,156,805 字节。输入、改造前源码副本和原始样本保存在 `output/storage-improvements-2026-09-16/`。

| 操作 | 旧保存 P50 / P95（ms） | 新保存 P50 / P95（ms） |
| --- | ---: | ---: |
| 场次小修改 | 840.7 / 972.8 | 533.0 / 621.6 |
| 镜头小修改 | 894.7 / 1275.7 | 529.9 / 705.0 |
| 时间线小修改 | 878.9 / 1212.7 | 561.8 / 685.4 |
| 单条导出记录 | 868.5 / 935.5 | 1.30 / 3.30 |

前三项保存中位耗时下降约 36%–41%。最后一项是仓库方法微测量：旧路径读写完整项目，新路径只读写一条记录，不包含业务终态检查、事件追加、UI 或 FFmpeg 导出时间。完整项目读取仍约 158 ms，没有证明读取提速。保存仍有约半秒的仓库锁持有时间；共享开发机、暖缓存、单写入者、15 次样本的结果不作为跨机器性能承诺。

实测时间线仅占文档 0.76%；生产文档及其历史占 93.4%。诊断中主要成本是 Python 对完整文档的深复制、快照与验证，SQLite 执行耗时较小。因此本轮不拆时间线，也不删除历史；先消除已确认的重复处理。旧、新路径的独立内容保留断言全部通过：15 份生产文档、161 个生产文档版本、7 个剧本版本保持，完整历史正文及来源引用一致。进一步把历史正文移出当前编辑热路径，留待实际大项目使用成本证明必要后再推进。

详细方法、读取耗时、诊断、复跑命令见本地 `output/storage-improvements-2026-09-16/measurement-notes.md`；正式样本为 `before-baseline.json`、`after-baseline.json`，保留断言为 `before-preservation.json`、`after-preservation.json`。

## 实际页面与媒体验收

使用隔离数据库 `existing-case.sqlite3`，从旧校园案例提取当前作品及 12 个 Take 的来源，未恢复原制作任务。页面服务为本轮临时 5196 / 8196；原数据和原素材保持。

1. 迁移旧项目的 3 条导出记录，打开 3 场、21 镜头和 131 秒时间线。
2. 在剧本页面修改第三场标题并保存，刷新后保留。通过确定性工具准备两条审阅，不调用语言模型；在页面保留一条原文、接受另一条标题修改。
3. 数据库触发器观察确认：提出审阅和拒绝均为正文零写入；接受为一次正文写入，决定同时保存。没有活动任务的审阅入口也可使用。
4. 在产品页面以 720×1280 / 24 fps 导出本集。实际状态从画面渲染、声音混合到字幕渲染，最终 completed；整个导出过程正文零写入，时间线修订仍为 7。页面实际播放至约 10 秒，画面及字幕正常。
5. 字幕版、无字幕版均完整解码通过，时长 131.000 秒、音轨存在；首 3 秒与所选原声音频相关系数 0.9985，SRT 包含开场及结尾对白。此为本地交付正确性检查，不代表重新评估全片审美质量。
6. 停止并重启隔离后端，重开页面后标题和最新导出仍在；HTTP 分页共得到 4 条不同导出，审阅仍为 accepted / rejected，默认项目读取完整回填。验收完成后关闭临时页面和服务。

本地记录：`ui-prepared.json`、`ui-rejection.json`、`ui-accepted.json`、`ui-media-acceptance.json`、`reopened-acceptance.json`。导出文件位于 `output/storage-improvements-2026-09-16/media/project-11b4f7a637c3/editorial-exports/editorial-export-5343ecc6cd61/`，包含 `film-subtitled.mp4`、`film-clean.mp4` 和 `subtitles.srt`。

## 工程检查与交付边界

两组最终相关后端回归分别 60 项和 61 项通过，扣除重叠的禁止 mock 检查，共 120 个不同用例。第一组覆盖独立记录、共享资产、审阅、导出、Harness；第二组覆盖工作台、项目包、剪辑、语义修改、字幕合并、并发图片和媒体恢复。SQL 回滚使用实际 SQLite 触发器，媒体检查使用本地真实 FFmpeg，未通过 mock 模拟事务结果。

```sh
# 在 backend 目录执行
.venv/bin/python -m pytest tests/test_studio_record_storage.py tests/test_studio_changes.py tests/test_studio_export_lifecycle.py tests/test_studio_review_scope.py tests/test_repository_shared_assets.py tests/test_studio_harness.py tests/test_no_mock_usage.py -q
.venv/bin/python -m pytest tests/test_studio_workspace.py tests/test_studio_project_package.py tests/test_studio_shot_editorial.py tests/test_studio_editor_selection.py tests/test_studio_scene_semantic_revision.py tests/test_subtitle_merge_regressions.py tests/test_concurrent_image_versions.py tests/test_studio_media_recovery.py tests/test_no_mock_usage.py
.venv/bin/pyright

# 在仓库根目录执行
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

全后端 Pyright 为 0 errors；TypeScript、Vite 前端及 Electron main/preload 构建通过。OpenAPI 已由本地工具重新生成；生成后发现的可选列表类型不一致已修正并重验 TypeScript。检查输出保存在本地 `backend-storage-tests.txt`、`backend-adjacent-tests.txt`、`pyright.txt`、`typescript.txt`、`build.txt`。

本轮完成的是当前工作树的存储改进。未安装新依赖、调用付费 Provider、重跑独立整片稳定性实验或重打 macOS 安装包；原生历史包尚不包含本轮改动。已有工作区修改保持，未提交或推送 Git。
