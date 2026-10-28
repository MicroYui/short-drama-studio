# 01A 工具全目录：Agent 究竟能调用什么

[返回 Multi-Agent 主章](01-multi-agent-runtime.md) · [并行推演](01b-multi-agent-walkthrough.md) · [学习地图](README.md)

本表于 2026-10-22 从当前检出 `e76aaa3` 的 ToolSpec、注册辅助函数、循环注册及 Service/ProductionManager 装配路径静态核对。它描述默认代码注册，不声称当前运行实例、凭证和全部外部服务都已经就绪。未导入后端或发起真实 Provider 请求。

## 1. 数量必须带口径

- `build_default_tool_registry` 的默认业务注册为 **142 个**；ProductionManager 再补 `agent.get_tool_attempt`、`agent.read_context` 和 `artifact.retry_asset_plan_attempt`，合计 **145 个**。
- 默认 145 个中，READ **39**、WRITE **73**、EXTERNAL **33**，共 **14 类**。
- 执行目标 `REAL_H3` 隐藏 6 个 Mock 专用工具，目录为 **139 个**；`MOCK` 隐藏 5 个 H3 专用工具，为 **140 个**；未指定目标隐藏两组，为 **134 个**。这是目录过滤，执行时仍需业务校验。
- 另有可选 `agent.delegate` 扩展 ToolSpec；默认 Service 没传 `agent_delegator`，不计入上述 145 个。默认委派通过 Leader 的 `DELEGATION` PlanStep。
- HTTP 路由、内部 Python 方法、某个 Skill 提到的软件，不自动算成可调用工具。这里也没有通用 Shell、浏览器点击、任意 Python 执行或联网搜索工具。

装配证据：[defaults.py](../../backend/short_drama/tools/defaults.py)、[service.py](../../backend/short_drama/service.py)、[ProductionManager](../../backend/short_drama/production_manager.py)。

## 2. 如何读表

**R/W/E** 是源码的 `ToolEffect.READ/WRITE/EXTERNAL`；“幂等”列是注册元数据。READ 也可能进行本地解码或 ASR；EXTERNAL 可能包含本地状态保存；WRITE 也可能包含下载或重型导出。因此该分类不是网络沙箱、耗时上界或完整副作用证明。`idempotent=True` 也不等于远端 exactly-once，不允许对未知提交盲目重发。

输入和输出列列出真实 Pydantic 模型名，点击工具 ID 可打开注册文件继续查字段。实际调用必须从当前 Registry 取得完整 JSON schema，本文的中文用途不能替代参数合同。返回还会包在 `ToolResult` 中，含 status、artifact_refs、receipt、error 和可能的 visual_attachments。

| 类别 | 默认数量 | 主要作用 |
| --- | ---: | --- |
| `project` | 6 | 项目与输出槽 |
| `planning` | 3 | 剧本规划任务 |
| `script` | 3 | 剧本正文 |
| `shot` | 21 | 分镜与镜头 |
| `artifact` | 31 | 资产、制作文档与跨 Agent 工件 |
| `image` | 15 | 图像生成与本地处理 |
| `route` | 20 | 生成组与 H3 路线 |
| `task` | 9 | 任务生命周期与 ComfyUI |
| `video` | 10 | 视频候选与观察 |
| `audio` | 7 | 声音与字幕 |
| `timeline` | 8 | 剪辑与导出 |
| `qa` | 7 | 专业观察与检查 |
| `change` | 3 | 定向修改与审阅 |
| `agent` | 2 | 上下文与调用证据 |

## 3. 项目与输出槽（project）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [project.bind_output_slot](../../backend/short_drama/tools/project/__init__.py) | W / 是 | 将精确工件绑定到语义输出槽；expected_revision 防止覆盖同槽新交接。 | `OutputSlotBindInput` → `OutputSlotMutationResponse` |
| [project.create](../../backend/short_drama/tools/project/__init__.py) | E / 否 | 按本 run 预分配项目 ID 创建项目；只用于项目还不存在的启动路径。 | `ProjectCreateInput` → `ProjectAggregate` |
| [project.get](../../backend/short_drama/tools/project/__init__.py) | R / 是 | 读项目聚合；模型投影有裁减，局部工作仍应使用更小读取接口。 | `ProjectGetInput` → `ProjectAggregate` |
| [project.get_output_slot](../../backend/short_drama/tools/project/__init__.py) | R / 是 | 读取某用途当前指向、历史绑定和失效原因。 | `OutputSlotGetInput` → `OutputSlotReadResponse` |
| [project.list](../../backend/short_drama/tools/project/__init__.py) | R / 是 | 列权威存储中的项目；存在此接口不代表支持任意跨项目写入。 | `EmptyToolInput` → `ProjectListResponse` |
| [project.list_output_slots](../../backend/short_drama/tools/project/__init__.py) | R / 是 | 列本项目语义输出槽，不复制底层分镜或生成计划正文。 | `OutputSlotListInput` → `OutputSlotListResponse` |

## 4. 剧本规划任务（planning）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [planning.advance](../../backend/short_drama/tools/planning/__init__.py) | E / 否 | 推进所属 AgentRun 的 Content Planning Job 一个有限单元；保留游标。 | `PlanningAdvanceInput` → `PlanningRunResponse` |
| [planning.resolve_story_verification](../../backend/short_drama/tools/planning/__init__.py) | E / 否 | 由所属 Leader 根据精确报告决定下一条修订路线；不是固定 Critic 循环。 | `PlanningResolveStoryVerificationInput` → `PlanningRunResponse` |
| [planning.start_script_revision](../../backend/short_drama/tools/planning/__init__.py) | E / 否 | 从当前精确剧本版本启动可恢复修订，区分用户反馈与制作约束证据。 | `PlanningStartScriptRevisionInput` → `PlanningRunResponse` |

## 5. 剧本正文（script）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [script.plan_scenes](../../backend/short_drama/tools/studio/__init__.py) | W / 是 | 保存 Agent 决定的场次安排并返回真实 scene_id；不填入尚未完成的正文。 | `SceneOutlineInput` → `CurrentReadOutput` |
| [script.revise_lines](../../backend/short_drama/tools/script/__init__.py) | W / 否 | 按选定台词创建剧本修订。 | `ScriptReviseLinesInput` → `ProjectAggregate` |
| [script.save_scene](../../backend/short_drama/tools/studio/__init__.py) | W / 否 | 按完整场次保存正文和逐字存在的台词，保护 expected_text；可带明确跨场关联更新。 | `SceneWriteInput` → `SceneWriteOutput` |

## 6. 分镜与镜头（shot）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [shot.append_storyboard_group](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 逐组保存完整镜头并立即展示，含真实文字锚点、时段和引用。 | `StoryboardDraftAppendInput` → `StoryboardDraftMutationResponse` |
| [shot.append_storyboard_groups](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 一次提交多个已完成组；每组独立持久化，不是整批原子成功。 | `StoryboardDraftGroupsAppendInput` → `StoryboardDraftGroupsMutationResponse` |
| [shot.append_storyboard_ledger_segment](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 可选的时长思考记录，不是所有分镜的前置门槛。 | `StoryboardDraftLedgerSegmentAppendInput` → `StoryboardDraftMutationResponse` |
| [shot.append_storyboard_outline_segment](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 可选保存分镜总纲片段，不强制先确定全片再写镜头。 | `StoryboardDraftOutlineSegmentAppendInput` → `StoryboardDraftMutationResponse` |
| [shot.apply_director_repair](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 应用或记录已作出的 typed 导演修复决定。 | `ShotDirectorRepairInput` → `ProjectAggregate` |
| [shot.begin_storyboard_draft](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 开始可增量写入的分镜草稿，可直接按组追加。 | `StoryboardDraftBeginInput` → `StoryboardDraftMutationResponse` |
| [shot.compile_keyframe_brief](../../backend/short_drama/tools/shot/__init__.py) | R / 是 | 编译指定镜头的空间、起始或结束关键帧需求。 | `ShotKeyframeBriefCompileInput` → `KeyframeBriefCompileResponse` |
| [shot.complete_storyboard_ledger](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 结束可选时长笔记；不要求所有工作必须经过该阶段。 | `StoryboardDraftLedgerCompleteInput` → `StoryboardDraftMutationResponse` |
| [shot.complete_storyboard_outline](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 可选完成总纲及其节奏观察，不形成固定审批。 | `StoryboardDraftOutlineCompleteInput` → `StoryboardDraftMutationResponse` |
| [shot.create_storyboard](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 一次保存已经完成的结构化分镜，适合确有完整结果时。 | `StoryboardCreateInput` → `StoryboardMutationSummaryResponse` |
| [shot.director_advice](../../backend/short_drama/tools/shot/__init__.py) | E / 否 | 调用当前体裁导演方法提供局部镜头建议。 | `ShotDirectorAdviceInput` → `DirectorAdviceResponse` |
| [shot.finalize_storyboard](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 将当前镜头组装配为正式分镜，做实际结构、引用和覆盖检查。 | `StoryboardDraftFinalizeInput` → `StoryboardMutationSummaryResponse` |
| [shot.get_storyboard](../../backend/short_drama/tools/shot/__init__.py) | R / 是 | 读取指定当前或历史分镜正文一次，不重复带全部版本。 | `StoryboardGetInput` → `StoryboardCompactReadResponse` |
| [shot.patch_storyboard](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 局部修改、插入、删除、拆合或重分组，保留无关镜头与人工修改。 | `StoryboardPatchInput` → `StoryboardMutationSummaryResponse` |
| [shot.preview_reference_plan](../../backend/short_drama/tools/shot/__init__.py) | R / 是 | 预览传统独立 Shot 参考解析，不修改项目。 | `ShotReferencePlanPreviewInput` → `ReferencePlanPreviewResponse` |
| [shot.record_storyboard_draft_verification](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 保存具体分镜检查报告；不等于用户审批或创建媒体。 | `StoryboardDraftVerificationInput` → `StoryboardDraftMutationResponse` |
| [shot.replace_storyboard](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 在确需全片结构重写时整体替换；局部修订优先 patch。 | `StoryboardReplaceInput` → `StoryboardMutationSummaryResponse` |
| [shot.replace_storyboard_draft_group](../../backend/short_drama/tools/shot/__init__.py) | W / 否 | 替换一个草稿组，保留其他组，基于实际当前版本保护修改。 | `StoryboardDraftGroupReplaceInput` → `StoryboardDraftMutationResponse` |
| [shot.run_director_loop](../../backend/short_drama/tools/shot/__init__.py) | E / 否 | 复合执行建议与可用修复；有该工具不表示每个 Worker 都跑 Critic 循环。 | `ShotDirectorAdviceInput` → `DirectorLoopResponse` |
| [shot.save_current](../../backend/short_drama/tools/studio/__init__.py) | W / 否 | 保存当前镜头正文、时间窗及 GENERATION/EDITORIAL 精确引用，返回实际保存回执。 | `ShotWriteInput` → `ShotWriteOutput` |
| [shot.update_reference_plan](../../backend/short_drama/tools/shot/__init__.py) | W / 是 | 更新传统独立 Shot 的参考/动作计划；已提交草稿引用用 save_current。 | `ShotReferencePlanUpdateInput` → `ShotReferencePlanMutationResponse` |

## 7. 资产、制作文档与跨 Agent 工件（artifact）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [artifact.add_reference_entries](../../backend/short_drama/tools/artifact/__init__.py) | W / 是 | 批量保存已选图片的用途与关系；CURRENT 且文件可用的 DRAFT/APPROVED 均可，不自动审核。 | `ReferencePackEntriesAddInput` → `ReferencePackEntriesMutationResponse` |
| [artifact.add_reference_entry](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 保存单个引用条目及用途；已有多项明确选择可用批量接口。 | `ReferencePackEntryAddInput` → `ReferencePackEntryMutationResponse` |
| [artifact.create_asset](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 创建人物、场景、道具或通用参考对象及初始文本版本；不生成图片。 | `AssetCreateInput` → `AssetMutationResponse` |
| [artifact.create_asset_plan](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 创建可分批扩展的资产工作计划，保存真实来源、稳定工作项和依赖。 | `AssetPlanCreateInput` → `AssetPlanMutationSummaryResponse` |
| [artifact.create_assets](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 一次保存多项已确定资产，返回真实 ID 和引用；不等于创建 Bible 实体或生成媒体。 | `AssetsCreateInput` → `AssetBatchMutationResponse` |
| [artifact.create_production_document](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 创建跨 Agent 使用的结构化制作文档；返回精确版本回执，正文按需读取。 | `ProductionDocumentCreateInput` → `ProductionArtifactMutationSummaryResponse` |
| [artifact.create_production_spec](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 建立本次制作约束并同步相关项目属性；不生成媒体，也不代替项目 Bible。 | `ProductionSpecCreateInput` → `ProductionArtifactMutationSummaryResponse` |
| [artifact.get_asset_plan](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 读指定当前或历史资产计划正文；需要局部工作时优先 index 或 work_context。 | `AssetPlanGetInput` → `AssetPlanCompactReadResponse` |
| [artifact.get_asset_plan_index](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 读稳定 item/work-item ID、执行状态、依赖和关联媒体等目录，省去全量 brief/prompt。 | `AssetPlanIndexGetInput` → `AssetPlanIndexResponse` |
| [artifact.get_asset_planning_context](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 为资产规划读取去重的当前剧本、Bible、制作约束、分镜及源资料。 | `AssetPlanningContextGetInput` → `AssetPlanningContextResponse` |
| [artifact.get_asset_work_context](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 按精确 scope 读取一个资产或工作项所需切片；包含必要父项和实际依赖。 | `AssetWorkContextGetInput` → `AssetWorkContextResponse` |
| [artifact.get_production_document](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 读取一份制作文档选定版本；不把全部历史再次放入上下文。 | `ProductionDocumentGetInput` → `ProductionDocumentCompactReadResponse` |
| [artifact.get_production_spec](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 读取当前或指定版本制作约束，供不同工种对齐。 | `ProductionSpecGetInput` → `ProductionSpecCompactReadResponse` |
| [artifact.import_catalog](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 导入已明确选中的跨项目资产候选；候选适合新用途仍须具体判断。 | `AssetCatalogImportInput` → `AssetMutationResponse` |
| [artifact.import_local_version](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 把本地媒体登记成资产新版本；登记不等于已验收或正式采用。 | `AssetVersionImportLocalInput` → `AssetMutationResponse` |
| [artifact.import_stock](../../backend/short_drama/tools/artifact/__init__.py) | E / 否 | 导入选定的素材源候选；外部素材不自动获得角色身份权威性。 | `StockAssetImportInput` → `AssetMutationResponse` |
| [artifact.import_url_version](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 从 URL 下载并登记资产版本；源码标记 W，不能据此断言无网络 IO。 | `AssetVersionImportUrlInput` → `AssetMutationResponse` |
| [artifact.inspect_coverage](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 读所需参考覆盖与缺口，区分必需/可选；不代 Agent 自动选择第一项候选。 | `AssetCoverageInput` → `AssetCoverageResponse` |
| [artifact.list_production_documents](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 列文档身份与当前精确版本，不读取正文或全历史。 | `ProductionDocumentListInput` → `ProductionDocumentCompactListResponse` |
| [artifact.patch_asset_plan](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 按稳定对象 ID 局部追加、替换或删除计划内容，保护无关工作。 | `AssetPlanPatchInput` → `AssetPlanPatchSummaryResponse` |
| [artifact.patch_asset_plan_fields](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 对选中实体作封闭字段 patch；适合明确局部修订，不代替整体语义协调。 | `AssetPlanFieldPatchInput` → `AssetPlanPatchSummaryResponse` |
| [artifact.read_current](../../backend/short_drama/tools/studio/__init__.py) | R / 是 | 读当前剧本、镜头、资产及精确媒体引用；按 scene_id/shot_id 缩小范围。 | `CurrentReadInput` → `CurrentReadOutput` |
| [artifact.reconcile_asset_plan_execution_contract](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 把 Agent 已决定的 canonical、supplemental、引用和 lineage 关系协调为合法计划。 | `AssetPlanExecutionReconcileInput` → `AssetPlanExecutionReconcileSummary` |
| [artifact.replace_asset_plan](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 整体修订资产计划；必须保留不打算删除的对象，局部改动优先 patch。 | `AssetPlanReplaceInput` → `AssetPlanMutationSummaryResponse` |
| [artifact.replace_production_spec](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 基于当前版本更换制作约束；不悄悄重写已有分镜。 | `ProductionSpecReplaceInput` → `ProductionArtifactMutationSummaryResponse` |
| [artifact.retry_asset_plan_attempt](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 对已持久化的失败计划请求作稳定 ID 与 JSON Pointer 局部修复后重试。 | `AssetPlanAttemptRetryInput` → `AssetPlanMutationSummaryResponse` |
| [artifact.review_version](../../backend/short_drama/tools/artifact/__init__.py) | W / 是 | 保存 Agent 已作出的采用用途、批准/拒绝及理由；不会自行看图。 | `AssetVersionReviewInput` → `AssetMutationResponse` |
| [artifact.revise_production_document](../../backend/short_drama/tools/artifact/__init__.py) | W / 否 | 基于当前文档版本追加修订；回执保留新版本身份。 | `ProductionDocumentReviseInput` → `ProductionArtifactMutationSummaryResponse` |
| [artifact.search_catalog](../../backend/short_drama/tools/artifact/__init__.py) | R / 是 | 搜索可复用的已批准本地资产目录；匹配与采用仍由 Agent 判断。 | `AssetCatalogSearchInput` → `AssetCatalogSearchResponse` |
| [artifact.search_stock](../../backend/short_drama/tools/artifact/__init__.py) | E / 是 | 查询已配置素材源候选；不自动导入，也不是任意网页浏览器。 | `StockAssetSearchInput` → `StockAssetSearchResponse` |
| [artifact.update_brief](../../backend/short_drama/tools/artifact/__init__.py) | W / 是 | 更新资产外观、空间、材质、状态与引用需求；不自动触发生图。 | `AssetBriefUpdateInput` → `AssetMutationResponse` |

## 8. 图像生成与本地处理（image）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [image.advance](../../backend/short_drama/tools/image/__init__.py) | E / 否 | 推进一个已持久化图像任务的确定生命周期，可能执行慢 Provider 操作。 | `ImageTaskActionInput` → `ImageTaskResponse` |
| [image.cancel](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 取消符合条件的未终态图像任务；不抹掉历史或已生成文件。 | `ImageTaskActionInput` → `ImageTaskResponse` |
| [image.capability](../../backend/short_drama/tools/image/__init__.py) | R / 是 | 读本地适配器配置与能力快照，不泄露密钥，不发起图片生成。 | `ImageCapabilityInput` → `ImageProviderCapabilityResponse` |
| [image.compose_reference_sheet](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 把明确选中的 2–9 张本地图片确定性拼成参考板。 | `ImageReferenceSheetComposeInput` → `AssetMutationResponse` |
| [image.compose_text_plate](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 本地排版必须准确的文字、金额、名称等；避免靠生成模型重绘精确字符。 | `ImageTextPlateComposeInput` → `AssetMutationResponse` |
| [image.composite_plane](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 将已有准确图文按观察到的四角映射到照片平面，保存本地候选。 | `ImagePlaneCompositeInput` → `AssetMutationResponse` |
| [image.create_edit_guide](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 为实际观察到的问题生成局部编辑图示和透明遮罩；不调用 Provider。 | `ImageEditGuideInput` → `AssetImageEditGuideResponse` |
| [image.crop](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 从实际观察过的归一化区域裁切，登记新图片版本。 | `ImageCropInput` → `AssetMutationResponse` |
| [image.derive_edit_result](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 复用已完成遮罩编辑的 Provider 原始像素，本地重合成或取出结果。 | `ImageEditResultInput` → `AssetMutationResponse` |
| [image.enqueue](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 创建一个明确 GENERATE/REFERENCE_GENERATE/EDIT 图像请求；排队不等于媒体成功。 | `ImageEnqueueInput` → `ImageTaskResponse` |
| [image.list](../../backend/short_drama/tools/image/__init__.py) | R / 是 | 读已持久化图像任务，可按资产筛选；不会推进任务。 | `ImageListInput` → `ImageTaskListResponse` |
| [image.recover_output](../../backend/short_drama/tools/image/__init__.py) | E / 否 | 恢复已经存在但下载或落盘中断的输出；不新建远端生成。 | `ImageTaskActionInput` → `ImageTaskResponse` |
| [image.resolve_not_submitted](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 对账已证明未提交时记录该事实；不能把超时自行视为未受理。 | `ImageNotSubmittedInput` → `ImageTaskResponse` |
| [image.retry](../../backend/short_drama/tools/image/__init__.py) | W / 否 | 仅 RETRY_SAME_REQUEST 分类可原请求重试；语义错误需修订，UNKNOWN 先对账。 | `ImageTaskActionInput` → `ImageTaskResponse` |
| [image.view](../../backend/short_drama/tools/image/__init__.py) | R / 是 | 向紧接的 Worker 决策附真实图片像素；不创建版本，不自动产生 QA 判断。 | `ImageViewInput` → `ImageViewOutput` |

## 9. 生成组与 H3 路线（route）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [route.compile_generation_plan](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 把分镜组编译为 Provider 中立生成单元和镜头源时间窗；不提交 H3。 | `GenerationPlanCompileInput` → `GenerationPlanMutationResponse` |
| [route.compile_h3_prompt](../../backend/short_drama/tools/route/__init__.py) | E / 否 | 基于精确镜头/生成单元与选定媒体编译 H3 prompt，内部可能调用语言模型。 | `RoutePromptCompileInput` → `LanguagePromptCompileResponse` |
| [route.enqueue_h3_probe](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 按明确 typed 请求创建 H3 测试任务；仅真实 H3 目录保留。 | `RouteProbeInput` → `H3ProbeResponse` |
| [route.execute_h3_plan](../../backend/short_drama/tools/route/__init__.py) | E / 否 | 按持久化路线与显式选项执行真实 H3；不会因名称相似而执行 Mock。 | `RouteExecuteInput` → `H3ProductionRouteExecuteResponse` |
| [route.get_generation_plan](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读取指定生成计划和单元；缺计划时报告缺项，不隐式创建。 | `GenerationPlanGetInput` → `GenerationPlanReadResponse` |
| [route.get_generation_unit_context](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读取选定组的当前事实和来源检查，不因全局旧计划阻断未变的组。 | `GenerationUnitContextInput` → `H3GenerationUnitContextResponse` |
| [route.get_h3_group_operation](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读取已保存组操作和当前任务状态，不调用模型或视频 Provider。 | `H3GroupOperationGetInput` → `H3GroupOperationResponse` |
| [route.get_h3_plan](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读完整单条路线、prompt、有序媒体及当前来源检查。 | `RoutePlanGetInput` → `H3ProductionRoutePlanResponse` |
| [route.get_h3_run](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读精确 H3 执行记录、输入顺序、prompt 和已存远端结果。 | `RouteTaskInput` → `H3ProviderRun` |
| [route.h3_capability_profile](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 读已接入 H3 的能力合同与证据边界。 | `RouteProjectInput` → `H3CapabilityProfileResponse` |
| [route.list_h3_plans](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 列路线与当前 source_checks；历史 READY 不保证现在仍有效。 | `RouteProjectInput` → `H3ProductionRoutePlanListResponse` |
| [route.list_h3_reconciliation](../../backend/short_drama/tools/route/__init__.py) | E / 是 | 分页查询与本地未知提交兼容的远端候选；空页不证明没提交。 | `RouteReconciliationInput` → `H3ReconciliationCandidatesResponse` |
| [route.list_h3_runs](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 列紧凑 H3 执行、状态和 Take ID，正文按需读取。 | `RouteProjectInput` → `H3ProviderRunIndexResponse` |
| [route.plan_h3](../../backend/short_drama/tools/route/__init__.py) | E / 否 | 保存当前 H3 路线，明确镜头或生成单元、输入、限制和准备状态。 | `RoutePlanInput` → `H3ProductionRoutePlanResponse` |
| [route.prepare_h3_group](../../backend/short_drama/tools/route/__init__.py) | E / 是 | 组合完成当前组准备，可按 enqueue 控制是否入队；有操作记录，非任意万能重试。 | `H3GroupOperationInput` → `H3GroupOperationResponse` |
| [route.preview_h3_probe](../../backend/short_drama/tools/route/__init__.py) | R / 是 | 生成请求预览，不发送远端生成。 | `RouteProbeInput` → `H3CompilePreviewResponse` |
| [route.resolve_h3_not_submitted](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 记录已确认未受理的对账结论，随后才可继续本地流程。 | `RouteResolveNotSubmittedInput` → `H3TaskActionResponse` |
| [route.resolve_h3_submission](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 把已核实的远端 task ID 绑定到本地未知提交任务。 | `RouteResolveSubmissionInput` → `H3TaskActionResponse` |
| [route.review_h3_take](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 保存对候选真实观察形成的质量判断；PENDING 与 REJECTED 含义不同。 | `RouteReviewTakeInput` → `H3TakeReviewResponse` |
| [route.update_h3_asset_preparation](../../backend/short_drama/tools/route/__init__.py) | W / 否 | 回写关键帧准备交接；READY 不自动把旧 NEEDS_ASSET 路线变成可执行。 | `RouteAssetPreparationUpdateInput` → `H3RouteAssetPreparationUpdateResponse` |

## 10. 任务生命周期与 ComfyUI（task）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [task.advance](../../backend/short_drama/tools/task/__init__.py) | E / 否 | 推进指定视频任务一个生命周期动作；成功返回实际 Take 引用。 | `TaskActionInput` → `GenerationTask` |
| [task.cancel](../../backend/short_drama/tools/task/__init__.py) | W / 否 | 取消符合当前条件的任务；远端取消效果仍取决于具体实现和 Provider。 | `TaskActionInput` → `GenerationTask` |
| [task.drain](../../backend/short_drama/tools/task/__init__.py) | E / 否 | 仅推进确定性 Mock 任务，不用于真实 H3。 | `WorkerDrainRequest` → `WorkerDrainResponse` |
| [task.get](../../backend/short_drama/tools/task/__init__.py) | R / 是 | 读取本地已存任务和 Take，不轮询 Provider，也不自动补发。 | `TaskActionInput` → `TaskReadResponse` |
| [task.read_workflow](../../backend/short_drama/tools/studio/__init__.py) | R / 是 | 读取可选 ComfyUI 连接、实际工作流和输入绑定。 | `WorkflowReadInput` → `CurrentReadOutput` |
| [task.reconcile](../../backend/short_drama/tools/task/__init__.py) | E / 否 | 仅对账确定性 Mock 任务，不是 H3 未知提交的通用恢复接口。 | `TaskActionInput` → `GenerationTask` |
| [task.recover_output](../../backend/short_drama/tools/task/__init__.py) | E / 否 | 取回已完成但本地中断的 Provider 输出。 | `TaskActionInput` → `GenerationTask` |
| [task.retry](../../backend/short_drama/tools/task/__init__.py) | W / 否 | 问题修复后显式重试支持的失败/取消任务；未知提交不能直接重试。 | `TaskActionInput` → `GenerationTask` |
| [task.run_workflow](../../backend/short_drama/tools/studio/__init__.py) | E / 否 | 执行已接入 ComfyUI 的 API 工作流，绑定真实素材并导入输出。 | `WorkflowInput` → `ComfyRun` |

## 11. 视频候选与观察（video）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [video.compare_takes](../../backend/short_drama/tools/video/__init__.py) | E / 否 | 按完整生成组或指定镜头窗比较候选视觉；不评判原声对白。 | `VideoCompareTakesInput` → `TakeComparisonResponse` |
| [video.enqueue](../../backend/short_drama/tools/video/__init__.py) | W / 否 | 创建确定性 Mock 候选批次；真实视频用 H3 路线执行。 | `VideoEnqueueInput` → `ProjectAggregate` |
| [video.extend](../../backend/short_drama/tools/video/__init__.py) | W / 否 | 确定性 Mock 延长候选；不是已接通的 LTX 模型 Extend。 | `VideoLocalReworkInput` → `GenerationTask` |
| [video.extract_frame](../../backend/short_drama/tools/video/__init__.py) | W / 是 | 从真实 Take 精确位置抽帧，保存可用图像资产并返回实际 PTS。 | `VideoExtractFrameInput` → `TakeFrameAssetResponse` |
| [video.gap_fill](../../backend/short_drama/tools/video/__init__.py) | W / 否 | 确定性 Mock 补洞候选；不代表真实 Provider 同名能力。 | `VideoLocalReworkInput` → `GenerationTask` |
| [video.get_lip_sync](../../backend/short_drama/tools/video/__init__.py) | R / 是 | 只读历史唇同步记录；当前不执行 Kling 唇同步。 | `VideoLipSyncRunInput` → `LipSyncResponse` |
| [video.get_take_inspection](../../backend/short_drama/tools/video/__init__.py) | R / 是 | 读已有观察报告，避免重新抽帧、调用模型或改 QA。 | `VideoGetTakeInspectionInput` → `VideoInspectionLookupResponse` |
| [video.inspect_take](../../backend/short_drama/tools/video/__init__.py) | E / 否 | 观察 Take 或实际剪辑范围并保存视觉证据；静态帧不能替代音频听审。 | `VideoInspectTakeInput` → `VideoInspectionResponse` |
| [video.retake](../../backend/short_drama/tools/video/__init__.py) | W / 否 | 确定性 Mock 替换候选；真实局部补拍走 H3 当前组流程。 | `VideoLocalReworkInput` → `GenerationTask` |
| [video.select_take](../../backend/short_drama/tools/video/__init__.py) | W / 是 | 按当前可用候选与精确范围正式采用，可限 clip 使用位置并检查剪辑修订。 | `VideoSelectTakeInput` → `ProjectAggregate` |

## 12. 声音与字幕（audio）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [audio.align_subtitles](../../backend/short_drama/tools/audio/__init__.py) | E / 否 | 用本地 ASR 将选定字幕对齐实际采用音频，遵守当前时间线修订。 | `AudioAlignNativeSubtitlesInput` → `NativeSubtitleAlignmentResponse` |
| [audio.analyze_music](../../backend/short_drama/tools/audio/__init__.py) | E / 否 | 分析当前 Master Audio，保存 Audio Structure Map；依赖已配置分析服务。 | `AudioAnalyzeMusicInput` → `MusicAnalysisResponse` |
| [audio.bind_master](../../backend/short_drama/tools/audio/__init__.py) | W / 否 | 从选定可用音频窗口建立项目 Master Audio。 | `AudioBindMasterInput` → `MasterAudioBindingResponse` |
| [audio.inspect_manifest](../../backend/short_drama/tools/audio/__init__.py) | E / 否 | 检查 EDITORIAL 或候选 TAKE 的声音/字幕及信号；不等于采用候选。 | `AudioInspectManifestInput` → `AudioInspectionResponse` |
| [audio.transcribe_native](../../backend/short_drama/tools/audio/__init__.py) | R / 是 | 转写实际原声音频，返回观察而不改剪辑；不能用抽帧代替此能力。 | `AudioTranscribeNativeInput` → `NativeTranscriptionResponse` |
| [audio.update_timed_text_lock](../../backend/short_drama/tools/audio/__init__.py) | W / 否 | 创建或修订锚定 Master Audio 的精确时序文字。 | `AudioTimedTextLockUpdateInput` → `TimedTextLockResponse` |
| [audio.update_voice_reference](../../backend/short_drama/tools/audio/__init__.py) | W / 是 | 绑定或清除人物选定的可用参考音频；不是通用音色克隆接口。 | `AudioVoiceReferenceUpdateInput` → `VoiceReferenceMutationResponse` |

## 13. 剪辑与导出（timeline）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [timeline.add_asset_overlay](../../backend/short_drama/tools/studio/__init__.py) | W / 是 | 把已有精确图片叠加到当前剪辑，保留原视频、声音、字幕和时长。 | `TimelineOverlayInput` → `CurrentReadOutput` |
| [timeline.edit_clips](../../backend/short_drama/tools/studio/__init__.py) | W / 否 | 按精确 timeline/clip/subtitle ID 局部修改，检查 expected_revision。 | `TimelineEditInput` → `CurrentReadOutput` |
| [timeline.export](../../backend/short_drama/tools/timeline/__init__.py) | E / 否 | 导出既有版本化 timeline manifest，不隐式重建；与 Studio editorial 路径分开辨认。 | `TimelineExportInput` → `ExportRecord` |
| [timeline.export_editorial](../../backend/short_drama/tools/studio/__init__.py) | W / 否 | 把已保存 Studio/LTX 剪辑导出实际 MP4；本地重 IO，源码标为 W。 | `TimelineExportInput` → `StudioExport` |
| [timeline.read_editorial](../../backend/short_drama/tools/studio/__init__.py) | R / 是 | 按整集、镜头或实际选择读时间线、修订与源时间映射。 | `TimelineReadInput` → `CurrentReadOutput` |
| [timeline.read_export](../../backend/short_drama/tools/studio/__init__.py) | R / 是 | 读并实际解码已保存导出，可按时间抽取成片帧作观察。 | `ExportReadInput` → `CurrentReadOutput` |
| [timeline.rebuild](../../backend/short_drama/tools/timeline/__init__.py) | W / 否 | 从当前采用 Take 建 manifest；已有用户编辑使用 editorial 接口。 | `TimelineRebuildInput` → `TimelineManifest` |
| [timeline.save_editorial](../../backend/short_drama/tools/studio/__init__.py) | W / 否 | 保存完整或指定镜头的 LTX 时间线，合并局部改动并顺移后续内容。 | `TimelineWriteInput` → `CurrentWriteOutput` |

## 14. 专业观察与检查（qa）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [qa.analyze_source_image](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 观察用户或外部源图，说明可见覆盖、缺口和可复用方式。 | `QaSourceImageAnalyzeInput` → `SemanticSourceImageAnalysis` |
| [qa.check_input_compatibility](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 对所选图片与当前生成组及镜头时窗作联合兼容观察。 | `QaInputCompatibilityInput` → `InputCompatibilityResponse` |
| [qa.compare_keyframes](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 比较候选关键帧，允许所有候选均不适用。 | `QaKeyframeCompareInput` → `KeyframeComparisonResponse` |
| [qa.inspect_asset_version](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 对实际图片及本次用途做多模态检查；报告不等于写入采用决定。 | `QaAssetVersionInspectInput` → `SemanticAssetReview` |
| [qa.list_visual_input_reports](../../backend/short_drama/tools/qa/__init__.py) | R / 是 | 读取已保存的排序、关键帧比较和兼容报告，避免无故重复检查。 | `QaVisualReportsInput` → `VisualInputReportsResponse` |
| [qa.rerank_reference_visuals](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 观察并排序合法参考候选，为生成选择提供证据。 | `QaReferenceRerankInput` → `ReferenceVisualRerankResponse` |
| [qa.review_episode](../../backend/short_drama/tools/qa/__init__.py) | E / 否 | 可选地依据已有视觉证据检查整集叙事连续性；不是所有任务的审批门。 | `QaReviewEpisodeInput` → `EpisodeReviewResponse` |

## 15. 定向修改与审阅（change）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [change.apply](../../backend/short_drama/tools/change/__init__.py) | W / 否 | 应用已有内容的 typed 语义修改，并只使相关下游失效；不负责新建场次。 | `ChangeApplyInput` → `SemanticChangeApplyResponse` |
| [change.preview](../../backend/short_drama/tools/change/__init__.py) | E / 是 | 为语义修改规划最小依赖影响范围；预览不等于已应用。 | `ChangePreviewInput` → `SemanticChangePlan` |
| [change.propose_text](../../backend/short_drama/tools/studio/__init__.py) | W / 是 | 仅在用户要求逐项审阅时保存真实文字差异；用户接受前不写正式正文。 | `TextProposalInput` → `StudioChangeSet` |

## 16. 上下文与调用证据（agent）

| 工具 | 效果 / 幂等 | 实际用途与边界 | 输入 → 输出 |
| --- | --- | --- | --- |
| [agent.get_tool_attempt](../../backend/short_drama/tools/agent/__init__.py) | R / 是 | 读取持久化调用的原参数、结果或两者；Leader 可读本 run，Worker 限自己的或明确交接的引用。 | `AgentGetToolAttemptInput` → `AgentGetToolAttemptOutput` |
| [agent.read_context](../../backend/short_drama/tools/agent/__init__.py) | R / 是 | 按 snapshot_id、JSON pointer、offset/limit 精确回读已卸载上下文；快照不是最新项目。 | `AgentReadContextInput` → `AgentReadContextOutput` |

## 17. 默认未启用的委派适配器

`agent.delegate`：WRITE、非幂等，`AgentDelegateInput` → `AgentDelegateOutput`。它把 recipient_role、objective、artifact_refs、constraints、expected_output 交给外部传入的 AgentDelegator。仅有这个接口定义不能证明默认 Worker 能递归创建孙 Agent，也不能把它替换成当前完整 WorkOrder/ContextManifest 协议。见 [agent 注册模块](../../backend/short_drama/tools/agent/__init__.py)。

## 18. 面试时应能不查表还原的六条链

1. **剧本**：`artifact.read_current` → 必要时 `script.plan_scenes` → `script.save_scene` → 精确结果交接。
2. **图片**：`artifact.get_asset_work_context` → `image.capability` → `image.enqueue` → `image.advance` → 实际观察/用途判断 → 引用登记。并非每次都必须重新跑 QA 或改变 APPROVED。
3. **H3**：`route.get_generation_unit_context` → `route.prepare_h3_group` 或显式编译/规划 → 真实任务推进 → Take → 观察 → `video.select_take`。准备是否入队由本次目标及 enqueue 决定。
4. **图像修复**：观察具体缺陷 → 选择裁切/确定性排字/平面合成/遮罩编辑 → 新候选 → 对照真正受影响区域，不为每一步机械新建 Agent。
5. **剪辑**：`timeline.read_editorial` → `timeline.edit_clips` 或局部 `timeline.save_editorial` → `timeline.export_editorial` → `timeline.read_export`。
6. **跨 Agent 接手失败请求**：明确 ToolAttemptRef → `agent.get_tool_attempt` → 修正真实参数，或用 `artifact.retry_asset_plan_attempt` 做局部修复；不能凭聊天摘要重造整个失败请求。

这些是能力组合示例，不是全产品固定流水线。用户只要求正文时不应继续生成，局部修改也不应无条件重跑全部链路。
