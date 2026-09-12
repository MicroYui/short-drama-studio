# 第七批集中修复：保留交接、实际看图与 H3 来源

本批在[第六轮空项目](70-sixth-fresh-pre-video-end-to-end.md)自然结束、保存旧代码最终结果之后实施。原运行耗时 1 小时 48 分 28.81 秒，26 张图片、18 镜、6 组；最终两组来源过期，未通过完整视频前验收。问题归因和公开方法来源保留在[修复范围](71-sixth-run-consolidation-candidates.md)。记录已单独本地提交 `6c49921`。

用户最新要求为“修复以后先不用重新重跑了，先停止”。本批只修改代码、Skills 和说明，使用保存输入、临时项目库、确定性测试图片与 Fake client 做本地回放及相关回归。修复后不重启加载，不恢复旧项目，不创建新空项目，不调用真实语言、图片或 H3 Provider。测试中的 Service 重建用于检查持久化恢复，不是运行后端重启或真实制片复跑。

## 本批修复

| 问题 | 当前行为与边界 |
| --- | --- |
| 成功 READ 没有 artifact refs，导致正确 PARTIAL 整份被拒 | 同工单的已成功 READ 可引用为持久化观察，明确 `observation_only`，保持空产物引用。原判断、发现和下一步照实交给 Leader；未完成/失败读取、未知工具、无产物写入与真正完成合同仍校验。 |
| 真正结果合同失败后，Worker 原判断丢失 | 复用现有类型化协议反馈，保留完整被拒 decision 和具体错误；原 Worker 修正后真正保存成功才清反馈。被拒内容不是已完成事实，重复相同问题仍按已有交接方式交回 Leader。 |
| 大回执错误根目录导致反复分页 | 新完成回执直接指向 `/result`，参数另以 `/arguments` 定位。旧快照和空根仍可读；错误返回实际父路径、可用键或列表长度，不猜改路径。 |
| 草稿能被 read_current 读取，却不能用于 QA | 两处使用相同的当前草稿投影。检查记录精确草稿文档、版本和镜头来源；不会把草稿 revision 冒充正式 shot revision。旧观察在草稿更新或转正后仍保留原来源。 |
| Worker 产图后还需额外 Tool 和嵌套 QA 才能看图 | 成功图片操作的回执携带精确候选与必要对照，下一次已有 Worker 决策直接附图。masked EDIT 分开附 final/raw/source，crop 附实际裁图和原图；本地派生附精确来源。观察不自动审核、采用或完成素材。 |
| 需要重新观察精确原图或细节 | 新增只读 `image.view`，明确版本、用途及可选区域。EXIF 后按原像素裁框，再生成有实际尺寸说明的有界观察图；源文件不改。缺图明确列出，不换成 current；没有附件的旧记录不猜图，也不向其他 Worker 或 Leader 广播。 |
| Leader 收尾为已知类型工件额外发现工具 | 按本次已持久化、可解析的正式分镜、生成计划、路线和操作类型预载九个相关合同，复用原 schema 缓存。未按目标关键词路由，也不要求执行预载工具。 |
| 旧生成计划让 READ 连当前事实都读不到 | READ 返回当前完整组、来源文档版本和具体差异，原请求身份仍明确。投影只在内存中存在；编译、准备和执行仍检查所选组的实际来源。 |
| H3 保存立即失效、前镜过度失效 | 正文 cue 与 reference_plan 按精确媒体对应逻辑主体，避免把构图文件的容器资产算成新出镜主体。前镜依赖采用已写出的 outgoing continuity，缺必要终态字段时保留全文回退；实际终态、当前组、媒体变更仍触发失效。支持既有局部修改入口更新六个 continuity 字段。 |
| READY 是旧快照，当前来源变化不可见 | 路线 list/get 附只读 `source_checks` 和具体原因，保留原 READY 状态与历史路线。没有旧操作来源的路线明确仅检查当前结构化输入，不伪造历史来源比较。 |
| 已存提示词无法作为当前来源重新保存 | 在既有 `route.plan_h3` 接受 Agent 明确选择的精确路线、修订和组 decision。保持原 prompt、格式、媒体顺序，检查当前时窗、对白、引用和文件，保存新的当前来源操作及旧路线出处。此分支不调用语言，不自动选择过期提示词，不提交视频。 |
| H3 超长首稿、空间与邻镜方法不够具体 | 用完整四镜六段实例替换部分重复抽象说明，保留约 6000 字符软目标与 7000 硬上限，不截断事实。三个既有角色段落补充实际附图观察、当前机位参考、合法覆盖调整和受影响邻镜同步；不固定多视图、机位 plate 或 QA 循环。 |
| 全局纸张文案为空，显式候选文案仍被拒；资产封面选到坏图 | 允许显式 `content_state` 声明该候选完整文案，仍保留非空固定文案、物理载体、追加和像素合同。资产卡与详情共用默认预览选择器：无效 current 回退至已有可用母版或选用文件，所有旧图仍可明确查看；不改采用指针。默认预览因读取失败或 current 更新而变化时，右侧 Agent 的范围同步到实际显示版本，保留人工显式选择。 |

普通 100k–110k 文本保持正常使用。图片不以 Base64 当文字计数，实际附图每张预留 8192 tokens 的保守空间，Trace 明确这是 allowance、不是供应商用量或精确视觉计数。本地用 105k 文本加两图验证未触发整理；供应商上下文拒绝后的单次重试保持图片顺序和观察映射。

## 保存输入回放

版本管理中的必要实际输入保存在 `backend/tests/fixtures/`：

- `fresh6_saved_read_partial.json`：原 S13 成功读取及正确 PARTIAL，通过 Registry、Store 重建、Worker 与 Leader 输入逐项核对。
- `fresh6_draft_qa_source.json`：原草稿与失败 QA 参数，当前 draft 投影相同；后续版本与正式来源保持可区分。
- `fresh6_h3_source_contract.json`：实际第 4 组引用刷新与前镜终态，覆盖容器误判及真实内容变化仍失效的反例。
- `fresh6_worker_crop_observations.json`：原 S04 两次裁框和实际整数像素盒，通过产图 Tool、落盘、Store 重建到下一次 Worker 请求核对。测试图只用颜色标记验证第一次裁框遗漏、第二次包含，不模拟照片质量。
- `fresh6_paper_candidate_contracts.json`：原空全局 brief 的 t6 字版请求，与较晚的独立 UI 快照分别保留。测试实际写出 PNG，物理载体、共享 brief、current 和已登记选择不被改动。

图片附件还验证 final/raw/primary 顺序、精确被拒版本观察、缺图不回退、EXIF 原像素区域、原文件不变、请求中真实 `input_image` 顺序以及 schema 发现和预算重试时的映射。这里使用 Fake 语言响应检查产品调用链，不声称模型已经正确看懂图片。

另将第六轮保存的完整项目与 7 份历史操作放入临时 Repository，重建 Service 后经实际 Registry 读取路线：最新 6 组全部为 CURRENT；被后来 S12 正文修改替代的旧第 4 组仍为 STALE，原始记录和项目内容未被 READ 改写。明确选择旧第 4 组的 6385 字符提示词与原 9 图，在当前计划版本下本地保存新路线成功，提示词与有序媒体逐值不变，新来源为 CURRENT、旧操作不变。所有 Provider factory 均设置为调用即报错，视频任务与 Take 为零。记录与可重放脚本为 `output/quality-e2e-2026-09-12/observations/fresh6-h3-closed-replay.json` 和同目录 `fresh6_h3_closed_replay.py`；这证明保存合同修复，不改写第六轮真实验收未通过的结论。

## 本地验证

统一执行 39 个相关测试文件、672 个节点：669 passed、3 failed，313.96 秒。三处均为旧测试断言未同步已明确改变的合同：空路线列表增加 `source_checks`、已有 Shot 预载三个相关 schema、首次结果合同失败交回原 Worker。只修正对应测试，补足实际返回内容/原判断/当前 schema/任务归属验证后，三个节点共同复验 **3 passed，5.27 秒**；后端生产代码未为这些断言改变。保留这次统一运行原日志，不改写成首遍全通过。

H3 专项最终增加了互斥选择与当前实际对白变化的拒绝验证，**22 passed**；9 个相邻 H3/语言/修改文件 **185 passed**。这些与统一回归有重叠，不加总。原 READ/草稿 QA/预载专项的 33 项、文字图版/Skills 的 97 项、Worker 图像接线及相邻 49 项也仅作为各自范围的辅助检查，不重复累计。

30 个改动的生产 Python 文件经 Pyright strict 检查，0 errors / 0 warnings。使用本地 Python 导出 OpenAPI、现有 `openapi-typescript` 同步类型，TypeScript 检查通过。最终资产预览与详情 scope 同步 **13 项 Node 检查通过**，包括运行实际 effect 的回退、刷新、显式选择和关闭路径；最终 TypeScript 与前端/Electron Vite 构建通过。没有安装或升级依赖，没有运行真实桌面验收。

统一测试文件列表、节点列表及原日志分别保存在 `output/quality-e2e-2026-09-12/observations/seventh-consolidation-test-files.json`、`seventh-consolidation-test-nodes.json`、`seventh-consolidation-pytest.log`；生产检查清单与日志为 `seventh-consolidation-pyright-files.json`、`seventh-consolidation-pyright.log`。

复现命令均使用本地依赖（下列 Python 命令在 `backend` 执行）：

```sh
.venv/bin/python -m pytest tests/test_worker_visual_observations.py tests/test_asset_inspection.py tests/test_model_context_budget.py tests/test_language_provider.py tests/test_image_edit_result_tool.py tests/test_asset_crop_suggestion.py tests/test_tool_registry.py tests/test_tool_registry_coverage.py tests/test_saved_read_completion_feedback.py tests/test_draft_asset_inspection_context.py tests/test_leader_artifact_tool_preload.py tests/test_saved_completion_results.py tests/test_completion_receipts.py tests/test_production_manager.py tests/test_harness_tool_context.py tests/test_asset_review_scopes.py tests/test_paired_asset_review_context.py tests/test_asset_inspection_pair.py tests/test_storyboard_local_edits.py tests/test_h3_current_source_contract.py tests/test_h3_generation_unit_language.py tests/test_h3_group_operation.py tests/test_h3_generation_unit_prompt.py tests/test_h3_route_planner.py tests/test_h3_composition_references.py tests/test_h3_subject_reference_truth.py tests/test_h3_time_ranges.py tests/test_h3_output_options.py tests/test_short_drama_h3.py tests/test_h3_asset_preparation_handoff.py tests/test_semantic_change_executor.py tests/test_text_plate_empty_brief.py tests/test_text_plate_states.py tests/test_text_plate_layout.py tests/test_text_plate_append.py tests/test_text_plate_tool.py tests/test_production_skill_loader.py tests/test_production_method_skill_loader.py tests/test_no_mock_usage.py -q
.venv/bin/python -m pytest tests/test_tool_registry_coverage.py::test_read_adapters_use_the_real_service_without_bypassing_it tests/test_production_manager.py::test_language_planner_preloads_small_hot_set_and_discovers_long_tail tests/test_production_manager.py::test_specialist_contract_failure_returns_lossless_feedback_to_same_worker -q
.venv/bin/python export_openapi_schema.py
```

仓库根目录的前端命令为 `./node_modules/.bin/openapi-typescript frontend/generated/backend-openapi.json -o frontend/generated/backend-openapi.ts`、`./node_modules/.bin/tsc --noEmit`、`node --test scripts/test-studio-media-library.mjs`、`./node_modules/.bin/vite build`。源码和文档均检查 `git diff --check`。

## 停止点与仍待验证内容

本批完成后停止推进，运行中的后端仍是第六轮使用的旧代码；修复未加载到真实生产运行。没有新增视频任务或 Take，没有生成完整视频。

收尾只读核实：8021 监听仍为原 PID 35813；`studio-job-11fda417622f` 保持 completed，`agent-run-2e912068740048388fdfce51dac66b04` 保持 COMPLETED，更新时间仍为第六轮 19:49:52。核实未更新运行库，详见 `seventh-consolidation-stop-state.json`。

真实图像与视频质量、实际修复次数、总耗时、模型往返及无工程介入完成率均未复测。现有 outgoing continuity 是正常内容合同，非空字符串不能机械证明它与任意正文语义一致；Agent 修改设计时仍需同步实际依赖。提示词实例是否降低首稿超长率、直接附图是否减少重复 QA，都需要后续真实任务才能确认。

共享参考条目覆盖、父子 work_unit ID 冲突和正文锚点歧义的拒绝保留；本批没有为这些真实冲突增加自动覆盖或自动选择，也未扩做新的错误建议系统。第六轮最终结果依旧记为未通过，不用修复数量或本地测试替代成熟稳定产品的证明。
