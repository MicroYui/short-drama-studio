# 素材登记合同：明确选用、参考包与共享备注

## 实际链路与统计范围

本次只读观察针对项目 `project-5b8286dc63e6`、Job `studio-job-20f086971171`。所有时间为北京时间；原始事件和 Trace 使用 UTC。数据来自当前实际数据库和模型请求，不是模拟运行。Task 于 2026-09-12 07:35:04 请求暂停，07:36:56.620 的事件 7250 确认 `paused` 后才修改产品源码。未改运行数据库、历史 Trace 或已生成媒体，没有调用 Provider。

收尾素材 Worker `work-order-9830d5cadfc34b9ebaa95946d8c03198` 为 `wi-yqq-s14-recap-start` 生成候选 `asset-version-08a44419dfc3`，归属许岚 `asset-85d4f6cc7d52`。t18 已登记候选；t19 `qa.inspect_asset_version` 于 07:27:28.524 完成，评分 4.5、`recommended=true`。QA 确认面部、肩膀、双手、右手裸笔与桌上唯一笔帽的初态用途，提出顶部留白可选裁切；它没有证明画外纸叠、跨镜落帽位置或动态扣帽成功。

后续实际发生：

| Turn / 动作 | 真实语言调用 | 输入 tokens | 输出 tokens | 已报告缓存输入 | LM 耗时 | Tool 耗时 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| t20 `artifact.review_version` | `model-e93dfdf32754` / event 6846 | 76,405 | 594 | 8,906 | 12.547010 秒 | 0.741342 秒 |
| t21 `artifact.patch_asset_plan_fields` | `model-d5b6172bf747` / event 6859 | 76,374 | 680 | 8,906 | 14.026770 秒 | 1.227370 秒 |
| t22 `artifact.add_reference_entry` | `model-d8fb5feeed43` / event 6873 | 68,577 | 519 | 8,906 | 11.151156 秒 | 0.244915 秒 |
| t23 `shot.save_current` | `model-ba986cd3b134` / event 6933 | 69,326 | 1,891 | 8,906 | 30.973161 秒 | 1.197344 秒 |
| 合计 | 4 次成功语言决策 | 290,682 | 3,684 | 35,624 | 68.698097 秒 | 3.410971 秒 |

模型均为实际 `gpt-6-astra` / `xhigh`。t19 完成至 t23 完成的墙钟为 84.168112 秒；该窗口还包含上下文装配、调度和持久化，不能仅用 LM 与 Tool 耗时相加代替。QA 自身另有一次视觉语言调用 `model-5d174518332b`：24,000 输入、4,807 输出、3,205 缓存输入，115.705600 秒；不混入上述四次决策。此处只统计一条已发生的登记链，不是包含多次开发暂停的整个项目性能基准。

t20 明确决定采用现图、不按可选建议裁切，并记录观察范围和待视频验证事项。这是有效的选用判断，不能因为 QA 推荐就自动替 Agent 批准。t21 再写完成指针，同时重复提交 t18 已保存且未变化的两个 candidate 字段。t22 的主体、角色 `CHARACTER_BODY`、角度、景别和来源根在 t14 读取的计划中已有依据，工具调用补充了实际可见的用途与标签。t23 将同一候选绑定到 `shot-yqq-14` 的第一个 beat（0–1.7 秒），保留既有动作、对白和后续 beat。各状态事实不同，不能只因同一媒体 ID 重复出现就认定都应删除。

t23 后直接进入 t24 读取 s13 的另一工作项，没有再读取 s14 分镜或生成参考预览。这是 [29：采用链往返](29-asset-adoption-round-trips.md) 和 [41：镜头保存回执](41-shot-save-receipt.md) 对应行为已被实际使用的证据；不是全部登记成本已经解决。

## 确定的描述冲突

实际请求文件：

`output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T232730045265-90051980-model.input.json`

该 t20 请求同时包含两套不同说明：

- `asset_builder.md` 已允许明确选中的当前可用图片直接登记 `completed_*` 和 ReferencePack，单独审核标记不是前置条件。
- `available_tools` 首句和已加载的 `artifact.add_reference_entry` schema 仍称“已批准图片版本”“不会为草稿自动背书”，并把 `CANONICAL_MASTER` 写成必须使用当前已批准母版。

实际 `ShortDramaService.add_reference_pack_entry` 的合同为：

1. 输入为 `CURRENT`、状态 `DRAFT` 或 `APPROVED` 的图片版本，且有本地路径或外部 URL。
2. `CANONICAL_MASTER` 必须使用该主体自身的图片，但不要求版本 ID 等于 `Asset.approved_version_id`。其 `lineage_root_version_id` 可省略或填写本版本，保存时为自身。
3. 其他角色可以使用其他 Asset 承载的图像；若明确提供来源根，该根必须是主体自身当前可用的图片。独立来源可不填来源根。
4. 参考包登记不改变资产的 `current_version_id`、`approved_version_id` 或图片审核状态。

因此旧 Tool 描述确实可能诱导先做独立批准。实际 t20 同时承担了合理的选用说明，不能证明这一调用完全由旧描述造成，也不能机械取消所有 `review_version`。

本次完整替换 `tools/artifact/__init__.py` 的该 Tool 描述，同时说明 DRAFT、主体归属、canonical 角色、来源根与指针边界；没有放宽服务校验。Builder 的既有候选段只补充：已保存候选未变时，只提交本次改变的完成字段。实际审图、选用、拒绝和保留意见的判断仍由 Agent 作出。

## 共享 notes 不是个人进度追加

同轮开场素材 Worker `work-order-4bc871f2fb124bfa889aab8f65014161` 的 t24 于 07:33:11.406 产生事件 7152：`artifact.patch_asset_plan_fields` / `tool-817268e97051` 返回 `FAILED`，错误为 `AssetPlan notes changed while this work was running`。目标是 `wi-yqq-s01-opening-contact` 的两个候选指针，base 为 `production-document-version-ce739de75165`；同时传入只有一条个人进度的 `notes` 列表，内容为新图已落盘、尚未采用、等待实查。

该字段实际是共享计划完整备注的替换，不是向工作项追加进度。并行工作已修改共享备注，原冲突检查正确阻止覆盖。Tool 的回执失败不能被 SpecialistTurn 外层 `status=COMPLETED` 误解为写入成功。

实际恢复也已完成：t25 于 07:33:23.641 读回三个目标工作项的局部上下文；t26 省略 `notes`，仅登记候选指针，于 07:33:36.392 成功；t27 随后继续实际 QA。它是自主修复后的成功，仍包含一次失败和回读，不能算无返工执行。

本次在紧凑 Tool 的 `AssetPlanFieldPatchInput.notes` schema 和 Builder 既有候选段明确：

- 省略或 `null` 保留现有共享备注。
- 提供列表表示整表替换，`[]` 表示清空，并保持基于 base 的共享备注冲突检查。
- 个人进度写 `working_notes`；只有实际要修改共享备注时，才提供完整目标列表。

Domain `AssetPlanPatchRequest.notes` 同步说明此合同。服务保留原冲突判断，错误明确定位共享 `/notes`、整表替换以及本次无字段保存；个人进度改用 `working_notes`，可以省略 notes 重试原精确字段，仍保留目标内容冲突检查。真正修改共享备注时，应读取最新备注并合并完整目标列表。没有自动丢弃请求中的真实备注，也没有关闭并发冲突。

新增两项行为回归分别覆盖省略 notes 和显式 null：并行任务已修改另一工作项及共享备注时，旧请求的个人备注保存失败且无写入；原 base 的同一字段请求可以安全重试，保留并行内容；之后基于当前版本明确合并共享备注仍可成功。两项新用例及既有字段并发用例合计 3 项通过（0.86 秒）。

## 公开依据与未实施边界

[Anthropic 的官方工具设计文章](https://www.anthropic.com/engineering/writing-tools-for-agents)建议用真实任务和调用记录评估工具，清楚描述其行为，返回决策所需事实，并围绕常见完整任务组织接口。文章举例用一次能够检查可用性并安排会议的工具替代多个底层接口调用。这是公开的工程方法，不是 MiniMax Design、即梦或 Kling 私有 Agent 调度架构的证据。

本次采用的是先消除与实际能力矛盾的描述，让已确定的选择无需因错误前置条件再走批准。现有 `patch_asset_plan_fields` 只修改计划；`reconcile_asset_plan_execution_contract` 只修计划执行意图；`shot.update_reference_plan` 不替代正文中的精确 cue；`shot.save_current` 不同时登记工作项完成与 ReferencePack。因此，不能通过 Skill 宣称这些状态已经能一次保存。

如果后续实际运行仍证明已确定选择后的登记决策成本值得合并，需要单独实现明确的复合保存能力，并复用现有精确版本、工作项与镜头并发保护。参考包用途、真实来源和正文作用时段仍须由调用方明确；不能按 QA 分数自动采用，也不能把计划中的所有服务镜头全部绑定。该能力本次没有实施，不能预先宣称四次决策已缩成一次。只减少候选重复字段也不等于减少一次 LM 请求。

## 验证与交付状态

复用五项既有回归，全部通过（1.07 秒）：

- `test_reference_pack_preserves_subject_roles_and_uses_explicit_draft`：未批准图片可明确加入参考包，真实拒绝后不再可选。
- `test_canonical_pack_role_uses_an_explicitly_selected_subject_image`：主参考的主体归属与显式选择，不错误要求已是批准指针。
- `test_explicit_draft_can_complete_plan_and_unblock_derived_work`：完成登记与后续依赖不要求额外批准。
- `test_parallel_field_edits_preserve_sibling_work_and_reject_changed_target`：保留并行无关内容，保护已改变的目标。
- `test_patch_asset_plan_applies_compact_fields_across_items_atomically`：原子字段保存和既有共享备注行为。

`tools/artifact/__init__.py` 的 Pyright 为 0 errors / 0 warnings，修改文件 `git diff --check` 通过。低影响描述修改未新增镜像文案测试；共享备注恢复采用上面的实际服务行为回归。产品源码修改紧凑 Tool 和 Domain 的 notes 说明、服务冲突反馈、参考包 Tool 描述及 Builder 既有候选段；原始记录不改。

以上为统一加载前的本地回归与合同说明修复。恢复后的实际输入核对见文末；尚无对照证明调用次数、输入 tokens 或总耗时已下降。

## 同轮视频准备的只读核对

在暂停后的实际项目快照中，当前分镜为 `production-document-version-417b64f11e95`，制作规格为 `production-document-version-8ad7797db7d1`；项目最后保存于 07:36:55.765。使用只读 SQLite 快照及现有纯函数核对，没有构造运行项目 Service，也没有调用 `prepare_h3_group`、语言模型或媒体 Provider。

当前为 20 镜、5 个生成组、59 秒。生成计划尚未保存，H3 operation、route plan、generation task 和 provider run 均为 0。根据当前正式分镜纯派生的结果为：

| 生成组 | 当前镜头 | 设计秒数 | H3 整数秒数 | 当前默认输入 |
| --- | --- | ---: | ---: | --- |
| g01 | 01、02、03、03-evidence | 12.5 | 13 | DIRECT，0 图 |
| g02 | 04、05、06、06-evidence、07 | 16 | 16 | DIRECT，0 图；超过单次上限 |
| g03 | 08、09 | 10 | 10 | REFERENCE，3 图 |
| g04 | 10、10-reaction、10-result、11 | 9 | 9 | DIRECT，0 图 |
| g05 | 12、12-reaction、12-result、13、14 | 11.5 | 12 | REFERENCE，2 图 |

表中短名对应实际 `clip-yqq-g01` 至 `clip-yqq-g05` 和 `shot-yqq-*`。g03 当前输入为 `asset-version-3a8b4ea6267b`（人物身份）、`asset-version-d42fba8ad6bb`（场景身份）、`asset-version-07155ca88530`（构图）；g05 为 `asset-version-89add4b760e5` 和 `asset-version-08a44419dfc3`（构图）。这些明确引用均为当前可用图片，本地文件存在；选择和最终解析保留相同精确版本，未丢 ID。其余三组当前 0 图不代表已经通过视频前准备，也不自动构成非法 DIRECT 请求；是否需要身份、空间或动作初态参考仍是 Agent 根据实际镜头完成的制作判断，不新增每镜一张首帧的门槛。

g02 的 16 秒是当前唯一已证明的单次时长错误。`h3_group_operation.py` 在语言编译前返回实际组 ID、16 秒、4–15 秒范围和局部编辑 Tool 提示。`shot.patch_storyboard` 已有 `clip_group_splices`，能够只替换受影响的连续生成组，保留镜头正文、精确引用及无关组；无须重写全片，也不在创作保存层禁止这份分镜。该处留给产品 Agent 正常局部调整，本次未代改作品。

跨镜续句的现有修复与当前数据一致：g01 的 line02 从组内 8.5 秒至 12.5 秒，覆盖 03 的 2.5 秒和 03-evidence 的 1.5 秒，只派生一次 utterance；各组其他对白均落在实际组窗口内，没有跨两个独立生成组延续同一句的当前冲突。

项目画幅为 9:16，各派生组尚未保存明确分辨率。当前 `resolution=None` 的默认值仍是 768P，不能把用户要求 2K 冒充已经准备完成。后续正常调用应明确 `resolution="2K"`、`aspect_ratio="9:16"`、`enqueue=false`；这组参数经现有纯解析对五组均合法，g02 仍另受时长限制。当前没有任何声线绑定，制作规格使用原生生成对白，不应为该片自动补入全局音频。默认 LANGUAGE 编译和可选官方 Context IR 是二选一；本次未验证官方鉴权，也没有推测最终 7000 字以内的 Prompt 已经生成。

[AceData 当前官方 H3 API](https://raw.githubusercontent.com/AceDataCloud/MiniMaxAPI/main/docs/videos.md) 明确列出 4–15 秒、768P/2K、9:16 等画幅及 7000 字的文本上限；首尾帧路线与全参考路线不能混用。[MiniMax 官方 H3 Skill](https://raw.githubusercontent.com/MiniMax-AI/cli/main/skill/h3-video/references/h3-video.md) 要求说明媒体用途和声画时序，参考音频需同时有图像或视频，但没有要求每个镜头分别提供一张首帧。这些是公开接口与方法依据，不是本账户已经完成 2K 视频或 Context IR 验收的证据。

现有 Fake 回归已覆盖保存跨镜续句后准备、重启复用原 Prompt、明确 enqueue 才创建任务，以及当前 DRAFT 图、构图用途、超长组精确反馈。这次只读核对没有发现必须为本片新增制作流程的依据；已准备媒体尚未绑定、g02 局部分组和最终实际 Prompt 编译均仍是后续工作。

## 代码复查发现：显式 DRAFT 参考重排仍沿用旧批准门

沿当前候选解析路径复查时，发现 `visual_input_quality.build_reference_visual_candidates` 的显式 `candidate_entry_ids` 分支仍要求图片 `APPROVED`，并要求已声明来源根等于主体的 `approved_version_id`。因此，同一条由 `add_reference_pack_entry` 合法保存的当前 DRAFT 参考，可在正常参考预览中被读取，却会在显式 `qa.rerank_reference_visuals` 中被拒绝。这是代码复查和本地用例证明的合同差异；没有证据表明本轮实际项目已经触发这次失败。

本次仅修显式条目分支：复用 `usable_reference_pack_entries` 的当前 DRAFT/APPROVED 图片与来源根归属规则，并在检查前核对声明角色、canonical 的主体和自身来源、实际本地文件。精确选中的旧版本是否仍可用与当前母图指针分别判断；不会把被选图升为母图，也不会修改审核、current 或 approved 指针。拒绝、失效、缺文件、未知来源根和其他主体的来源根仍拒绝，不调用模型。未指定条目的自动主参考选择分支保持原行为。

同一函数的空候选反馈改为“没有匹配本镜头与 ReferencePlan 的已选中或符合条件的当前视觉候选”，不再把全部可用图片称作已批准图片。顺查同模块、rerank Tool 和当前生成组候选路径，没有再发现需要一起修改的同类批准前置描述；其他真正针对自动批准母版的逻辑未替换。

新增 `test_explicit_reference_rerank.py` 的 12 个节点：两种合法 DRAFT 条目通过实际 ToolRegistry → Service → Fake 视觉请求 → 建议保存及重启读取，按字节核对发给模型的是所选 PNG，保持原项目全文和批准母图；十种失效、拒绝、缺文件、错根、未知根、canonical 归属及角色反例在模型调用前失败。与既有视觉工具、多个参考版本、DRAFT 参考包和当前 H3 组候选等回归合计 **24 项通过（5.16 秒）**。源码和新测试 Pyright 为 **0 errors / 0 warnings**，所改文件 `git diff --check` 通过。该本地验证未调用真实 Provider、未写运行项目或历史媒体；不宣称已减少真实返工或语言往返。

## 07:56 恢复后的实际输入核对

本批代码以 `bde1b8b` 加载至后台 PID 60231；同一 Job 于 2026-09-12 **07:56:38.498** 恢复，没有追加创作消息。这仍是经过开发暂停的原调试项目，不是新空项目无干预验收。本次只读核对恢复后最早三份 Worker `model.input.json` 及其对应 `provider.language_generation` 完成事件，未修改源码、运行数据库、历史 Trace 或任务状态，也未为验证额外请求 Tool 或 Provider。

三份均为 `ASSET_BUILDER` / `ASSETS`，实际任务范围分别为四张文字证据图、s12–s14 收尾镜头、s01–s03 开场镜头。精确输入文件：

- 准确文字证据图片准备，`work-order-a318e1e27639488fab20ba34fc3a80d8`：[20260911T235644332534-34fed496-model.input.json](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T235644332534-34fed496-model.input.json)。
- 收尾镜头素材制作，`work-order-9830d5cadfc34b9ebaa95946d8c03198`：[20260911T235644371694-b7aecab7-model.input.json](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T235644371694-b7aecab7-model.input.json)。
- 开场镜头素材制作，`work-order-4bc871f2fb124bfa889aab8f65014161`：[20260911T235644934905-501005ce-model.input.json](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T235644934905-501005ce-model.input.json)。

三份实际输入中均已载入以下内容：

1. `goal.current_goal` 明确“全部视频生成前准备后结束”，要求 2K、9:16、中文普通话，并禁止创建、入队、提交或执行视频任务，不进行声音后期、剪辑或导出。历史完整成片要求仍保留在 `original_request`，没有取代新的当前目标。
2. `tool_schemas["artifact.add_reference_entry"].description` 已是新说明：CURRENT DRAFT/APPROVED 均可，登记不改变审核和 current/approved 指针；CANONICAL_MASTER 使用主体自身图片，不要求已是批准母图，来源根按精确归属校验。这是实际加载的完整 Tool 描述，不是只看到工具目录名字。
3. `tool_schemas["artifact.patch_asset_plan_fields"].input_schema.properties.notes.description` 明确省略或 null 保留、列表整表替换、空列表清空、个人进度使用 working_notes。Builder 实际 `instructions` 同时包含该规则，以及候选未变时只补本次改变的完成字段。
4. Builder 的实际 `instructions` 包含姿态局部编辑的新说明：完整移动部位、必要连接关节、原位置、目标位置和背景恢复范围；不能只圈指尖锁死指根，也不固定扩大到整手或整腕。
5. Builder 的实际 `instructions` 包含可见纸内共面区域的方法：保留源角身份逐点对应，区域须包含本次完整必要内容，只采样源 quad、修改目标 quad，不外推整页、不猜遮挡角，并按实际可见区域保护相交前景。

**这三份均未加载 `image.composite_plane` 的完整 Tool schema。** 因而这里只证明共面子区域方法已进入相关 Builder 的实际指令，不能声称 `ImagePlaneLayer.source_quad/target_quad` 的新字段说明已随该 schema 发给模型。嵌套 QA 模型的新指令也不在本次三份 Worker 请求核对范围内，不能以 Builder 已加载替代其验证。三份 `response_protocol_feedback` 和 `tool_schema_feedback` 均为 null，本次输入未携带这两类修正反馈。

实际请求与完成回执均记录 `gpt-6-astra`、`reasoning_effort=xhigh`，供应商响应的 `response_model` 也为 `gpt-6-astra`；不以配置页或源码标签代替实际请求。用量均由 Provider 报告，三个请求的 `image_count=0`：

| Worker / call_id | 完成事件 | 请求时间（北京时间） | 完成回执时间 | 输入 tokens | 输出 tokens | 缓存输入 tokens | LM 耗时 |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| 文字证据 / `model-fadf343d7f76` | 7309 | 07:56:44.332534 | 07:56:59.194513 | 77,333 | 687 | 9,052 | 14.820312 秒 |
| 收尾镜头 / `model-f928726514e1` | 7318 | 07:56:44.371694 | 07:57:04.981964 | 71,858 | 999 | 0 | 20.556100 秒 |
| 开场镜头 / `model-2d21219359ee` | 7325 | 07:56:44.934905 | 07:57:07.354088 | 78,770 | 1,035 | 9,052 | 22.409006 秒 |

三次均 `success=true`、`completion_type=sse_response_completed`。缓存输入的 0 是该次已报告的实际值，不作为缺失值，也不推测兼容代理为何没有命中。请求时间来自 `model.input`，完成时间来自 Studio 完成事件，LM 耗时采用 Provider adapter 已记录数值；不同 Worker 并发运行，不能将三次 LM 耗时相加称为墙钟。

这次观察证明恢复后新合同和相关方法正文进入了真实请求，**不能证明它们已被正确执行、减少了多少轮次或产生净性能收益**。三个不同任务的首次恢复请求不构成同任务前后对照，原项目也包含多次开发暂停；质量、返工次数、总耗时与缓存收益仍须依据后续实际结果分别判断。
