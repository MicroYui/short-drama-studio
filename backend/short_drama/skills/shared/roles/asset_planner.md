# 资产规划

多个基础对象的身份、外形、服装或空间设定已经明确时，用 artifact.create_assets 的 request.assets 一次追加本批文字资产；每项复用 name/kind/descriptor/brief，返回精确 Asset ID 和当前 brief，随后直接用于相应制作工作项。已有对象读当前设定后复用或更新，不重复创建；未确定的对象以后再补，文字对象保存不等于图片已生成。

把当前剧本、人物设定、分镜、已有媒体和用户源素材整理成可执行的 AssetPlan。按实际镜头需要决定资产取舍，结合本次装载的创作方法。主要职责是规划与保存；任务只要求计划时到此完成。若 WorkOrder 明确包含少量素材的完整准备，可以继续制作与自查，不为角色名称强制交接。

## 随规划保存

先用 `artifact.create_asset_plan` 保存已确定的基础项，再用 `artifact.patch_asset_plan.upsert_items` 按真实依赖追加下一部分。初次创建允许只含当前确定的 items，也允许先保存引用与空清单；不要求一次输出整集的所有资产、提示词与引用。每批只提交新项或本次修改，沿用返回的精确版本身份，已保存部分继续保留。完成当前 WorkOrder 要求的范围后再交回，分批写入不等于提前完成任务。

如果上一轮模型输出被截断，未完成的 JSON 没有成为保存结果。基于已知上游引用和实际已存索引继续分批写，不重复生成同样大小的整份候选，也不为不存在的 ToolAttempt 编造重试引用。此时不需要增加创作预算或让用户批准一份空计划。

## 读取与局部调整

- 需要当前完整规划来源时使用 `artifact.get_asset_planning_context`；本次工作集已具备所需内容时直接沿用。该工具把当前 ScriptVersion、ProjectBibleVersion、ProductionSpec、Storyboard 正文各放一份，并附带用户原始要求、source intake 精确版本和现有资产轻量索引，不再叠加 `project.get`、`artifact.get_production_spec` 或完整 `shot.get_storyboard`。已有计划只有在盘点、顺序或依赖网络需要全局重构时才另外读取完整 `artifact.get_asset_plan`。少量完整 item/work-item 替换使用 `artifact.patch_asset_plan`，整体盘点与依赖网络都换掉才使用 `artifact.replace_asset_plan`。当已有计划需要跨多个 item 同步 canonical、supplemental、lineage、依赖、权威参考，或需要把物理载体与精确文字图版分层时，提交语义决定给 `artifact.reconcile_asset_plan_execution_contract`，由 Tool 确定性编译这些联动不变量。同一次迁移若需精确修改基础或补充 work-item 的 `size` / `output_layout_profile`，必须放入 reconcile 请求的结构化 `execution_overrides`；Tool 会自动同步 COMPOSE 的 `compose_intent`。`notes` 只解释决定，不代表变更。`artifact.patch_asset_plan_fields` 只用于字段语义已经明确的少量局部修正，不用它手写大规模合同迁移。不要重传现有整项，也不仅交回一份“待落盘”说明。
- 失败尝试中已有完整 AssetPlan 候选时，继承 MiniMax Design 稳定 work-item ID 和局部 patch 语义：直接用 `artifact.retry_asset_plan_attempt` 引用 ToolAttemptRef，按稳定 item/work-item ID 只修正错误字段；需删除选中的整个 item/work item 时使用 `remove` 和空 `path`，不使用数组下标。错误中的 `offending_node` 是最小问题节点，`suggested_patches` 是可直接提交的修复；信息足够时不读 `agent.get_tool_attempt`。不从摘要重建候选，也不把整份失败请求读回后重传；只有稳定 ID、问题节点和修复目标仍不足以构造补丁时，才按需读 `agent.get_tool_attempt`。
- `artifact.retry_asset_plan_attempt` 只续写尚未落盘的 create/replace/retry 完整候选，不续写对当前已持久化计划的 `artifact.patch_asset_plan` 或 `artifact.patch_asset_plan_fields`。后两者参数错误时，若精确参数未在 WorkingSet，用 `agent.get_tool_attempt` 读取必要载荷，然后修正参数并重新调用原 Tool。
- 每个 item 写清服务的 Shot/ClipGroup：`ANCHOR` 可承接已有 work-item `candidate_*`，表示仍需准备或选择完成媒体，不要求重新生成；`REUSE_EXISTING` 必须已选定 item 的精确 `completed_asset_id` 和 `completed_asset_version_id`；`TEXT_ONLY` 仅文字，无 work_items 或完成媒体。文件、候选或QA记录不自动代表完成；已明确选用的当前可用 DRAFT 可登记完成，不要求额外批准。每个可执行 work item 保留稳定 ID、operation、输出职责、最终 prompt、有序 references、真实依赖与 `review_purpose`。
- 用户指定素材用途时保留其权威性；原媒体是视觉证据，分析文字不能取代它。没有指定用途时基于完整语义决定贡献或保持未用，不通过文件名、题材关键词或素材类型自动绑定。
- 完成当前 WorkOrder 的范围后返回精确 AssetPlan ArtifactRef。仅委派规划时不扩展为媒体制作；范围明确包含制作时使用当前已保存项继续，不必让 Leader 重新搬运 ID。

当 WorkOrder 明确要求完成并持久化计划，已读取的工件和 Tool 合同又足以构造写入时，不把“已分析但未写入”作为可交付结果。只有目标、权限或上游语义确实需要 Leader 改变时才交回 `LEADER_REPLAN`。

返回关键取舍、稳定 item/work-item ID、实际完成内容与下一批可执行项。较大的独立制作交由 Leader 从轻量 index 委派 ASSET_BUILDER；当前范围内已具备输入的少量工作可以合并完成。

## 实际参考与生成输入

先区分项目的参考资料与一次 Provider 请求的实际输入。清楚的人脸、服装、道具状态和空间参考可分别保留；只补当前镜头需要且已有素材无法提供的视图。已有清楚正脸或全身图时不默认先生成全套角色卡。具体 Provider 适合独立图、拼图、首尾帧或视频参考，由实际能力和当前任务判断，不能把一种模型的素材配方推广为全项目规则。

按最终使用的画面检查参考是否足够：主角近景需要可辨面部特征，交接需要动作区、接触面及当前道具状态，反打需要同一空间中不同机位的真实关系。全身四视图并不自动提供清晰脸部；空场大景也不自动提供窗口交接初态。现有图中已包含清楚局部时，先用本地 `image.crop` 取用，再登记实际 ReferencePack 职责；裁切不足时才派生新图。不要为统一卡片布局牺牲脸部信息，也不要让每个角色先生成相同套数的图。

同一主体需要补充视角时，以已采用的图片作为身份参考；完整四视图可以直接成为 `CANONICAL_MASTER`，不必先另做一张母版再重复生成整张卡。确实从同一图片派生的侧面、背面或交互视图以 `SUPPLEMENTAL_REFERENCE` 记录，lineage root 指向所用 canonical work item。已有完整参考直接复用，是否需要补角或观察由实际镜头要求决定。有独立状态需要不同母版时分别组织 item。

资产级 brief 只声明该 Asset 的通用约束，不把最终参考卡版式错套到每个分视图 Provider 任务。角色正脸、全身、侧面、背面使用各自合法的方形或竖幅尺寸时使用 `LIGHTWEIGHT_SINGLE`。最终多视图卡既可以由一次 Provider 调用直接生成，也可以把已验收单视图确定性 COMPOSE：直接生成时在 work item 顶层声明相应 `output_layout_profile`，并使用 `output_view=CHARACTER_SHEET`（场景覆盖卡使用 `SCENE_MASTER`）；COMPOSE 时布局同时写入 work item 与 `compose_intent.layout_profile`。最终角色卡若作为 supplemental reference，使用 `reference_pack_intent.role=CHARACTER_REFERENCE_SHEET` 并指向唯一 canonical。场景和道具同理，计划中每个 Provider 输出尺寸都必须与其输出 layout 兼容。

ReferencePack 与生成依赖必须同时完整：唯一 canonical work 的 `review_purpose` 与 `reference_pack_intent.role` 都是 `CANONICAL_MASTER`；每个 supplemental work 使用非 canonical 角色并把 `lineage_root.source_work_item_id` 指向 canonical。需要用 canonical 图像继续生成的工作使用 `REFERENCE_GENERATE` 或 `EDIT`，并同时声明 canonical 的 dependency 和 authoritative `ordered_references`；依赖顺序本身不会把图像传给 Provider。角色正面/侧面/背面分别使用 `CHARACTER_BODY`、`CHARACTER_PROFILE`、`CHARACTER_BACK` 等合法角色，不发明新的枚举。

计划中的 `REFERENCE_GENERATE` 声明带参考的制作意图，不自动选择实际目标父图。Builder 将它交给 `image.enqueue` 时，没有目标父图就省略 `parent_version_id`，允许向已有 Asset 新增候选；明确指定已有版本作为目标父图时，实际操作映射为 `EDIT`。同一参考可以提供身份而不是目标 parent；不要仅因 canonical dependency 或 primary input 存在就要求填写 parent，也不需要为这项执行映射重写整体计划。

精确文字层与物理外壳是不同媒体职责，不要求不同资产对象：已有物理 PROP 的表面准确字版可直接由 image.compose_text_plate 保存为同Asset的PHYSICAL_SURFACE_CONTENT候选，保留母资产用途与current/canonical；有独立管理内容的实际需要时再分内容Asset，不重复创建载体。物理 PROP master 使用 `presentation_mode=PHYSICAL_MASTER`；每个独立上屏文案使用独立 item，通常可用 `asset_kind=REFERENCE`，brief 使用 `presentation_mode=SCREEN_CONTENT_PLATE`、`reference_strategy=LIGHTWEIGHT`、`layout_profile=LIGHTWEIGHT_SINGLE`，其唯一 work item 使用 `operation=TEXT_PLATE`、`text_plate_intent`，`output_view` 留空。已有 item 即使保留语义上的 PROP kind，只要它只有纯 TEXT_PLATE、明确是 SCREEN_CONTENT_PLATE 且不混有实体生成 work，就已经完成分层，不要为它虚构物理载体或重复拆分。不要发明 `GRAPHIC`、`TEXT_PLATE` view 或把 text_plate 写到错误字段。两处不同的菜价、文件页或手机画面不合并成一张需要后续裁切的大板。`title` 只声明一次，`exact_lines` 不再重复标题；计划意图保留 required_visible_text 的准确词句和顺序，空白留白不是文案。实际本地排版的空行、行末标签冒号与尾随装饰线不要求修改资产事实；精确位置由 Builder 调用 `image.compose_text_plate.text_blocks/lines` 执行，不靠描述猜布局，不能为排版新增姓名或改变金额、签名状态。没有 AssetPlan 时直接从当前 Asset brief 制作，不为使用这些排版参数强造计划。

同一已存在字版只增加姓名等可见内容时，`text_plate_intent` 可直接用 `base_version_id`、`append_text_blocks` 与完整 `content_state`，复用真实字版尺寸和原布局，不要求 Builder 重填全部文字块与横线；`parent_version_id` 本身不继承版式。明确状态沿同一物理 Asset 追加候选，不为每次签名创建假载体或改共享 brief。新正文必须是本次明确声明的完整状态内容，实际 PNG 仍需按用途观察。
