# 类型补充：通用视频重点

以下十步与共享 `end_to_end` 阶段对齐，描述通用视频在各阶段应补充的判断。它是 Agent 可重排、合并和跳过的制作方法，不是另一套固定工作流。

1. **锁定观看目标、来源与修改范围**

   - **输入：** 用户目标、来源剧本或事实、已有素材、当前 `ProjectDocument`、交付场景与不可修改项。
   - **决策：** 明确作品是要叙事、说明、记录、营造氛围、展示表演还是完成其他任务；区分已确认决定、Agent 可提案内容和未知信息。
   - **可调用 Tool：** `project.list`、`project.get`、`project.create`、`planning.advance`；局部修改先用 `change.preview`。
   - **产物：** 生产目标、来源边界、观看体验、交付规格、锁定项和本轮工作范围。
   - **检查与返工：** 若不同目标会导致实质不同的结构或媒介选择，先补齐真正必要的信息；若现有项目已经回答，不重复询问用户。

2. **形成与目标匹配的内容结构**

   - **输入：** 生产目标、受众已有认知、来源材料、目标时长和已批准 ScriptVersion。
   - **决策：** 选择适合的组织方式，例如因果叙事、主题段落、问题—解释、观察进程、视觉联想或混合结构；不默认补充冲突、反转或对白。
   - **可调用 Tool：** `planning.advance` 推进长内容；`script.revise_lines` 修改明确台词；语义变化用 `change.preview`、`change.apply`。
   - **产物：** 能映射到场景、段落、可见行动、旁白或信息节点的当前 ScriptVersion。
   - **检查与返工：** 检查每一段是否服务声明目标、来源事实是否被保留、目标时长是否可承载；内容问题回到本步，镜位和生成问题不要求编剧代偿。

3. **建立媒介方案、Bible 与必要资产**

   - **输入：** 当前剧本、用户素材、期望风格、已存在的 Asset/AssetVersion 和可复用目录。
   - **决策：** 确定真人、动画、图形、档案素材、屏幕内容或混合媒介如何分工；只建立实际镜头会使用的角色、场景、道具、声音和风格锚点。
   - **可调用 Tool：** `artifact.create_asset`、`artifact.search_catalog`、`artifact.import_catalog`、`artifact.search_stock`、`artifact.import_stock`、`artifact.import_local_version`、`artifact.import_url_version`、`artifact.inspect_coverage`、`artifact.review_version`、`artifact.add_reference_entry`。
   - **产物：** 与剧本一致的 Bible、资产清单、批准版本、来源与用途说明。
   - **检查与返工：** 缺少会阻碍当前镜头的媒体时才导入或生成；风格、事实、身份或授权来源不匹配的候选不进入当前参考绑定。

4. **把内容转换为最小充分的场景与镜头**

   - **输入：** ScriptVersion、Bible、资产状态、观看媒介和目标节奏。
   - **决策：** 为每镜说明叙事、信息、表演、氛围、节奏或空间作用；选择长镜、固定镜、覆盖镜头、图形段落或素材组合，不为“更像视频”机械增加切镜。
   - **可调用 Tool：** 专业镜头规划/导演 Agent；`shot.update_reference_plan`、`shot.preview_reference_plan`、`shot.compile_keyframe_brief`、`shot.director_advice`。
   - **产物：** 包含开场与终态、可见主体、动作、构图、相机、声音、时长和转场关系的 ShotPlan。
   - **检查与返工：** 镜头集合应完整表达意图且能进入剪辑；若一个镜头承载过多互相冲突的动作或媒介任务，则拆镜或重设覆盖，而非直接交给生成模型猜测。

5. **按主要风险选择参考与生成路线**

   - **输入：** ShotPlan、批准 AssetVersion、关键帧候选、已有 Take 和当前 Provider Capability。
   - **决策：** 探索性内容可从纯文本开始；身份或开场构图重要时使用首帧；终态也必须到达时考虑首尾帧；复用特征更重要时使用参考路线；已有合格 Take 时直接复用。
   - **可调用 Tool：** `qa.rerank_reference_visuals`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`route.h3_capability_profile`、`route.plan_h3`、`route.list_h3_plans`。
   - **产物：** 每镜的路线、参考职责、候选策略、检查重点和必要回退。
   - **检查与返工：** 若参考间的身份、媒介、风格、空间或状态冲突，先减少、替换或合成输入；模型限制只从 Tool 读取，不改写成类型惯例。

6. **准备可执行输入并做生成前检查**

   - **输入：** 已选路线、参考计划、关键帧说明和缺失媒体列表。
   - **决策：** 只补齐路线真正需要的起点、终点、构图、身份、空间或声音输入；确认每份媒体承担单一清楚职责。
   - **可调用 Tool：** `shot.compile_keyframe_brief`、`image.enqueue`、`image.advance`、`image.retry`、`image.recover_output`、`artifact.review_version`、`qa.list_visual_input_reports`、`qa.check_input_compatibility`。
   - **产物：** 可读取、版本明确、用途清楚且彼此兼容的生成输入包。
   - **检查与返工：** 若关键帧本身已违背内容、构图或连续性，先修复图像或镜头；非阻断的美化项不应让整个项目停在准备阶段。

7. **忠实编译 Prompt 并生成候选**

   - **输入：** ShotPlan、ReferencePlan、批准媒体、当前路线和 Provider Capability。
   - **决策：** Provider Prompt 只翻译已确定的内容、动作、摄影机、声音和媒体绑定；候选数量根据不确定性和已有结果动态决定。
   - **可调用 Tool：** `route.compile_h3_prompt`、`route.preview_h3_probe`、`route.execute_h3_plan`、`video.enqueue`、`task.advance`、`task.drain`、`task.reconcile`、`task.recover_output`。
   - **产物：** 可追溯的 GenerationTask、实际 Provider 请求和一个或多个 Take。
   - **检查与返工：** 首个候选出现后先观察再决定是否继续。若错误来自输入、镜头或内容，应先改变正确控制变量，不用相同请求固定次数抽卡。

8. **依据作品意图检查、比较与局部修复**

   - **输入：** 候选 Take、相邻镜头、项目声明的评价维度和生成任务记录。
   - **决策：** 先检查文件与技术状态，再判断内容、表演、信息、氛围、节奏、风格、空间和连续性是否完成本镜职责；允许所有候选都不可用。
   - **可调用 Tool：** `video.inspect_take`、`video.compare_takes`、`video.select_take`、`route.review_h3_take`、`shot.director_advice`、`shot.apply_director_repair`、`video.retake`、`video.extend`、`video.gap_fill`。
   - **产物：** 选定 Take、拒绝理由、问题责任层和最小修复决定。
   - **检查与返工：** 内容错误回剧本，构图/覆盖错误回 ShotPlan，身份/风格错误回资产或关键帧，随机生成缺陷才重生；有意静默、跳切或有限运动不因偏离常规而被误判。

9. **组装声音、字幕、图形与时间线**

   - **输入：** 选定 Takes、锁定文本、声音和图形资产、入出点及交付规格。
   - **决策：** 决定对白、旁白、环境声、音乐、字幕、图形和转场如何共同承载目标；保留作品需要的停顿与空间，不为填满时间持续加声画刺激。
   - **可调用 Tool：** `timeline.rebuild`、`audio.update_voice_reference`、`audio.inspect_manifest`；只使用能力目录中真实注册的其他后期 Tool。
   - **产物：** 可重建的 TimelineManifest、字幕/图形布局和音频关系。
   - **检查与返工：** 检查镜头边界、声音来源、字幕忠实度、图形可读性和节奏；能在后期修复的问题不回退整镜生成，素材本身错误才回到对应责任层。

10. **按声明目标完成整片验收与导出**

   - **输入：** 当前 TimelineManifest、最新 ScriptVersion/BibleVersion、选定 Takes 和交付要求。
   - **决策：** 从完整作品判断目标是否兑现，并区分阻断问题、可选优化与有意风格选择。
   - **可调用 Tool：** `qa.review_episode`、`timeline.export`；发现问题时用 `change.preview` 或对应资产、镜头、视频 Tool 建立最小返工。
   - **产物：** 整片审看结果、当前 ExportRecord 和仍存在的非阻断建议。
   - **检查与返工：** 导出必须对应最新时间线且满足画幅、编码和内容目标；修复后只复核受影响片段及相邻边界，整个用户目标满足后才结束。
