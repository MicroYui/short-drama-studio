# 当前实现质量审查：六条端到端制作链

记录日期：2026-09-11。范围是当前源码与《午饭时间》真实运行记录；没有调用 Provider、启动服务或修改运行代码。本文补充 [现状诊断](01-current-diagnosis.md)，不重新统计模型轮数，也不把“注册了某个 Tool”当作能力已经有效交付。

## 结论与判定口径

当前最值得做的不是继续堆工具，而是统一已有工具实际使用的**内容范围、媒体来源、时间线和修复目标**。视频观察已经能给出有时间位置的有效问题；问题在于参考图过滤、比较上下文、修复落点和后期保存路径没有全程一致，Agent 因而要靠长篇交接、重新编译和手工转写补齐。

- **明确缺陷**：当前可达代码直接存在输入/输出不一致；不等于本案例一定触发过。
- **设计弱点，需验证**：静态机制可解释风险，但尚不能从本次记录证明导致了多少耗时或质量损失。
- **已修复/保留**：当前代码或本轮后期记录已证明旧问题不再成立，不重复列为待开发能力。
- 真实记录只读 `output/studio-launch-2026-r2/runtime/short-drama.sqlite3`，按 WorkOrder ID + specialist_turn.sequence 定位；没有把重复状态快照计为语言调用。

| 链路 | 当前结论 | 处理方向 | 优先级 |
| --- | --- | --- | --- |
| 具体参考/关键帧 → H3 输入 | 生成输入已支持明确选中的 DRAFT；视频语义检查仍过滤这些实际绑定图；输入兼容性检查仍以单镜为对象 | 保留生成契约，统一观察用图；将检查范围对齐 GenerationUnit | P1 |
| 视频观察 → 候选比较 | 观察已支持整组且能定位真实缺陷；比较仍只给单镜计划，复用报告不区分关注范围 | 保留观察内核，重写比较上下文与报告复用条件 | P1 |
| 导演修复 → 实际局部重生成 | 导演工具修改旧投影；真实 H3 仍从 managed Storyboard 取组/时窗；所谓 local rework 是 Mock | 统一修复保存入口，真实 H3 采用受影响生成组重生成 | P1 |
| 声音/字幕 → 当前时间线 | 声音检查已读当前编辑结果；字幕对齐仍改旧 Manifest，当前导出不消费它 | 改写对齐的读写目标，复用本地 ASR 和当前剪辑适配 | P1 |
| Leader/Worker → 信息与完成 | 最新要求传递、持久化等待已有修复；schema 丢失、厚读取、批次等待仍抵消收益 | 保留角色模型，压缩确定性往返，按结果到达继续编排 | P1 |
| Skill 加载 → 实际专业指导 | 已按角色/阶段拆分；同一次 Leader 请求仍包含互相冲突的分工要求；嵌套导演工具没有带入已选方法 | 合并运行协议，按工具真正输入修订专业说明 | P1/P2 |

## 1. 具体参考、关键帧与 H3 输入

### 已经有效的部分

当前真实 H3 输入链为 `route.get_generation_unit_context / route.compile_h3_prompt → _h3_generation_unit_sources → _current_generation_plan_for_video → validate_generation_units_current`，随后校验本组明确生成引用和 Provider 媒体契约。编译读取全部 `unit.shot_ids`、实际 `shot_windows`、逐镜正文和对白；不是只把第一个镜头送给模型。[输入入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:10800) · [本组验证](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/generation_plan_state.py:15) · [逐镜编译上下文](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:463)

`resolve_current_asset_media` 明确允许正文已选中的 CURRENT/DRAFT 图片，随后 `assert_generation_references` 核对本组引用。这个规则应保留；不要为了让其他检查通过，再要求用户或 Agent补批准标记。[媒体真相](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/asset_reference_truth.py:20) · [执行前引用校验](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:12215)

`shot.compile_keyframe_brief` 是确定性编译，已经能表达 START/END/TOPOLOGY、从哪一动作节拍后开始、主画布、身份参考分工、左右与接触关系；不在内部另叫语言模型。它的主要问题不是“缺少关键帧工具”。[关键帧编译](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/reference_planner.py:709)

### Q1：视频观察使用的实际参考图，与生成所用图片不一致——明确缺陷，案例数据适用

`video_inspection._semantic_reference_images` 先把 `ReferencePlan.bindings` 指向的 AssetVersion 加入候选，再补 canonical approved 图片；**随后对两者一起执行 `approval_status == APPROVED` 筛选**。因此不是只限制 approved 兜底集合，而是会过滤显式绑定的 DRAFT 图。`video.inspect_take` 和 `video.compare_takes` 都消费这个函数。[参考收集与统一过滤](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/video_inspection.py:119) · [整组检查调用](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/video_inspection.py:670) · [比较调用](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/take_comparison.py:110)

真实项目当前 `shot-lunch-01`、`shot-lunch-15`、`shot-lunch-16` 各有 8 个去重 GENERATION 图片引用，其中 2 APPROVED、6 DRAFT；ReferencePlan.bindings 中也实际存在对应 DRAFT 版本。检查模型因此无法同时看到这 6 张已用于生成的身份/道具参考。它仍能根据影片看出空盘等问题，但不能声称已完整比对全部选用资产。

**建议：改写共同参考解析器，保留工具名称。** 检查首先使用本次 Take/生成组真实输入与明确使用的媒体，并保留用途、对象和窗口；可选 canonical 图作为补充，不能替代本次选择。CURRENT、文件存在与引用匹配继续验证，DRAFT 不再作为过滤理由。验收用现有项目纯本地读取即可：8 个已选版本出现在模型 image_map 中，EDITORIAL 图版不混成身份/首帧参考。

### Q2：输入兼容性工具没有覆盖真实生成组——明确接口不足，误判量需验证

`qa.check_input_compatibility` 的 service 入口只接受一个 `shot_id`，取得一个 ShotPlan 和一个 ReferencePlan。内部 payload 只有 `shot/reference_plan/route/review_focus/image_map`，没有 GenerationUnit、其余镜头、组内时窗，也没有实际编译后 H3 输入标识。[service 入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:11448) · [检查 payload](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/visual_input_quality.py:710)

真实 `opening_video_compiler`（`work-order-76f054d353d64d12b22835cdf0b556a0`）的连续链：

| turn | 实际动作 | 结果与含义 |
| --- | --- | --- |
| 3–4 | 编译生成计划，读取 cg-lunch-01 | 上下文已经知道是镜 01–03 的整组 |
| 5 | 对 shot-lunch-01 检查 8 张图，靠 review_focus 补写整组意图 | 语言输出因 max_output_tokens 失败 |
| 6–7 | 拆成两次各 4 图，再用长 review_focus 逐图解释用途 | 一份 NEEDS_REVIEW：林一图不可查看；一份 COMPATIBLE，但只确认部分参考用途 |
| 8–9 | 再编译生成计划、再读同组 | 检查报告本身不会直接形成后续输入绑定 |
| 10–12 | H3 Prompt 编译三次 | 两次超 7,000 字符，第三次成功 |

这不是用户交互次数。前面的语义判断有必要，反复抄写图序、用途、整组边界是接口缺少既有事实造成的机械负担。两份分图报告也不能自动等同“8 张图联合且全组兼容”。

**建议：合并检查上下文准备与当前生成组绑定读取。** 请求给 GenerationUnit/候选输入引用即可，工具从现有保存内容构造精确图片角色和时窗；输出逐输入观察、受影响 shot/window、可执行建议和未覆盖项。保留用户可覆盖建议，不增加必经审核。历史 8 图输出截断不直接断言仍存在：当前视觉工具已给 16,000 输出额度，需复用 Fake/既有记录检验新的输入长度和结果覆盖。[当前额度](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/visual_input_quality.py:51)

## 2. 视频观察到候选比较

### 已修复：整组观察、稀疏采样和声音能力边界

当前 `VideoInspector._semantic_review` 按 `take.shot_windows` 装载所有 covered shots，显式标记 GENERATION_UNIT，要求按整组总时长和分段意图观察；同时明确静帧不能验证对白、声线与同步。[整组与音频说明](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/video_inspection.py:640)

最后一次结尾观察确实输出了可操作证据：`video-inspection-b0ae6757709149789b53101f8f36fb29` 识别开头端盘状态重置、校长相对位置变化、重复添菜及离场构图，并给 0.050–0.400、1.603–2.404、8.655–8.976 等时间。它把剪辑区间称为视觉候选，音频标 UNKNOWN。这部分应保留，不能称为无效的“泛泛评分”。

### Q3：比较工具仍按单镜理解整组候选——明确缺陷，当前案例未实际调用比较

`service.compare_takes` 取得一个请求 shot，最终要求每个 Take 的 `take.shot_id == shot.id`。而 `repository.list_takes_for_shot` 已按 `covered_shot_ids()` 查到覆盖该镜头的组片：例如比较镜 16 的结尾组，查询能找到代表镜为 15 的 Take，随后 service 又拒绝它。[覆盖检索](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/repository.py:1199) · [service 检查](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13569)

即使以代表镜 15 发起比较通过，`compare_take_candidates` 传入的 payload 仍只有一个 `shot`，没有整组 `current_shots/shot_windows/generation_unit`；每个候选却展示整段接触表。它无法像当前视频观察一样准确解释 15–18 镜各自目标，且只收代表镜 ReferencePlan 的参考图。[比较上下文](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/take_comparison.py:124)

此外比较指令要求比较“台词/声源语义”，输入只有图片和观察报告，未带真实音频报告；虽然附有证据约束，也应明确音频为未提供，不把音频比较隐含交给静帧模型。[比较指令](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/take_comparison.py:117)

本案例 `take_comparisons` 表为 0，不能说这个缺陷已导致最后一次选择错误。**建议：重写比较上下文，复用已经修正的整组观察事实结构；允许从任何 covered shot 解析到对应生成组，并明确本次比较的是整组还是选用片段。** 输出保留 NONE_ACCEPTABLE 和相对优缺点；不给所有候选强制排出冠军。xhigh 下当前比较只给 4,000 输出 tokens，候选多时是否再截断属待验证项，而不是已确认故障。[比较模型调用](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/take_comparison.py:160)

### Q4：已完成语义报告，不等于当前问题已被完整检查——明确缓存/范围设计不足

`compare_takes` 复用某 Take 最新的 `semantic_status=COMPLETE` 报告，不判断它检查的是全组质量、某块价牌、某段边界还是另一套当前意图；`VideoInspectionReport` 没有保存请求 review_focus、已观察时间范围、所用内容修订或完整覆盖条件。[复用选择](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13611) · [报告结构](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/models.py:2661)

真实例子：`EDITORIAL_FINISH`（`work-order-20569c6100774870964096e765649bdd`）turn 7 只检查第一组 0–4.5 秒的图版安全区，turn 42 只为第二组寻找两个文字图版区域；这些都是有效的局部 COMPLETE 报告，但不能替代叙事、身份、连续性的全组检查。反过来旧的全组报告也不能证明后来合成图版正确。

早期 `existing_video_reviewer` 的 turn 5、6 两次 `video.inspect_take` 都返回 Tool SUCCEEDED，但报告 `semantic_status=FAILED/max_output_tokens`，随后 turn 8 才取得成功的缩短观察。这说明工具成功、媒体抽帧成功和语义判断成功必须分开读；当前报告已经保留这种区分，应保留并让下一步明确消费。

**建议：保留抽帧和局部复查，补报告用途、实际输入内容/媒体标识、观察区间与能力覆盖；按请求语义选择可复用报告。** 旧报告仍可作证据，不简单丢弃；但不能只靠“最新且 COMPLETE”推定足够。验收无需新视频：用同 Take 的全组报告和图版局部报告测试比较器选择及缺项提示。

## 3. 导演修复到真正局部重生成

### Q5：导演修复写入旧投影，真实生成组仍取另一套组/时窗——明确静态缺陷，本案例未触发该工具

`shot.apply_director_repair → service.decide_director_repair` 直接修改 `document.shots`、`reference_plan`、必要时 `execution_plan`，并把相关 Takes 标 stale、失效旧 manifests；它没有同步 managed Storyboard 的内容/ClipGroup，也不返回受影响 GenerationUnit 或时间线使用位置。[注册入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/tools/shot/__init__.py:913) · [修复保存](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:12489) · [拆镜](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:15285)

**真实 H3 的关系如下，不能用 Mock `_new_task` 代替证明：**

1. `_h3_generation_unit_sources` 经 `validate_generation_units_current` 消费现有 GenerationPlan；修复增加 shot.revision 后，旧单元首先会因版本不符而拒绝。这是正确的旧输入保护。
2. Agent 再调用 `route.compile_generation_plan` 时，`service.compile_generation_plan` 仍从 managed Storyboard 读组和时窗，只把 `document.shots` 的当前 revision 套进去。[重编入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:7697)
3. `production_contracts.compile_generation_plan` 用 managed Storyboard 的 `shot.duration_seconds`、`group.shot_ids`、`generation_intent/continuity` 建单元；H3 Prompt 又读取 `document.shots` 中修复后的正文。[组/时窗来源](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_contracts.py:3522) · [Prompt 正文来源](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:482)
4. 因此时长修复可能形成“旧窗口 + 新镜头时长/正文”，拆镜会留下 managed Storyboard 引用已被移除的旧镜 ID。简单摄影文字修改不一定阻断生成，不能一概说所有修复必失败；明确问题是保存的内容真相未统一。

当前产品的 `shot.save_current → StudioService.save_shot` 已把镜头正文与 managed Storyboard/ClipGroup 同步，是可复用的正确路径。[当前保存](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_service.py:264)

本案例 `repair_history=0`，WorkOrder 没有调用 `shot.director_advice/apply_director_repair/run_director_loop`。上述工具默认注册、可被选择，但此问题不能算进本次已发生的故障次数。

**建议：合并修复写入与当前保存服务。** typed repair 可以继续存在；应用时生成明确的当前内容修改、受影响组和时间线使用位置，并保护已有可用结果。拆镜/调时通过同一 Storyboard 编辑服务同步，后续只重编受影响组。导演建议本身不直接被等同于已重生成。

### Q6：现有 local rework 不是 H3 局部重生成；嵌套导演也没有真正继承当前方法——边界缺口

`video.retake/extend/gap_fill` 的描述已经注明 Mock，内部最终 `_new_task` 设置 `provider="mock"/model="deterministic-media-v1"`；没有 H3 repair/inpaint 的实现。`shot.run_director_loop` 应用建议后只返回 `next_action="REGENERATE_SHOT"`，不会创建真实 H3 任务。真实 H3 新候选必须走当前 generation plan、Prompt、route、execute 链，并最终由 `_new_h3_task_and_run` 创建普通 GENERATE。[Mock 重工](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:14255) · [Mock Task](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:15141) · [导演 loop](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:12584) · [真实 H3 Task](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:14956)

`shot.director_advice` 内部另外叫一次语言模型，JSON 无效时再叫一次修复模型；`build_director_advice_input` 固定 `DIRECTOR/STORYBOARD`，没有传入 WorkOrder 已选 `method_skill_ids`，因此不加载本轮选中的 `method.drama_series` 等专业补充；还只带单镜、汇总问题文字，不带完整生成组、候选比较引用或真实图像。[嵌套调用](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:11660) · [导演输入](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/reference_planner.py:1100)

**建议：当前产品先用已有 H3 的“受影响生成组新候选 + 精确采用区间”，不要宣称支持供应商没有的局部重绘。** 把输入重编、提交、等待和结果登记作为持久化的确定性动作串，Worker 保留是否改参考/机位/分组的创意决定。导演建议可保留为可选工具，但应接收当前目标、角色方法、生成组、已观察证据与可执行修复面；若没有新的专业信息，不要求 Worker 再调用一个同配置的导演模型来重复自己的判断。

真实 ENDING_VIDEO 链（`work-order-b8965a749797480c9f0cc0f3a958c69d`）turn 52 已看见空盘/站位/重复添菜，turn 53 读当前生成组，54–55 编译再次因 8,904、7,256 字符失败，56 才通过。观察结果确实进入后续修订，并非“没人用报告”；但根因处理仍主要靠 Agent 重写大段 source_prompt。应补一个“本次改了哪一个控制变量、针对哪条观察”的小型修复记录，减少原样重试；不是加一个反复评判的 Critic 循环。

## 4. 字幕、声音与当前时间线

### 已修复：当前声音/整集检查的输入

`audio.inspect_manifest` 已优先调用 `editorial_audio_input`，读取当前已保存剪辑的裁切、速度、倒放、静音、音量及字幕，并记录 `source_editorial_revision`。`qa.review_episode` 也已优先读当前编辑文档；失败阶段现在会跳出，不能继续把旧的隐藏重试循环列成当前问题。[声音入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13672) · [音轨适配](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_audio.py:52) · [整集入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13781)

### Q7：字幕对齐成功仍不会更新用户当前剪辑——明确活跃缺陷

`audio.align_subtitles → align_current_native_subtitles` 与声音检查不同：只读 `_current_manifest(document)`；对齐后追加新 TimelineManifest 并更新 `current_manifest_id`，没有读取或保存 `editorial_timeline`。[实际读写](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13704)

当前导出 `_write_subtitles` 遍历的是保存的 `EditorTimeline.subtitles`。打开编辑器也只有初始化投影会从 legacy manifest 搬入字幕，已有保存内容不会每次重建。[当前导出](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_export.py:113) · [初始化投影](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_editorial.py:175)

Tool 说明却要求“使用返回 manifest，并以 rebuild_manifest=false 导出”，这是旧导出流程的指令，不能直接指导现在的 `timeline.export_editorial`。[对齐 Tool 说明](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/tools/audio/__init__.py:133)

真实 `post_editor`（`work-order-8f66df67545547ff9cb26ea4e1d3656f`）turn 1 先读当前 editorial，turn 3 做音频检查，turn 4 对旧 `project-974ca837e45e-manifest-v1` 对齐成功，返回新 manifest/调整清单/报告引用；turn 5 又读视频观察，turn 6 开始读完整对齐文档，随后以 PARTIAL 交接。不能从这次 Tool 成功推断用户当前字幕已经对齐。最终三句结尾字幕是在后续当前时间线中另行保存的，制作记录也这样记载。[制作记录](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/presentation/studio-launch-2026/04-production-progress.md:172)

**建议：保留本地 ASR 对齐算法，改写当前编辑输入适配和局部保存。** 请求 current editorial revision + cue IDs/片段范围；结果直接给同一时间线上的精确 cue 修改、实际音频源区间及未识别项。采用时只更新对应字幕，保护视频、音轨、文字与用户编辑。不要让 Worker 从旧报告逐条手抄时码，也不要新建另一份字幕真相。跨 Worker 交接只需报告引用和实际保存的 revision/变动 cue IDs。

## 5. Leader/Worker 信息与完成判断

### Q8：运行协议把确定性推进重新变成语言工作——明确机制与真实量级

每次 specialist 决策都重新构造 `loaded_schema_ids`，只从当次修复反馈等来源预装；schema 请求在局部 while 内追加，成功执行下一 Tool 后不成为 WorkOrder 的持续状态。提示词同时要求复杂参数先发现 schema，且一次只发一个 Tool。[协议](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4510) · [重建](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4640) · [局部追加](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4771)

[统计](01-current-metrics.json)已确认 802 条语言结果只有 schema 发现。按 queued/paused/failed/waiting_user 切段并按 WorkOrder + Tool ID 去重，仍有 458 条“这一段已见过所有请求 ID”的纯发现；这是保守重复口径，仍不代表无视 schema 版本后可以直接删除 458 次。模型仍保持 gpt-6-astra/xhigh，先让同一版本 schema 跨 Tool turn 保留、预装角色高频小集合，并让普通批量读取/登记一步完成。

读取工作集也不是只保留当前必要事实：非少数特殊 Tool 的 read observation key 包含完整 arguments，所以 `timeline.read_editorial` 的 include_project=true/false/省略可同时占三份；完整结果按不同参数保留。[观察身份](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:2033)

真实 `work-order-8ecfeff371264744b2063ab638509ef2` 在 turn 19/27/38 保留这三种同一时间线读取。一个后期实际调用 `model-36ec912784f7` 输入为 96,040 tokens；其工作集 JSON 中 read_observations 占 198,978 字符。字符数只是定位负载，不是 token 换算。应按“当前时间线对象 + 规范化范围 + 内容修订”替换相同事实，完整工件继续保留在 Trace/context_ref 中。

**建议：保留分工和精确引用，合并机械步骤。** 编译好的当前组不应为了登记一个已知字段再走一次 schema 发现、全量读、长参数重传。优先预装 `artifact.read_current`、角色常用的 `shot.save_current`/当前 timeline 读写、生成组上下文和状态读取；带版本的 Tool schema 缓存是普通工程机制，不需要新增模型。

### Q9：批次等待与完成证明仍有薄弱处——设计风险，区分已修复内容

`TeamExecutor.advance` 的 ThreadPoolExecutor 会等待本批所有 Future 返回；Worker 在 H3 已确认的常规等待中可持续 `task.advance` 并 sleep，再由同一 Worker 循环持有执行槽。Studio 调用的是同步 `advance_agent_team(...continue_until_handoff=True)`。因此同批一个短任务完成，不会马上释放当前调用让 Leader 安排它的后继，只要另一 Worker 仍在这次同步 drain 中。[批次等待](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/team_executor.py:140) · [H3 等待](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/team_executor.py:389) · [Studio 入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_runtime.py:352)

这与“空计划时独立 ready 工作被挡住”是不同问题：当前 Studio 已先执行 ready actions，那条历史问题已修复。轮询也已经能不调用语言模型，44 次 H3 轮询无新增语言决策的快照应保留。现在需要的小型 Fake 验证是：A 短任务完成、B 等待媒体时，A 的后继是否立刻可运行，而不是再做一次完整付费成片。

完成契约目前校验结构、不允许 COMPLETED 带 unresolved_questions，并要求至少一种完成线索；但线索可以只是 `recommended_next_actions` 或任意非空 deliverable，未按本次交付类型核对实际输出是否已保存。[完成校验](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/agent_runtime/contract_validation.py:16) · [结果落盘](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/agent_runtime/runtime.py:825)

这是**证据表达弱点，不是已证实的“模型都在谎报完成”**。Leader 仍要按真实目标作最终语义判断；不应加一个固定 Critic。媒体生成/剪辑导出的完成结果应自然携带可解析的任务终态、当前文件/编辑修订和缺项，纯文字修改则携带实际保存字段。让普通确定性检查核对这些事实即可，不能让 JSON 形状正确替代交付已发生。

## 6. Skill 的运行时有效性

### 已经完成的部分应保留

`compile_production_prompt` 按角色与阶段选择方法，公共前后缀按来源路径去重，Worker 不再整体注入完整端到端 playbook。上游原文与本地适配已分开存放，`SKILL.md` 中某条说明存在并不代表它进入了实际请求；本次只把 manifest 真正选中的章节和最终请求当证据。[方法去重](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_profiles.py:217) · [阶段编译](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_profiles.py:286) · [按章节载入](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/loader.py:387)

### Q10：同一实际 Leader 请求仍同时要求“自主自查”与“强制三段分工”——明确活跃矛盾

`leader.md` 说明角色可以覆盖制作与自查，不强制 Builder 后另派 Verifier；`leader_protocol.md` 又规定一个 WorkOrder 不把规划、生成、自审合并，并要求 ASSET_PLANNER 完成后结束，Leader 再读 index 委派 Builder，媒体产生后另交 ASSET_VERIFIER。[允许自主](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/prompts/runtime/leader.md:11) · [强制职责分离](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/prompts/runtime/leader_protocol.md:15) · [三段资产流程](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/prompts/runtime/leader_protocol.md:37)

这些不是闲置文档：最终 Leader 输入文件 `20260910T215149373323-3f35c5ac-model.input.json`，call_id=`model-4e2658a977ef`，确实同时包含这两组原句。要求 Agent 根据语义少交互，而更后面的输出协议又强制分工，会制造不必要的交接和再次取数。55 个 WorkOrder 不能全部归因于这一矛盾，但这个实际输入冲突本身需要修复。

**建议：合并成一份一致的职责规则。** 保留需要独立规划/制作/复核的情境说明，让已确定的小任务可以在同一 Worker 完成制作与必要自查；对确有并行价值的资产保持独立分工。验收先比较实际生成的 Leader prompt，再用现有案例的固定状态看计划与语言调用差异，不靠删掉专业方法换取短 prompt。

### Q11：部分专业说明仍是绝对限制，嵌套工具和阶段回退又丢失方法上下文——活跃说明缺陷/设计弱点

`method.drama_series` 的 DIRECTOR 实际加载 `storyboard.md::不通过视频串联保持连续性`，原节要求跨 clips “绝不要引用上一段渲染结果”。本地 footer 虽说明方法可覆盖，但并未在这个具体决策处解释什么时候真实前片观察/终态参考有益。当前 H3 compiler 的 `previous_clip_terminal_state` 也只读上一 ShotPlan 设计字段，不读已采用影片或当前剪辑的实际结束状态。[实际 manifest](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/methods/drama_series/manifest.json:54) · [被加载原节](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/storyboard.md:318) · [编译器上一状态](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:262)

本案例 11 个 DIRECTOR WorkOrder 均选择 `method.drama_series`，其中 post_editor 还是 DIRECTOR/POST；`prompt_for` 若该 stage 没有专门章节，会回退角色通用方法。不能因此宣称所有指令都重复注入，但应检查 POST 下是否仍拿到以分镜/生成为主的专业说明。[阶段回退](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/models.py:114)

前片真实结尾与设计不一致时，单靠“写清上一设计终态”会再次传播计划值。本案例结尾观察已证实端盘和站位与前片实际边界不连贯。这里的改进不是把每段视频盲目串作下一段输入，而是：先明确**设计意图、实际已采用终态、可接受偏差**各是什么，再决定文本描述、抽出的边界图、身份锚点或 Provider 支持的视频参考是否必要。专业说明应描述这个取舍，并与可调用工具的输入能力一致。

另一个已定位的问题是 Q6 的嵌套 `director_advice` 固定 STORYBOARD 且没有方法 ID。外层 Worker 的 Skill 不会神奇进入工具内第二次语言调用。**建议保留来源原文，改写本地运行说明并显式传递本次角色/阶段/方法与观察范围；当某阶段没有合适说明时只载入通用职责，不静默装载不相干阶段。**

## 保留、改写、合并、替换矩阵

| 对象 | 建议 | 必须达到的可观察结果 |
| --- | --- | --- |
| `shot.compile_keyframe_brief` | 保留，接入同一具体媒体解析 | 图片顺序、身份参考/主画布/时段用途与生成输入一致 |
| `qa.rerank_reference_visuals / compare_keyframes / check_input_compatibility` | 保留判断能力，改写生成组上下文；不要新增同名工具 | 联合检查实际选中输入；输出受影响窗口、可执行调整和未覆盖范围 |
| `video.inspect_take` | 保留抽帧、整组观察和局部复查；修参考读取和报告范围 | 明确看到了哪些参考/区间、没听到什么，不重复旧语义失败 |
| `video.compare_takes` | 重写上下文和报告复用 | 能比较真实整组/选用区间，NONE_ACCEPTABLE 有证据；不把局部图版检查当全组证明 |
| `shot.director_advice` | 保留为可选专业调用；继承当前方法/阶段/证据 | 相比外层 Worker 提供新信息，输出真正能落到当前内容的修复 |
| `shot.apply_director_repair / run_director_loop` | 合并当前内容保存服务 | 改动后 Storyboard、生成组、当前镜头的语义与时窗一致；返回实际后续范围 |
| `video.retake/extend/gap_fill` | 保持明确 Mock 边界；产品真实操作替换为 H3 受影响组新候选 | 不误用 Mock，不声称 H3 有局部重绘能力，不重发已完成媒体任务 |
| `audio.align_subtitles` | 复用本地 ASR，改写当前时间线读写 | 对齐成功后用户所见字幕与下一次当前导出确实变化 |
| schema discovery / 当前数据读取 / 状态推进 | 缓存同版本合同，合并确定性批量动作 | 发现次数、重复正文和语言输入量下降，同时仍保留完整 Trace |
| Skill 及 Leader 协议 | 合并冲突规则，按阶段校正本地适配 | 同一次请求无互相冲突职责要求；嵌套模型看到本次已选方法 |

## 建议先完成的最小验证

按改动顺序做普通工程验证，不追加付费稳定性实验：

1. 使用现有项目副本和 Fake，验证 8 张已选参考全部进入正确观察输入；EDITORIAL 用途不混入生成参考。
2. 对同组 Take 分别从代表镜与非代表镜发起比较，检查包含全部窗口；输入局部图版报告时明确缺少全组判断。
3. 对导演时长修改、拆镜、单纯镜头文字修改分别验证当前保存内容与重编结果；其中前两种不能留下旧组窗口。
4. 在已保存且裁切/变速过的当前时间线上对齐一段字幕，只允许对应 cues 改变；当前导出读取相同 revision。
5. 用 A 短任务/B 媒体等待的 Fake 检查 A 后继能继续；用同版本 schema 的连续 Tool turn 检查无需重复发现。
6. 保存实际生成的 Leader/Worker/嵌套工具输入摘要，对照已选角色、阶段、方法、媒体范围；已有专业知识必须能在实际请求中找到，且没有相互矛盾的职责要求。

这些验证衡量“同一个事实是否真正贯穿到下一步”和“修复后当前作品是否确实改变”。成片审美和创意取舍仍由 Agent/用户结合真实媒体判断，不用新审批门或额外质量分数代替。
