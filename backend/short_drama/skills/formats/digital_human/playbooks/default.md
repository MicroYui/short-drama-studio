# 类型补充：数字人口播重点

以下十步与共享 `end_to_end` 阶段对齐，说明数字人口播在每一步需要增加的输入、判断和检查。Agent 可根据已有文案、音频、数字人素材和用户修改范围跳过或重排步骤。

当前可用且明确选用的媒体可直接使用；检查结论按实际观察记录，不增加批准标记或逐阶段确认。后期以项目当前编辑时间线为准，精确修改保护已有裁切、字幕与声音，不为同步流程重建旧 Manifest。

1. **锁定沟通目标、事实来源与主播关系**

   - **输入：** 用户目标、来源文档、品牌或产品事实、目标观众、语言、发布场景、现有 `ProjectDocument` 和不可修改项。
   - **决策：** 明确观众看完应理解、相信、记住或执行什么；确定主播是讲解者、专家、品牌代表、主持人还是故事人物，以及表达关系需要何种正式度和亲近感。
   - **可调用 Tool：** `project.list`、`project.get`、`project.create`、`planning.advance`；局部需求先用 `change.preview`。
   - **产物：** 沟通目标、事实清单、禁改项、主播定位、语言/声线要求和交付规格。
   - **检查与返工：** 来源主张存在冲突或关键数字不明时先标记未知，不由 Agent 补造；项目已有答案时直接继续。

2. **把内容改写为可听懂、可朗读的口播稿**

   - **输入：** 已确认事实、受众知识水平、目标时长、用户原稿和现有 ScriptVersion。
   - **决策：** 组织开场话题、段落推进、示例/证据、重点回收和行动指令；按语义、换气、重音和发音难度调整句子，但不改变事实与承诺。
   - **可调用 Tool：** `planning.advance` 处理长稿；`script.revise_lines` 修改指定语句；涉及论点或后续画面时用 `change.preview`、`change.apply`。
   - **产物：** 带说话人、段落、停顿意图、专有名词读法和强调位置的当前 ScriptVersion。
   - **检查与返工：** 逐句按口语朗读思路检查指代、换气、数字和专名；过密段落优先重组或增加视觉解释，不机械加快语速。

3. **建立主播、声线、背景与辅助资产**

   - **输入：** 主播定位、已有人像/数字人/声音参考、品牌视觉、背景需求、图表、演示素材和 B-roll。
   - **决策：** 分开管理主播身份、服装造型、声音身份、背景、产品/演示物、图形和补充画面；只准备当前稿件真正需要的素材。
   - **可调用 Tool：** `artifact.create_asset`、`artifact.update_brief`、`artifact.search_catalog`、`artifact.import_catalog`、`artifact.import_local_version`、`artifact.import_url_version`、`artifact.inspect_coverage`、`image.enqueue`、`image.compose_reference_sheet`、`qa.inspect_asset_version`、`artifact.review_version`、`artifact.add_reference_entry`、`audio.update_voice_reference`。
   - **产物：** 主播与声音的可用版本、背景/图形/B-roll 清单以及每项资产的用途。
   - **检查与返工：** 人脸、服装、声线、品牌样式或产品外观不一致时先修复资产；没有表达作用的 B-roll 不进入生产清单。

4. **按自然语音边界设计表演段与镜头**

   - **输入：** 口播稿、主播和声音基准、辅助资产、目标画幅与观看设备。
   - **决策：** 以语义段落、呼吸、情绪和视觉说明需求拆分 ShotPlan；决定何时保持主播、何时使用推近/景别变化、图形叠加、屏幕演示或 B-roll。
   - **可调用 Tool：** 数字人导演/镜头规划 Agent；`shot.update_reference_plan`、`shot.preview_reference_plan`、`shot.compile_keyframe_brief`、`shot.director_advice`。
   - **产物：** 每段包含锁定台词、语音意图、目光、表情、动作、构图、辅助画面、开场/终态和转场的 ShotPlan。
   - **检查与返工：** 切换必须改善理解、证明或节奏；若手势、图形和 B-roll 同时争夺注意，减少元素或重新分工。

5. **选择声音、口型和画面生成路线**

   - **输入：** ShotPlan、主播/声音参考、已存在音频或 Take、Provider Capability 和交付质量要求。
   - **决策：** 判断当前能力适合联合音画、先确认语音再驱动画面、参考图驱动或复用已有 Take；身份、声音、口型、动作与背景分别由哪类输入负责必须明确。
   - **可调用 Tool：** `qa.rerank_reference_visuals`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`route.h3_capability_profile`、`route.plan_h3`、`route.list_h3_plans`。
   - **产物：** 每段的生成路线、参考职责、音频基线、候选策略、检查重点与回退方案。
   - **检查与返工：** Tool 未注册独立配音或口型能力时明确记录缺口，不用 Prompt 假装可用；易变参数只从 Tool Schema/Capability 获取。

6. **先确认语音内容与主音频意图，再准备兼容输入**

   - **输入：** 锁定台词、读音与韵律要求、选定路线、主播关键帧、背景和图形素材。
   - **决策：** 在产生依赖口型的候选前锁定本轮使用的文字/音频版本；只准备稳定身份、构图、视线、服装和背景所需的最小媒体。
   - **可调用 Tool：** `audio.update_voice_reference`、`shot.compile_keyframe_brief`、`image.enqueue`、`image.advance`、`image.retry`、`qa.inspect_asset_version`、`artifact.review_version`、`qa.check_input_compatibility`、`qa.list_visual_input_reports`。
   - **产物：** 版本明确的台词/声音基线和可执行的主播画面输入包。
   - **检查与返工：** 发音、数字、专名、语速或停顿不成立时先修正上游；主播关键帧的嘴型、视线或构图错误时先修图，不要求视频模型自动补救。

7. **编译表达指令并生成可比较候选**

   - **输入：** 当前台词/音频版本、ShotPlan、ReferencePlan、可用媒体和当前路线。
   - **决策：** Prompt 忠实表达说话人、台词、情绪、表情、目光、身体动作、摄影机和背景，不自行增加新主张；候选数量按口型、身份和表演不确定性动态决定。
   - **可调用 Tool：** REAL_H3 可用 `route.prepare_h3_group` 组合准备、编译与提交；需要逐项控制时使用 `route.compile_h3_prompt`、`route.preview_h3_probe`、`route.execute_h3_plan`、`task.advance`、`task.recover_output`；MOCK 使用 `video.enqueue`、`task.drain`、`task.reconcile`。
   - **产物：** 可追溯 GenerationTask 与一个或多个数字人口播 Take。
   - **检查与返工：** 第一条候选出现后立即抽查台词、声音、身份和口型。输入版本错误先停止后续候选；随机表演缺陷才考虑同路线再生成。

8. **检查信息、声音、口型与表演**

   - **输入：** 候选 Take、锁定稿、声音基线、主播资产和相邻段落。
   - **决策：** 分开评估事实/台词是否正确，语音是否清楚自然，身份是否稳定，口型是否可信，目光/表情/动作是否支持语义，以及辅助画面是否有用。
   - **可调用 Tool：** 共用 `video.inspect_take`、`video.compare_takes`、`video.select_take`、`route.review_h3_take`、`shot.director_advice`、`shot.apply_director_repair`；MOCK 才使用 `video.retake`、`video.extend`、`video.gap_fill`，REAL_H3 回到 H3 路线计划。
   - **产物：** 选定 Take、逐维观察、拒绝理由和最小修复方案。
   - **检查与返工：** 错词/事实回稿件，发音与韵律回声音基线，构图/手势回导演，身份回资产，随机口型或画面缺陷才重生；后期能修的停顿或入出点留给时间线。

9. **完成字幕、图形、B-roll、混音与时间线**

   - **输入：** 选定 Takes、锁定显示文本、图形/B-roll、音乐与音效需求、交付画幅。
   - **决策：** 按语义和呼吸断字幕；在不打断表达的前提下安排图形与 B-roll；统一对白可懂度、响度、环境声和段间停顿。
   - **可调用 Tool：** `timeline.read_editorial`、`timeline.edit_clips`、`timeline.save_editorial`、`audio.inspect_manifest`、`audio.update_voice_reference`；其他后期能力仅在 Tool Catalog 已注册时使用。
   - **产物：** 当前编辑时间线、字幕/图形布局、辅助画面入出点和音频关系。
   - **检查与返工：** 字幕不得改写事实或遮挡嘴部/手势/演示物；B-roll 与口播不一致时替换画面，不修改正确台词迁就素材。

10. **完成整片沟通效果验收与导出**

   - **输入：** 当前已保存编辑时间线、最新口播稿、主播/声音基准和交付要求。
   - **决策：** 从完整观看判断信息是否准确易懂、主播是否可信、形象和声线是否连续、口型是否可接受、辅助画面是否真正帮助理解。
   - **可调用 Tool：** 按需使用 `qa.review_episode`；当前剪辑使用 `timeline.export_editorial`、`timeline.read_export`；发现问题时用 `change.preview` 或相应稿件、资产、镜头、视频 Tool 建立局部返工。
   - **产物：** 整片审核记录、当前 ExportRecord 和可选优化项。
   - **检查与返工：** 局部改词后只重做依赖该台词/音频的段落并复核相邻剪辑点；导出对应最新时间线且满足内容和技术规格后才结束。
