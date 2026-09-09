# MV Stage 5 — 导演分镜与镜头组规划

## Stage-local 读取路由

Stage 4 被接受后，在同一次 `shot-plan` author turn 依次读取：production-plan Stage detail 的 `content_treatment`、`rhythm_strategy`、`spatial_progression`、`performance_choreography`、`insert_strategy`、`style_anchor`、`typography_system`、`text_ledger`、Medium lock 与时长权威；最终 `main_song_lyrics_timeline` 全文；Stage 4 当前 anchors、普通场景描述和已选氛围 refs；本文件；再按 `<workflowsDir>/mv/reference/style-system.md` 顶部路由只读共用导演段、当前已选风格段和“Stage 使用规则”，按 `<workflowsDir>/mv/reference/typography-packaging.md` 顶部路由只读共用合同、当前已选 route、音乐同步与 Gate。只有 capsule 缺本 Stage 必需的 Treatment，或用户接受后修改了制作计划，才补读用户可见制作计划的相关段落。节点简介、`textPreview`、聊天摘要和更早会话记忆都不算正文。

## 职责边界

- 一个镜头组对应一个 4–15 秒整数 H3 video work item；组内 Shot 是该次生成内部的独立剪辑镜头，不创建独立 work item。
- Stage 5 决定每组有几个 Shot、每镜几秒、拍什么、人物/主体和空间怎样运动、景别/构图/运镜、对应歌词/口型窗口，以及逐镜光影、材质、成像、VFX、准确文字、Layer A/B/C、切换与可见终态。
- Stage 5 沿用 Stage 3 已选风格和字体系统，把当前路线的必现证据、字体配方和包装机制落实成每个 Shot 可见的触发—发展—接管—恢复过程；不写 refs 六段外壳、模型参数或最终 Prompt 字符串。
- Stage 6 必须按 Shot 一一投影 Stage 5 的编号、时间、导演、风格和字体事实，只补真实 refs、音乐切片和 H3 执行所需的物理连续性，不得合镜、删镜、改词、改变动作/空间/运镜或另写一套设计。

## 输入与时间权威

author 前核对内部时长权威、最终音乐物理时长、歌词时间轴末行和制作计划总时长为同一整数；冲突时返回最早出错 Stage。`narrative_story` 保持人物、事件、因果、地点功能和结局；`visual_aesthetic` 只按主体、情绪、母题、关键画面与视觉演进组织画面，不补人物目标、任务、关系、阻碍或结局。

令最终整数音乐时长为 `T`：`N=ceil(T/15)`，`Q=floor(T/N)`，`R=T mod N`；前 `R` 组为 `Q+1` 秒，其余为 `Q` 秒。只有相邻组仍为 4–15 秒整数且总和不变时，才把边界吸附到歌词句尾、段落切换、重拍或呼吸点。组内 Shot 使用连续 0.1 秒边界，首镜从本组 0.0 开始，末镜严格落到本组整数终点；用户移动 Shot 时联动重排受影响边界，未受影响 id、Shot 和 refs 保持稳定。

## 镜头密度、空间与表演

| 风格 | 12–15 秒常用 Shot 数 | Stage 5 规划重点 |
|---|---:|---|
| 炫酷剪纸拼贴 | 10–15 | 复古环境、人物表演、实景/印刷层转换、空镜/细节、版式与大字接管任务 |
| 可爱动画包装 | 8–12；高能 10–15 | 完整糖果/奇幻环境、道具与尺度互动、逐笔手绘/小漫画触发任务 |
| 特效舞台表演 | 8–12；高能 10–15 | 舞台建立、表演近景、区域换位、灯光/投影/装置接管任务 |
| 千禧复古年代 | 10–15 | Y2K 布景、实体/身体微距、DV/CRT/窗口/像素媒介转换任务 |
| 电影感叙事 | 4–8；爆发段 6–10 | 因果行动、人物反应、环境/细节/主观插入、地点或时间变化和轴线 |

非叙事高能组形成 3–5 个有继承关系的空间区段或媒介状态；每组至少一次全身大动作或大位移、两次空间/服装/道具借力、一次近距离表情或嘴型落点，并混合人物表演、环境建立/空镜、身体或道具细节、反应/意象和包装接管。相邻 Shot 至少改变空间区段、人物路径、机位高度、身体朝向、景别或前中后景关系中的两项。

景别以中景、中近景、近景和特写构成主序列；全景只用于空间建立、大位移、场景交接或结尾落点。每个接近 12–15 秒的组通常只安排 0–1 个大广角/超广角爆点，之后及时回到中近景和特写。动作写清预备、发力、接触/反应、惯性和落稳；每镜只有一个主运镜目的，强技法后必须恢复到清楚主体。

## 一份或两份画布分镜

Stage 5 始终只有一个 `shot-plan` Stage，并只进行一次用户确认。中文项目默认物化一份 `MV导演分镜.md`；其它语言从第一版起使用 `working_language` 的自然标题。镜头组超过 6 组或预计正文超过 36000 字符时，只在完整镜头组边界近似均分为上下两份，两份同样使用 `working_language` 的自然标题。不得创建第三份、summary、schedule 或独立 Prompt 文档。一份分镜建立一个 document work item；上下两份建立两个按 `sequence_index` 排列的 document work item。

上下两份各自保持完整镜头组；上篇包含全片概览和前半组，下篇开头写上篇末镜可见终态、承接的音乐绝对时间和本篇组范围，再继续后半组。`execution_excerpt` 保存有序 `document_node_id`、真实项目 MD 路径、每份 `contentHash` 与真实总行数、覆盖组、每组 Shot 数和跨文档交界终态；不记录 Prompt 起止行或复制 Gate。Stage 6 按该顺序用 `hub_canvas_read_text` 完整读取一份或两份文档到 EOF。

画布只展示用户可理解和修改的自然语言，不显示 `clip_group_id`、`cue_id`、`style_id`、`route_id`、`VISIBLE_TEXT` token、slot id、Gate、字段路径或 `key=value`。稳定 id、refs 顺序、逐镜风格过程与字体事件映射写入 `shot-plan.execution_excerpt`；准确上屏文字直接用引号展示并保持原文。

## 用户可见分镜格式

在物化第一份文档前，先把文档标题、概览、镜头组、派单、主音乐、秒、镜头、组功能、音乐与歌词、参考素材、视觉系统、字体系统、Shot 六字段等全部显示标签一次性生成成 `working_language`，随后在本项目全部分镜中稳定复用。下列中文仅说明语义，不得逐字复制为不同语言项目的显示标题；准确歌词、片名、姓名和其它锁定原文保持自身语言。

```text
# <document_title>
<overview_label>: <最终音乐与整数时长、画幅、连续镜头组边界和镜头数、内容方向、媒介×风格、主视觉演进、人物/场景/氛围参考、口型、文字主轴、Hook 与结尾。>

## <clip_group_label> 1 / <dispatch_label> 1（<main_music_label> 00:00–00:15｜15 <seconds_label>｜12 <shots_label>）
<group_function_label>: <歌曲段落、Treatment 作用、能量轨迹、空间路线、动作升级、镜头配置和前后组交接。>
<music_and_lyrics_label>: <本组完整歌词/纯器乐、来源整数秒数、主唱/口型和声音触发。>
<reference_materials_label>: <按真实素材名称说明人物、场景、氛围和主音乐分别锁定什么；没有的维度不写。>
<visual_system_label>: <本组当前媒介、色彩、光源、材质、成像、VFX 与风格接管/恢复规则，以及分别落在哪些 Shot。>
<typography_system_label>: <本组 2–4 个准确主词、来源、允许上屏秒点、TYPE CAST、字体骨架、颜色、材质、Layer A/B/C 总规则、可读停留、跨镜传递和退出。>

### <shot_label> 1.1 — <group_local_time_label> 0.0–1.2s｜<main_music_label> 00:00.0–00:01.2｜<shot_size_lens_camera_label>｜<spatial_zone_label>
<scene_and_space_label>: <场景、环境主物、主体位置、前中后景、光源方向和从上一镜继承的状态。>
<subject_action_label>: <进入路径、动作起势、重心/发力、空间/服装/道具接触、惯性和落点。>
<composition_camera_label>: <机位、焦段、方向、速度、焦点、景别变化和最终构图。>
<music_lyrics_lipsync_label>: <完整歌词原句/纯器乐、来源秒数、本镜覆盖词段、正脸演唱/非演唱安排和节奏落点。>
<visual_typography_process_label>: <光影、材质、成像和 VFX 的来源与完整变化；准确文字的字体骨架、尺度、颜色、空间绑定、Layer A/B/C、人物/镜头联动、可读停留和退出落点。>
<transition_terminal_label>: <切换触发；人物、空间、镜头、光影/VFX 和字体 Layer 的可见终态；下一镜第一帧。>

### <shot_label> 1.2 — <按同一结构继续，直到组内 Shot 数准确>
```

每个 Shot 必须是独立编号和独立时间段，六个基础字段合计形成有先后关系的完整过程；人物/空间/运镜事实和视觉/字体过程都使用可见动作表达。分镜要让用户直接看懂镜头怎么拍、人物怎么动、光影/VFX 和文字怎样随动作与音乐推进；不用风格名、route id 或包装术语代替具体过程。两个 Shot 之间保留一个完整空行，不使用表格、YAML/JSON、内部 id、字段路径或分号关键词清单代替导演描述。

## 分镜导演规则

- 带歌词且同一表演者正脸可见时默认演唱；纯器乐、空镜、动作插入或嘴部不可见时可不唱。同一 cue 跨镜时每镜重复完整原句并指出覆盖开头/中段/结尾。
- 每镜明确一个主运镜和落点。根据动作、剧情和音乐选择推/拉/摇/移/跟拍/环绕、转焦、希区柯克变焦、甩镜/甩焦、旋转变焦、跳切、抽帧、速度突变、叠化、遮挡切或匹配切；强技法后给恢复点。
- 连续叙事锁定场景轴线、人物左右、视线和屏幕运动方向；越轴前用中性机位、移动过轴或重新建立空间关系。
- 前 1–3 秒建立清楚的视觉、动作、文字任务或声音 Hook。最后 0.3–0.8 秒规划稳定终态，不开启新动作、歌词或场景。
- `text_packaging!=none` 时每组使用 2–4 个准确主词；在对应 Shot 写清来源、允许秒点、字体骨架、材质/颜色、占屏、空间绑定、Layer A/B/C、人物/镜头联动、可读停留、跨镜传递和退出。没有新主词的 Shot 继续写既有文字的跟随、遮挡、传递、落稳或退出状态。

## 物化 Gate 与用户修改

`shot-plan.stage_fields` 只保存可靠标量：`storyboard_document_count=1|2`、`stage5_reference_refresh_gate`、`clip_group_count`、`duration_sum_sec`、`max_group_duration_sec`、`duration_schedule_gate`、`shot_count`、`stage5_storyboard_gate`、`cue_lipsync_assignment_gate`、`style_process_assignment_gate`、`typography_process_assignment_gate`、`continuity_gate`。完整组/Shot id、时间、cue、refs、有序文档、逐镜风格和字体过程映射写入 `execution_excerpt`。

完成条件：全部组为 4–15 秒整数且总和等于音乐；编号和时间连续；每个 Shot 六个基础字段齐全；镜头密度、场景变化、动作、景别/运镜、歌词/口型窗口、光影/材质/VFX、refs、轴线和末态可追溯；`text_packaging!=none` 时每组有 2–4 个准确主词，且逐镜 Layer、空间绑定、可读停留和退出连续。Gate 检查高完成度导演分镜，不检查最终六段 Prompt 字数。

`review.after_execution` 用 `working_language` 一次展示全部分镜文档，让用户查看镜头组/派单数量、连续时间、每个独立 Shot、歌词/口型、人物动作、空间路线、景别/运镜、光影/VFX、字体 Layer、refs、Hook、结尾和跨文档衔接。用户修改时只 patch 受影响 Shot 与组级内容；其它 id、边界和 refs 保持稳定。

## 反模式

- 一个 15 秒组只有 3–4 个笼统镜头，或把多个镜头塞进一条 Shot。
- 人物在同一空间反复走动/转身，缺少大动作、空镜、细节、反应、场景切换和明确末态。
- 只写画面概念，不写景别、构图、运镜、人物/空间轨迹、歌词窗口和切换。
- 只把字体、光影和 VFX 留成接口或效果名，导致 Stage 6 必须重新设计逐镜视觉过程。
- 把分镜写成过短摘要，导致 Stage 6 必须猜镜头数量、动作、空间、歌词、运镜或衔接。
- 长片把同一镜头组拆到上下两份、重复/漏掉 Shot，或缺少跨文档交界终态。
