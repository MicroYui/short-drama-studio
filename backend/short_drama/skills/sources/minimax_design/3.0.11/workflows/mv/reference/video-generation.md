# MV Stage 6 — H3 最终 Prompt 编译、确认与派单

> **何时 Read**：Stage 5 全部导演分镜被接受后，author 唯一 `visual-gen` Stage 时读取。Stage 6 是最终 Prompt 的低自由度编译器；它完整投影已确认的逐镜导演、风格和字体过程，只补真实 refs、音乐切片与 H3 执行细节，再把完整 `work_items[].prompt` 一次交给用户确认。Executor 只取回并派发该已确认字符串，不重新编写。

## Stage-local 读取路由

第一工具回合只调用 `hub_plan_get_stage_detail(plan_id, stage_id=shot-plan)`，取得有序 `document_node_id`、项目路径、每份 `contentHash`/总行数、覆盖组和 Shot 数；不能在尚未得到节点 id 时并行发起其它 Reference 或 Stage 读取。真实返回后，第二工具回合按顺序对每个节点调用 `hub_canvas_read_text(nodeId, offsetLine=1, limitLines=400)`；若 `truncated=true` 或 `endLine<totalLines`，下一回合从 `offsetLine=endLine+1` 继续，直到每份 `endLine=totalLines`。按行号移除读取工具添加的编号后拼回正文，并记录工具真实返回的来源节点、`contentHash`、总行数与正文 Shot 数；Stage detail 与正文的 hash/行数不一致时重取当前 Stage detail，不自行挑选一版。

用户可见导演分镜是逐镜内容、风格和字体过程权威。节点预览、Plan 摘要、Stage detail、项目路径、聊天正文和 compaction 摘要只能定位，不能充当分镜正文；未读到全部镜头组、每个 Shot 和最后一行时停止编译，保持 `stage5_source_read_gate` 未通过。全文读取完成后再读取 `production-plan`、`assets` Stage detail、最终 `main_song` 和本文件；Stage 5 已展开当前风格与字体，不重复读取 style-system、typography-packaging 或未选路线。发生 compaction 后，从第一工具回合重新执行，不从摘要恢复来源或 Prompt。

## 唯一职责

- 一个 `clip_group_id` 对应一个且只对应一个 video work item 和一次 H3 生成；组内全部 Shot 进入同一 Prompt，不拆 Shot、不合并镜头组。
- 全部镜头组共同写入唯一 `visual-gen` Stage；不创建版本号 Stage、逐组视频 Stage、Prompt 文档或额外汇总 Stage。
- Stage 6 按固定六段语义编译完整 Prompt，并直接写入 `work_items[].prompt`。H3 看不到分镜、Plan 或 Reference；确认框展示的字符串必须自包含全部画面和包装内容，同一字符串供用户修改、确认和 Executor 派单。
- Stage 5 的每个 Shot 必须在画面过程段一一出现；编号、时间、场景/空间、人物动作、景别/构图/运镜、歌词/口型窗口、光影/材质/成像/VFX、准确文字、字体 Layer、切换和终态逐项带入，不得概括、合并、省略或改写。
- Stage 6 只增加真实 refs 的用途、audio 1 局部同步、人物/材质/光学的 H3 物理连续性和生成锁；可以把已确认过程压紧成连续执行语言，不能新增创意、换风格/字体、改变发力/落点、移动歌词秒点或替换运镜。
- 最终 Prompt 从第一句起使用运行时 `working_language`，六个显示标题也一次性物化成该语言并在全部组稳定复用；准确歌词、片名、姓名和其它已锁定原文保持自身语言。不得把中文模板标题直接复制到非中文项目，也不得把锁定原文翻译成 `working_language`。

## 编译来源与固定插入地图

1. **逐镜导演事实**：用户可见分镜是空间、动作、景别/运镜、歌词/口型、光影/VFX、字体过程和末态的权威；shot-plan capsule 只提供稳定 id、时间、refs 顺序和内部映射。两层数量或时间不一致时停止，不自行挑选一版。
2. **组级内容与声音**：Production capsule 的 Treatment、音乐分析、主唱锁和声音策略只补核心创意、音乐和表演的组级说明，不覆盖逐镜原文。
3. **风格与字体**：Stage 5 已确认分镜锁定组级视觉系统和每镜的光影/VFX、Typography、Layer 与切点包装；Production capsule 只用于校验路线和准确文字，不重新展开风格库或字体库。
4. **参考素材**：以 Stage 4 当前 runtime refs、用户明确素材和已选氛围 refs 建立有序 slot；搜索候选、被替换资产和聊天旧路径不进入 Prompt 或 `refs`。

| Stage 5 / 上游内容 | Stage 6 最终位置 | 处理方式 |
|---|---|---|
| refs 与用途 | `reference_materials` | 按真实 slot 顺序写入，不补不存在的素材 |
| 组功能、Treatment、空间路线和交接 | `creative_core` | 汇总组级作用，不提前概括逐镜事实 |
| 组级音乐、全部歌词、声线和口型策略 | `music_lyrics_performance` | 不改词、不改时间、不新增口型 |
| 已选风格、色彩、成像和字体路线 | `visual_typography_system` | 展开组级基调、TYPE CAST、配方和 Layer 总规则 |
| Shot 标题与时间 | 对应 Shot 标题 | 原样继承 |
| 场景/空间与人物/主体行动 | 场景与人物 | 依次带入原文，不改事实 |
| 音乐、歌词与口型窗口 | 音乐、歌词与口型 | 原文带入，不新增逐词时间 |
| 景别、构图与运镜 | 构图、运镜与表演轨迹 | 原文带入，不改镜头 |
| 光影/材质/成像/VFX | 同编号 Shot 连续画面 | 完整投影已确认过程，只补 H3 物理连续性 |
| 准确文字、字体 Layer 与包装过程 | 同编号 Shot 连续画面 | 完整投影，不换词、route、空间关系或退出结果 |
| 衔接与末态 | 同编号 Shot 连续画面 | 完整投影，并保持下一镜第一帧一致 |

## 固定六段骨架

六段语义和顺序固定为 `reference_materials → creative_core → music_lyrics_performance → visual_typography_system → shot_process → global_delivery_locks`。先按 `working_language` 生成六个清楚、正向、用户可读的显示标题并记录为当前 Stage 的 `prompt_section_titles`；以下中文只解释语义，不是跨语言项目必须照抄的标题。所有 `<...>`、教学说明和内部字段名都必须替换成当前组真实内容。

```text
<prompt_section_titles.reference_materials>
image 1 = <素材名称与角色>；锁定<身份、脸、发型、服装或空间>；只贡献<当前组实际用途>。
audio 1 = 最终主音乐 <绝对开始–结束> 的 <整数秒数> 秒切片；作为本组歌词、节奏、口型和动作同步声音参考。
<只写真实存在且已进入 refs 的 slot；每个 slot 独占一行。>

<prompt_section_titles.creative_core>
<本组在全片中的作用、音乐段落、空间路线、视觉升级和前后交接；不改写逐镜导演事实。>

<prompt_section_titles.music_lyrics_performance>
<audio 1 的范围、节奏/能量/音色和落点；本组全部完整歌词原句或纯器乐时间；主唱/口型和已确认人物表演弧。>

<prompt_section_titles.visual_typography_system>
<从 Stage 5 组级视觉与字体系统完整投影当前媒介、色彩/光线/材质/成像/VFX、TYPE CAST、prompt_type_recipe、本组准确文字、Layer A/B/C 总规则、可读停留、跨镜传递和退出。>

<prompt_section_titles.shot_process>
Shot 1 — <本组局部时间>｜<主音乐绝对时间>｜<景别/焦段/主运镜>｜<空间区段>
<把 Stage 5 同编号 Shot 的场景/空间、人物动作、音乐/完整歌词/口型、构图/运镜、光影/材质/成像/VFX、准确文字与 Layer A/B/C、切换、下一镜第一帧和可见终态全部合并成一段连续画面；补充真实 refs、audio 1 局部时间和 H3 物理连续性，但不改变任何已确认视觉事实。>

Shot 2 — <按同一连续段落结构逐镜填写，直到数量、编号、时间和顺序与 Stage 5 完全一致；不得合段或写“同上”。>

<prompt_section_titles.global_delivery_locks>
<人物/场景/媒介/画幅/文字/连续性锁；代理音轨仅供同步，Stage 7 使用完整主音乐；准确文字、身份和肢体要求。>
<逐字放入 production_plan.style_anchor.medium_lock；完整行以当前工作语言的 Medium 标签或约定前缀开头，其后不再增加内容。>
```

## 逐镜编译硬合同

- 每个 Shot 必须有时间标题和独立连续段落；段落真实包含场景与人物、音乐/完整歌词与口型、构图/运镜与表演轨迹、光影/材质/成像/VFX、Typography/Layer、Rhythm/Cut/衔接与终态。任何一项不能被组级段落替代。
- Stage 5 的导演、风格、字体与切换事实不得重述成摘要；Stage 6 新增语言只补 refs、audio 1 局部同步、肢体/服装/材质/光学响应和生成连续性。
- 每个 Shot 块后保留完整空行；不用表格、YAML/JSON、项目符号或分号技术清单代替可读的连续过程。
- 每个带歌词 Shot 重复完整歌词原句与来源秒数，再指出本镜覆盖词段；正脸可见默认演唱，纯器乐、空镜、动作插入或嘴部不可见才不唱。
- `text_packaging!=none` 时每个 Shot 都有当前 Layer A/B/C 状态；没有新主词也写既有词的跟随、遮挡、传递、落稳或退出。每组只引入 Stage 5 已锁的 2–4 个新主词。
- 当前风格为可爱动画包装时，根据 Stage 5 预留触发，写清手绘线、小漫画分格或内容相关动漫小角色的外观、逐笔运动、人物/道具互动、跨镜传递与回收；贴纸、星点、糖珠和泡泡只增强这条动作链。
- 每个 Shot 保留 Stage 5 已锁主运镜、光影、材质、字体、VFX 和落点；Stage 6 只能补其在 H3 中连续成立所需的物理响应，不能新增或替换已确认设计。
- 最终 Prompt 中出现的 Shot 数必须等于 `shot_block_count`；出现“依次完成 N 个 Shot”却没有全部 Shot 正文，直接判定失败。

## 稳定编译顺序

1. 先完成两步来源读取，记录 `shot_plan_source_node_ids`、`shot_plan_source_content_hashes`、真实总行数和来源 Shot 数，并通过 `stage5_source_read_gate`；此前不建立 video work items。
2. 为全部视频 items 写完整结构外壳：`id/name/modality/clip_group_id/sequence_index/timing_target/refs/source/render/shot_block_count/prompt_section_count`；`timing_target` 是单行字符串。此时不写 `prompt_compile_gate=pass`。
3. 为当前组创建六个本地化标题和与来源 `shot_count` 相同数量、同编号的 Shot 区块；先逐 Shot 投影 Stage 5 的全部导演、风格、字体和终态事实。
4. 从 Production capsule 只补核心创意、音乐和组级表演；从真实 refs 与 audio 1 只补素材职责、局部同步和 H3 物理执行细节，不重新读取或设计风格/字体。
5. 逐镜对照来源；任何 Shot 缺失、被概括、换序，或任一光影/VFX、Typography、Layer、衔接事实消失时，恢复完整事实后再检查长度。
6. 完成当前组后冻结该 Prompt，再编译下一组；补结构字段、写后续组或修复校验错误时逐字复用已冻结字符串，不重新润色已经通过的组。
7. 全部来源、结构、长度、完整性和 refs 同时通过后，写 `stage5_fact_projection_gate=pass`、`stage6_execution_enrichment_gate=pass` 与 `prompt_compile_gate=pass`，再一次提交全部已冻结 Prompt 进入确认框。

## 长度与完整性

- 7000 字符是每组最终 `work_items[].prompt` 的绝对上限，汉字、英文、路径、空格、标点和换行都计入。12–15 秒非叙事/高能 10–15 Shot 组以 5200–7000 为写作目标，8–9 Shot 组以 4500–7000 为目标，4–7 Shot 或较短/安静组按完整事实自然收敛；目标用于发现摘要化，不为凑字数重复同义形容词。
- 长度只检查 Stage 6 的最终 Prompt；Stage 5 不登记 Prompt 字符数，也不做两阶段字符级相等检查。
- 接近上限时先删除组级段落与逐镜之间的重复说明、冗余形容词和不影响执行的解释；不得删除或概括 Stage 5 的 Shot 数量、时间、歌词/口型、动作、空间、景别/运镜、光影/VFX、字体 Layer、衔接和终态。
- Stage 5 全部导演事实加必需包装仍超过 7000 时，Stage 6 在不丢事实的前提下压缩重复表达；如果依然超限，保持 Gate 未通过并指出当前组的真实超限位置，不生成短版。
- 六段齐全、全部 Shot 逐个显式出现、每镜连续段落覆盖全部事实，最后一行为完整 Medium lock。低于对应写作目标时重新对照来源；只有全部来源事实和 H3 执行细节真实存在才可通过，不能以 `prompt_compile_gate=pass` 自我声明替代检查。“按分镜执行”“逐字复制”“见 Stage/Plan”“Shot 1.1–1.8”等指针或范围不能替代任何正文。

## 固定模型、音乐切片与 Stage 结构

固定 `MiniMax H3 · 2K`，画幅继承 production-plan。先按镜头组绝对整数范围准备全部 WAV 音乐切片；每个视频只绑定自己的等长切片，不传整首或相邻组。模型音轨只作口型/节奏同步代理，Stage 7 丢弃并换回完整主音乐。

```yaml
depends_on: [production-plan, shot-plan, music-prep, assets]
concurrency_limit: 1
execution_locks:
  - { production_mode: mv_video_generation, vendor: MiniMax, model_id: MiniMax-H3, resolution: 2K, aspect_ratio: <已确认画幅>, duration_unit: integer_seconds, duration_range_sec: [4, 15] }
  - { submission_order_lock: sequence_index_strict, one_video_tool_call_per_assistant_step: true, prompt_transport: confirmed_work_item_prompt }
stage_fields:
  execution_policy: serial_music_reference_prep_then_serial_confirmed_video_items
  prompt_template_id: cool_music_video_longform_group
  prompt_section_count: 6
  shot_plan_source_document_count: 1 | 2
  shot_plan_source_node_ids: "node_id_1[,node_id_2]"
  shot_plan_source_content_hashes: "content_hash_1[,content_hash_2]"
  shot_plan_source_total_line_count: <真实整数>
  stage5_source_shot_count: <真实整数>
  stage5_projected_shot_count: <真实整数>
  stage5_source_read_gate: pass
  stage5_fact_projection_gate: pass
  stage6_execution_enrichment_gate: pass
  prompt_compile_policy: stage6_low_freedom_project_complete_stage5_then_add_execution_details
  prompt_compile_gate: pass
```

`concurrency_limit=1` 与顺序锁是执行合同。先完成全部音乐切片；随后只提交最小 `sequence_index` 的待执行视频，当前 assistant step 只能出现一次 `hub_generate_video`。该调用完成或明确失败后，下一模型回合再读取同一 Stage 并提交下一组；用户不需要逐组确认。

```yaml
id: music_ref_cg_01
name: <用 working_language 表达“镜头组 1 音乐参考切片”>
modality: postprocess
refs: [main_song]
source: "main_song [0,15]"
render: { duration_target_s: 15, output_format: wav, output_role: generation_reference_only }
timeline: { operation: "hub_ffmpeg trim_audio_reference", ordered_input_refs: [main_song] }
prompt: "<用 working_language 写成的完整裁切指令：从 audio 1 取得 main_song 绝对 00:00–00:15 的 15 秒 WAV，保持原速度、音高与单次连续时段>"
```

```yaml
id: cg_01
name: <用 working_language 表达“视频镜头组 1”>
modality: video
clip_group_id: cg_01
sequence_index: 1
timing_target: "main_song 00:00–00:15；audio 1 局部 0.0–15.0s；Shot 1.1–1.12"
refs: [char_main, music_ref_cg_01]
source: "shot-plan cg_01；Shot 1.1–1.12；00:00–00:15；15s"
render: { duration_target_s: 15, generate_audio: true, audio_approach: reference_led_lipsync_proxy }
shot_block_count: 12
prompt_section_count: 6
prompt: |-
  <按固定六段骨架编译的完整最终 Prompt>
```

`timing_target` 和 `source` 使用单行标量；切片、视频时长和组边界相等。带歌词且主唱嘴部可见的组使用 `generate_audio=true + reference_led_lipsync_proxy`；没有口型且无原生声音职责时可为 false。固定 H3 不可用时保留 Stage 并报告能力问题，不静默换模型。

## Reference 绑定

从 Stage 1 用户 refs、唯一 assets Stage 当前 runtime refs、用户已选氛围 refs、合法前序 runtime refs 和本组音乐切片解析真实 path/url/node id。按 Prompt 中 image 1、image 2、video 1、audio 1 的出现顺序建立 `refs`；同一 ref 不重复，被替换资产、搜索候选和聊天旧路径不进入。人物卡锁 identity/wardrobe，条件场景卡锁 scene identity，普通场景沿用 Stage 5 描述。Production Board 展示 slot、素材名、stable id、当前真实 path/node id 和用途；缺失、错位或不可读时不派单。

## 一次确认与确认后派单

每组完成并冻结时，本轮编译出的完整六段字符串是该组唯一确认正文；Stage 6 的 `work_items[].prompt` 必须与其换行、标题、标点和正文逐字符相同。这里的“其”指本轮已完整读取 Stage 5 后编译出的当前字符串，不是 Stage 5 行号范围或复制指令。

`hub_plan_patch_stage` 一次写入全部切片、全部 video items、执行锁和一条 `review.before_execution`；该审阅文案用 `working_language` 表达：当前展示的是全部最终 H3 派单 Prompt，每条对应一个镜头组；请展开核对完整内容、对应秒数、音乐切片和有序参考素材；确认后按当前字符串逐组生成。每个 item 的 `prompt` 必须已经是可读、可直接交给 H3 的完整六段正文；用户在同一 Production Board 一次确认全部组，不逐组确认。

用户在确认前修改某组 Prompt 时，该修改后的完整字符串成为该 work item 的新冻结版本；只重新检查该组六段、Shot 数、refs、时间和 7000 上限，不要求把最终包装文字反写到 Stage 5 导演分镜。用户改变镜头事实、组边界或素材时才返回 Stage 5 更新对应导演内容后重新编译受影响组。

确认后不再触发 Planner。Executor 先读 Stage detail；若其 `notice` 说明 work item prompts 被省略，必须调用 `hub_plan_get_work_items` 按当前待执行稳定 item id 取回完整原始条目，结果过大时缩小到单个 id。随后把已确认 `work_item.prompt` 原样传给 `hub_generate_video`；不得从聊天、Plan 文件、Stage 5 分镜、Reference 或摘要重建、翻译、润色或压缩 Prompt。网络、参数或 capability 重试只改报错字段并复用同一 Prompt、refs、id、时长和顺序。

## 派单前 Gate

检查来源节点、`contentHash`、总行数、末行覆盖与 Shot 数均来自 `hub_canvas_read_text` 完整返回；全部组时长总和等于最终音乐；每组 4–15 秒整数；固定六段齐全；来源 Shot 数等于投影 Shot 数，编号和时间与 Stage 5 一一对应；每个 Shot 的导演事实、光影/VFX、Typography、Layer、衔接和终态完整；refs/slot/路径/媒体类型一致；画幅、Medium lock、audio 1 与生成参数一致；Prompt 不超过 7000，低于丰富度目标时已逐镜复查；`concurrency_limit=1`、顺序锁和三个来源/投影 Gate 均为 `pass`。确认前拒绝指针句、Shot 列表摘要或“依次完成 N 个镜头”替代正文；执行时 `hub_generate_video.prompt` 必须等于取回的已确认 `work_item.prompt`。

## 反模式

- 只读 Plan 摘要、节点预览、路径或聊天内容，不按两步顺序取得节点并用 `hub_canvas_read_text` 读完导演分镜。
- 把 Stage 5 多个 Shot 合并成“依次完成若干镜头”的摘要，或用“按分镜/按原文/Shot 范围”代替逐镜正文。
- 改写 Stage 5 的场景、动作、歌词、口型、景别、运镜、光影/VFX、字体 Layer、切点或终态，而不是只补允许的执行细节。
- 重读风格/字体库后重新设计，或用风格名、route id、几句关键词或短稿代替 Stage 5 已确认的风格和字体过程。
- 为通过校验、缩小 payload、应对 compaction 或减少思考时间而压缩 Prompt，或在校验失败后用短版替换已冻结完整版。
- Stage detail 省略 Prompt 后绕过 `hub_plan_get_work_items`，转而读取分镜或聊天重新编写。
- 不经用户查看最终 Prompt 就生成；确认后又让 Planner/Executor 重写或并行提交多个长 Prompt。
- 传整首音乐、错组切片、旧角色卡、未选风格图，或让代理音轨成为最终歌曲。
