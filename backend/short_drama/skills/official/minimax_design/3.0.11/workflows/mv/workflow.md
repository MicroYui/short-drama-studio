---
project_type: mv
stages: [intent, music-prep, production-plan, assets, shot-plan, visual-gen, post]
---

# MV — music-led video workflow

> **进入信号**：用户在当前请求中明确把目标交付称为 `MV`、`音乐视频` 或 `music video` 即可进入；包括 `做一个 MV / 做一个音乐视频 / 完整 MV / 完整音乐视频 / 全曲 MV`，以及英文等价的 `make a music video / create a music video / full MV / complete MV / full music video / complete music video`。未说明时长时默认进入本 workflow；15 秒以上、具体长时长、完整歌曲、多段分镜、多条连续视频、音乐分析、角色/场景锚点或终剪合成也进入。文件名、附件标题、参考素材描述、媒体分析和 Agent 推断中的品类词不建立进入资格。
>
> **边界**：明确要求 `15 秒 / 15 秒以内 / 不超过 15 秒 / 最长 15 秒 / ≤15 秒` 的单条 MV 或音乐视频，以及英文等价的 `15s MV / 15-second MV / 15-sec music video / music video within 15 seconds / up to 15 seconds / no longer than 15 seconds`，由 `cool-music-video` skill 承接。用户没有显式说 `MV / 音乐视频 / music video` 时保持 media-agent 默认路径，不根据音乐、歌词、卡点、表演或附件推断品类；但仍允许 `cool-music-video` 按自身专属触发词独立命中。其它明确品类按核心交付进入各自流程。

## Principle

最终主音乐是唯一时间权威。七个 Stage 只消费已确认上游产物，不从聊天摘要重建事实；已完成且被接受的 Stage 直接跳过。所有用户可见内容从第一版起直接使用运行时 `working_language`：Stage 显示名、`question`、画布 MD、审阅文案、图片/音乐/视频 Prompt 的生成指令与镜头描述统一随该语言生成；准确歌词、姓名、标题和其它用户原文保持自身语言。Gate、稳定 id、字段路径、route、模型派单骨架和其它机器合同写入对应 Stage 的 `execution_excerpt`，下游通过 Stage detail 读取，不在画布正文或附录展示。

内容先锁 `direction_mode`。`narrative_story` 保护人物、事件、因果、地点功能和结局，五种风格只改变呈现；`visual_aesthetic` 用主体、情绪、母题、关键画面和视觉演进组织画面，不编人物目标、关系、任务、阻碍或结局。动态大字默认在视频模型内生成，只有逐字准确歌词、标题或指定文案登记 Post 兜底。

## Stages

| 用户显示 | stage_id | 稳定输出 | 执行前检查 | 产出后检查 |
|---|---|---|---|---|
| <用 `working_language` 表达 Stage 1“确定 MV 方向”> | intent | 显式输入与单次补充问询、内部 Intake capsule、用户可读创意方向文档、音乐来源、媒介、风格、时长、画幅、角色—主唱锁和真实 refs 映射 | 无 | 查看故事梗概或五字段视觉概念，以及媒介、风格、时长、画幅和音乐来源 |
| <用 `working_language` 表达 Stage 2“确定音乐”> | music-prep | 最终整数秒 `main_song` runtime ref、音乐 Gate、来源/形式/语言/曲风和主唱呈现 | 无 | 试听最终歌曲，确认整体效果与末尾处理可用 |
| <用 `working_language` 表达 Stage 3“制作计划”> | production-plan | 唯一 `main_song_lyrics_timeline`、内部 Production capsule 和用户可读 `production_plan` | 无 | 同时查看实际歌词时间轴、内容方案、段落/空间/动作/风格/字体发展与制作执行说明 |
| <用 `working_language` 表达 Stage 4“角色与场景锚点”> | assets | `style_reference_capsule`、角色卡、按条件生成的场景卡和普通场景描述；用户主动要求时追加 Research | 检查真实生成/复用清单、prompts、用户明确指定的人物 refs 与图片参数 | 查看人物身份/服装/风格投影、条件场景；同一说明附非阻塞 Research 邀请 |
| <用 `working_language` 表达 Stage 5“分镜规划”> | shot-plan | 一份或长片上下两份高完成度导演分镜；逐组锁定全部 Shot、动作、空间、运镜、歌词口型、光影/材质/VFX、字体 Layer 和可见终态 | 无 | 一次查看全部分镜文档，核对连续时间、镜头密度、逐镜导演事实、风格/字体过程和跨文档衔接 |
| <用 `working_language` 表达 Stage 6 — 视频生成> | visual-gen | 每组真实音乐切片、1:1 `video_work_items`；Stage 6 完整读取已确认分镜并低自由度编译为可直接派单的完整六段 Prompt | 固定 MiniMax H3 · 2K；在同一 Production Board 展开检查完整 Prompt、refs、时长、画幅和声音策略 | 查看各组主体/场景、歌词口型、节奏、字体包装和段间衔接 |
| <用 `working_language` 表达 Stage 7“后期合成”> | post | `mv_timeline` 和 `mv_final` | Stage 6 视频确认后自动执行，不再询问剪辑确认 | 直接展示最终成片，检查音乐连续、文字/SFX、画幅、首尾和黑屏 |

## Artifact Handoff

- `intent`：完整机器结构写入 Stage `execution_excerpt`，方向文档只展示同一创意的可读正文；Plan 另保存合法标量锁值、真实文档引用和审阅文案。Stage 2 处理已有主音乐时只读 `execution_excerpt` 与真实音频 ref；Stage 3 需要展开创意时才读当前方向文档。
- `music-prep`：Stage 3 只分析当前最终 `main_song`；歌词种子和音乐生成 prompt 都不是最终歌词真相。
- `production-plan`：内部 Production capsule 是 Stage 4–7 的精确执行数据，用户可见制作计划是同一事实的可读投影。下游默认只读 Stage detail；只有 capsule 缺少本 Stage 必需的可读 Treatment，或用户在接受后修改了文档时，才补读当前制作计划的相关段落；无权重算音乐总时长。
- `assets`：未选 Research 候选不进入下游；被替换资产移出当前 refs，稳定资产 id 不变。
- `shot-plan`：一个镜头组对应一个视频 work item；内部 capsule 锁稳定 id、refs、一到两份分镜顺序、逐组 Shot 数量/时间和准确文字映射。用户可见分镜是逐镜内容与视觉过程权威，已经包含当前已选风格、光影、材质、VFX、字体 Layer 和衔接终态，但不承担最终六段 Prompt 的运输或行号映射。
- `visual-gen`：Stage 6 是最终 Prompt 的低自由度编译器；`work_items[].prompt` 将 Stage 5 的每个 Shot 全部事实逐项投影进固定六段骨架，只补真实 refs、音乐切片和 H3 执行所需的物理过程，不重新设计风格、字体、动作或镜头。确认后的该字符串就是 Executor 的派单权威；Stage detail 省略长 Prompt 时，Executor 用 `hub_plan_get_work_items` 按稳定 item id 取回原始条目，不从分镜或聊天重建。Post 只按 `sequence_index` 消费当前视频 runtime refs，并以 Stage 2 完整主音乐替换口型代理音轨。

## Reference Routing

本表已给出精确切片时直接读该切片，不再先读 `offset=1, limit=12` 发现路由。同一 Planner session 内，按“文件路径 + 已覆盖行范围 + 来源 Stage revision + 已选路线”复用成功的 `hub_read` 结果，后续只补读未覆盖的最小范围。只在发生 compaction、前次读取报错/截断、用户修改导致来源 Stage revision 变化，或风格/字体路线改变时重读受影响切片。不得为了“再确认一次”扩大到整本，也不得读入未选路线。

| 当前 Stage | Required reads |
|---|---|
| intent | `<workflowsDir>/mv/reference/creative-and-plan.md` 的 Stage 1 问询切片；显式输入登记与本次补充问询完成后再读 Stage 1 创意/方向文档切片，以及 `<workflowsDir>/_shared/stage-execution-plan.md` 的 `offset=14,limit=40` 与 `offset=66,limit=71` |
| music-prep | 已有单一主音乐时，只读 intent Stage detail 的 Intake capsule、当前真实音频 ref 和 `<workflowsDir>/mv/reference/music.md` 顶部声明的“已有主音乐快路径”切片；不读方向文档、角色卡、曲风候选库或音乐生成章节。只有 Agent 生成音乐时才读 Stage 2 问询/生成切片；多音频用途不明、Cover/Remix 或独立混音时才读 `<workflowsDir>/_shared/audio-pipeline.md` |
| production-plan | intent Stage detail 的内部 Intake capsule、当前方向文档、最终 `main_song`；`<workflowsDir>/mv/reference/music.md` 的 Stage 3 切片、`<workflowsDir>/mv/reference/creative-and-plan.md` 的 Stage 3 切片、`<workflowsDir>/mv/reference/style-system.md` 共用段+已选风格段、`<workflowsDir>/mv/reference/typography-packaging.md` 共用段+已选 route 段+同步 Gate |
| assets | 已上传完整角色卡且本项目无新增人物/条件场景卡时，只读 production-plan Stage detail 与 `<workflowsDir>/mv/reference/assets.md` 顶部声明的“已完成角色卡快路径”切片，直接登记现有 runtime ref；不读用户可见制作计划、共享资产长文、生成版式段或风格/字体库。需要生成新资产时才读 `<workflowsDir>/_shared/asset-pipeline.md` 的共享核心段和默认资产切片；用户明确要求 Research 后才读 Research 切片及当前风格/字体路线 |
| shot-plan | production-plan Stage detail 中的 Treatment、节奏/空间/动作/插入策略、`style_anchor`、`typography_system`、文字账本、最终 `main_song_lyrics_timeline` 与时长权威，以及 Stage 4 当前资产；用户可见制作计划只在 capsule 缺必需 Treatment 或接受后被修改时补读相关段落。读取 `<workflowsDir>/mv/reference/storyboard.md` 当前全文；`_shared/stage-execution-plan.md`、style-system 共用底盘+已选风格+Stage 使用规则，以及 typography-packaging 共用合同+已选 route+音乐同步与 Gate 按上述复用规则只补读未覆盖切片 |
| visual-gen | 第一工具回合只读取 shot-plan Stage detail；真实返回有序 `document_node_id` 后，第二工具回合按顺序用 `hub_canvas_read_text` 完整读取一份或上下两份分镜到 EOF。来源读取 Gate 通过后才读取 production-plan、assets Stage detail、最终 `main_song` 和 `<workflowsDir>/mv/reference/video-generation.md`；Stage 5 已展开当前风格与字体，不重复读取 style-system、typography-packaging、未选路线或聊天摘要 |
| post | 按 `shot-plan.execution_excerpt` 读取一份或上下两份分镜的完整顺序/画幅/准确文字兜底、`main_song`、Stage 6 refs；`<workflowsDir>/mv/reference/post.md` 与 `<workflowsDir>/_shared/video-merge.md`；只有连续字幕兜底时读 `<workflowsDir>/_shared/subtitle-pipeline.md` |

## Stage Rules

- 每轮只 author 当前 Stage；当前 Stage 产物被接受后才添加下一 Stage。全部镜头组共用一个 `shot-plan`，全部视频共用一个 `visual-gen`，禁止版本号 Stage、汇总 Stage或逐组 Stage。
- Stage 1 首次进入时，先从用户原文和真实附件登记已明确的内容、音乐、参考素材、媒介、时长与画幅；随后先用 `working_language` 物化完整问询，再用一次 `question` 只弹出缺失卡和必问的视觉风格卡。已经有具体证据的项目不重复问，摘要或模型推断不能代填；回答后再写方向文档和合法扁平 Plan，不把嵌套 `intake_result` / `creative_core` 放进 `constraints` 或 `stage_fields`。
- Stage 2 只有 Agent 生成音乐时询问一次具体曲风与固定语义的“歌曲语言形式”卡；选项语义固定为带歌词（英文）、带歌词（中文）、纯音乐、用户输入语言，标题、题面、选项名和说明先整体物化成 `working_language`。内部歌词直接用于生成，不展示或确认。来源生成/复用、目标附近试听、整数终点、本地裁剪和末尾复听在同一 Executor turn 完成；窗口内没有自然落点时直接按目标整数秒裁切并短淡出，不询问用户、不退回 Planner、不重做歌曲。
- Stage 3 在同一次 author 中先从最终成歌物化逐句整数时间轴，再写用户可读制作计划与内部 Production capsule；不新增音乐分析 Stage、问询或 Executor 工作。实际时长、时间轴末行和制作计划必须为同一整数。
- Stage 4 默认直接生成角色卡；普通 MV 只写场景描述，只有叙事空间连续、用户锁定同一空间或跨派单动作依赖空间时生成场景卡。资产确认说明附一次可选 Research 邀请；用户继续即跳过搜索。
- Stage 5 先按最终整数时长计算全部 4–15 秒整数组，连续无缺口且总和严格等于音乐；12–15 秒非叙事高能组默认 10–15 个独立 Shot，电影叙事通常 4–8 镜。它锁定每个 Shot 的时间、场景/空间、人物或主体动作、景别/构图/运镜、歌词/口型、光影、材质、成像、VFX、准确文字、字体 Layer、切换和可见终态；每镜形成可直接被 Stage 6 投影的完整连续过程，但不写 refs 六段外壳或最终 Prompt 字符串。
- Stage 5 默认物化一份导演分镜；镜头组超过 6 组或预计正文超过 36000 字符时，只在完整镜头组边界近似均分为上下两份，两份仍属于同一个 `shot-plan` 并只确认一次。文档顺序、覆盖范围和交界终态写入 `execution_excerpt`。
- Stage 5 写完后复读真实项目 MD，核对全部文档、镜头组、Shot 和末行均存在，并在 `execution_excerpt` 记录有序文档节点/路径、每份 `contentHash` 与总行数、覆盖组、Shot 数和跨文档交界终态；不记录脆弱的 Prompt 起止行或字符复制 Gate。
- Stage 6 是最终 Prompt 的低自由度编译器：先取得 shot-plan Stage detail，再按真实 `document_node_id` 用 `hub_canvas_read_text` 逐份读到 EOF，记录来源节点、`contentHash`、总行数和 Shot 数；未完成全文读取时不得建立或提交 video work items。逐 Shot 完整投影 Stage 5 的导演、风格和字体事实，只补 refs、音乐切片与 H3 执行物理过程。最终 Prompt 从第一版起使用 `working_language`，准确歌词、片名、姓名和其它锁定文字保持原文；每组不得超过 7000 字符，不能合并、删减或概括 Stage 5 Shot。全部 Prompt 在同一 Production Board 一次确认，用户可在确认前直接修改最终 Prompt。
- `visual-gen` 固定 `concurrency_limit=1` 和严格 `sequence_index`。确认后 Executor 使用已确认 work item；Stage detail 因体积省略 Prompt 时，必须通过 `hub_plan_get_work_items` 按当前稳定 item id 取回完整已确认 Prompt，过大则缩小到单个 item。不得从 Stage 5、聊天或摘要重新编写，只有完整已确认 Prompt 缺失时才返回同一 Stage Planner 修复。
- Stage 7 在用户确认 Stage 6 结果后自动合成，完整主音乐覆盖代理音轨；只执行 Stage 3/5 已登记的准确文字兜底，不重新设计动态包装。

## Anti-patterns

- 为已完成 Stage 重复问询、重新分析或重新生成稳定产物。
- 视觉模式编造人物故事，或故事模式让风格改写人物、因果、地点功能和结局。
- 把未选 Research 图、旧资产、旧音乐或聊天摘要当成当前权威。
- 镜头组超过 15 秒、总时长不等于音乐、一个组拆成多个派单或逐组创建 Stage。
- Stage 5 只写概念摘要或包装接口，没有逐镜动作、运镜、歌词口型、光影/VFX、字体 Layer 和承接终态；或 Stage 6 合并/省略 Shot、改写已确认事实、生成短版 Prompt。
- 长片把镜头组拆到超过两份文档、上下篇重复/漏掉 Shot，或 Stage 6 没有按顺序完整读取两份正文到 EOF。
- 用户确认后并行提交多个长 Prompt、把后续组改成“见 Plan/Stage”的引用句，或让派单参数与已确认 work item 不一致。
- Stage detail 省略长 Prompt 后让用户拆文档、从分镜重建或从零散字段重新编写；应使用 `hub_plan_get_work_items` 取回已确认的原始 item。
- 用普通字幕、随机文字、单层贴字或 Post 重做动态大字，替代视频模型内的字体包装。
