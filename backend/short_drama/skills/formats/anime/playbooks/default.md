# 类型补充：动画制作重点

以下十步与共享 `end_to_end` 阶段对齐，说明动画叙事在各阶段需要额外维护的视觉语言、关键姿势和风格连续性。Agent 可按项目媒介、已有设定和修改范围跳过、合并或并行步骤。

1. **锁定故事目标、动画媒介与视觉边界**

   - **输入：** 用户目标、来源剧本/设定、参考作品或素材、目标受众、交付画幅、当前 `ProjectDocument` 和锁定项。
   - **决策：** 明确二维、三维、漫画动态化、定格感、图形动画或混合媒介的关系；区分必须保持的原作事实与 Agent 可设计的表现方式。
   - **可调用 Tool：** `project.list`、`project.get`、`project.create`、`planning.advance`；局部修改先用 `change.preview`。
   - **产物：** 故事目标、媒介声明、视觉方向、来源边界、交付规格和本轮范围。
   - **检查与返工：** 若参考之间在线条、比例、材质或运动语言上冲突，先确定主次；已由项目工件确认的选择不重复询问。

2. **形成适合动画表达的剧本与视觉节拍**

   - **输入：** 故事目标、角色与世界设定、目标时长、来源文本和当前 ScriptVersion。
   - **决策：** 把场景写成可见行动、表情、姿势、构图变化和声音节拍；决定哪些信息由对白、动作、画面变形、蒙太奇或环境变化表达。
   - **可调用 Tool：** `planning.advance`；局部台词用 `script.revise_lines`；改变角色、设定或后续状态时用 `change.preview`、`change.apply`。
   - **产物：** 含场景目的、角色状态变化、关键动作/反应、对白和视听节拍的 ScriptVersion。
   - **检查与返工：** 若场景只能依赖解释性对白或不可见设定成立，先修内容；若问题只是构图、姿势或生成能力，留给后续步骤处理。

3. **建立动画 Bible、角色设定与风格资产**

   - **输入：** 当前剧本、用户设定图、角色/场景/道具/风格资产和可复用目录。
   - **决策：** 固定世界规则、角色轮廓和比例、脸型发型、服装、色板、线条、材质、背景、光影、道具状态与运动风格；将 canonical 设定与单镜关键帧分离。
   - **可调用 Tool：** `artifact.create_asset`、`artifact.search_catalog`、`artifact.import_catalog`、`artifact.import_local_version`、`artifact.import_url_version`、`artifact.inspect_coverage`、`artifact.review_version`、`artifact.add_reference_entry`、`image.enqueue`。
   - **产物：** ProjectBibleVersion、批准的角色/场景/道具/风格 AssetVersion 和用途明确的 ReferencePack。
   - **检查与返工：** 只补齐实际镜位所需视角和状态；临时镜头图不能未经审核覆盖角色造型基准，有意变形或换装需记录触发条件。

4. **用布局、姿势和关键动作设计镜头**

   - **输入：** ScriptVersion、动画 Bible、批准资产、视觉节拍和目标画幅。
   - **决策：** 为每镜确定画面作用、布局、景别、角色轮廓、姿势、表情、视线、关键动作、停顿、相机/画面运动、开场与终态；决定采用完整运动、有限动画、循环或静止保持。
   - **可调用 Tool：** 动画导演/镜头规划 Agent；`shot.update_reference_plan`、`shot.preview_reference_plan`、`shot.compile_keyframe_brief`、`shot.director_advice`。
   - **产物：** 连接剧情、设定、关键姿势、运动段落、声音和转场的 ShotPlan。
   - **检查与返工：** 角色轮廓和动作意图应在目标尺寸可读；复杂动作若包含多个接触、变形或方向变化，应拆成可控节拍或镜头，不盲目追求一镜完成。

5. **按身份、风格和运动风险选择路线**

   - **输入：** ShotPlan、批准设定、布局/关键帧候选、已有 Take 和 Provider Capability。
   - **决策：** 低连续性探索可用纯文本；角色造型和开场布局重要时使用首帧；明确姿势/构图终态时考虑首尾帧；风格、角色或运动特征复用更重要时选择参考路线；合格旧 Take 优先复用。
   - **可调用 Tool：** `qa.rerank_reference_visuals`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`route.h3_capability_profile`、`route.plan_h3`、`route.list_h3_plans`。
   - **产物：** 路线、每份参考的职责、关键帧策略、候选范围、检查重点和回退方案。
   - **检查与返工：** 参考在脸型、比例、服装、色板、线条、空间或道具状态上冲突时先处理输入；Provider 限制以 Tool 为准，不变成动画风格规则。

6. **准备布局帧、关键姿势与生成输入**

   - **输入：** 路线计划、角色/场景设定、动作节拍、关键帧 brief 和缺失媒体。
   - **决策：** 生成或编辑当前镜头必要的布局、首帧、尾帧、关键姿势或合成参考；让身份、构图、风格、空间和运动职责清楚分离。
   - **可调用 Tool：** `shot.compile_keyframe_brief`、`image.enqueue`、`image.advance`、`image.retry`、`image.recover_output`、`artifact.review_version`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`qa.list_visual_input_reports`。
   - **产物：** 已审核的镜头关键帧/布局、ReferencePlan 和兼容输入包。
   - **检查与返工：** 起始姿势、轮廓、比例、手部/道具接触、场景透视或终态错误时先修图；不要求视频阶段从错误关键帧恢复。

7. **编译动画表演指令并生成候选**

   - **输入：** ShotPlan、动作节拍、ReferencePlan、批准媒体和当前路线。
   - **决策：** Prompt 表达角色身份、姿势变化、表情、动作时序、画面/相机运动、风格和声音，不自行增加镜头或改变设定；候选数量按高风险维度动态决定。
   - **可调用 Tool：** `route.compile_h3_prompt`、`route.preview_h3_probe`、`route.execute_h3_plan`、`video.enqueue`、`task.advance`、`task.drain`、`task.reconcile`、`task.recover_output`。
   - **产物：** 可追溯 GenerationTask、实际请求和一个或多个动画 Take。
   - **检查与返工：** 首个候选出现后先看造型、动作路径和背景稳定性；若问题来自关键帧或冲突参考，先修输入，再决定是否继续生成。

8. **生成与修复时持续保护角色造型，并区分风格选择与真实缺陷**

   - **输入：** 候选 Take、动画 Bible、关键帧、相邻镜头和项目声明的运动风格。
   - **决策：** 检查角色造型、比例、服装、场景、道具、线条、色彩、动作可读性、表情、背景稳定、合成和音画；允许有意跳帧、保持帧、循环和夸张变形。
   - **可调用 Tool：** `video.inspect_take`、`video.compare_takes`、`video.select_take`、`route.review_h3_take`、`shot.director_advice`、`shot.apply_director_repair`、`video.retake`、`video.extend`、`video.gap_fill`。
   - **产物：** 选定 Take、逐维观察、风格决定、真实缺陷与最小修复策略。
   - **检查与返工：** 故事问题回剧本，布局/动作问题回 ShotPlan，造型/风格问题回资产或关键帧，模型随机漂移才重生；只有局部时间段失败时优先局部替换。

9. **完成对白、音效、音乐、字幕与合成节奏**

   - **输入：** 选定 Takes、锁定对白、角色声线、动作落点、音乐/音效、字幕和图形需求。
   - **决策：** 让对白、音效、音乐和停顿跟随动作与情绪节拍；决定字幕、拟声字、图形和转场如何融入既定画面语言。
   - **可调用 Tool：** `audio.update_voice_reference`、`timeline.rebuild`、`audio.inspect_manifest`；其他合成/后期能力只在 Tool Catalog 已注册时使用。
   - **产物：** 可重建 TimelineManifest、声音与动作关系、字幕/图形布局和镜头边界。
   - **检查与返工：** 检查对白身份、声音来源、动作落点、字幕遮挡、闪烁和色彩/合成连续性；能由时间线修复的问题不重做完整动画镜头。

10. **从完整作品验收视觉语言与叙事并导出**

   - **输入：** 当前 TimelineManifest、最新 ScriptVersion/BibleVersion、选定 Takes 和交付要求。
   - **决策：** 判断故事是否可理解、角色表演是否成立、视觉语言是否统一、风格化是否有意且一致、角色/场景/道具和音画是否连续。
   - **可调用 Tool：** `qa.review_episode`、`timeline.export`；发现问题时使用 `change.preview` 或对应剧本、资产、镜头、视频 Tool 建立最小返工。
   - **产物：** 整片审核记录、当前 ExportRecord、系列连续性状态和可选优化项。
   - **检查与返工：** 修复后只复核受影响镜头和相邻边界；导出必须对应最新时间线，且真正阻断的问题解决后才结束。
