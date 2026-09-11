# 同一生成组内，一句对白跨画面切换

## 实测问题

2026-09-12 的《一起签》调试中，导演将第03镜拆成2.5秒出示报价单和1.5秒静态证据，期望许岚的同一句话连续覆盖4秒。第一次 `shot.patch_storyboard`（Tool `tool-3926353e7bd6`，event2983）在两镜中保留同一对白ID，受到 Storyboard 全局唯一性校验拒绝，未部分保存。原参数不符合旧合同，但该创作意图本身合理。

04:30:07 的修正候选把ID仅留在前镜，并在两镜 `audio_intent` 中说明跨切持续。对实际 `model.output` 保存内容作纯函数重放，`shot_dialogue_spans` 得到前镜 `line-yqq-02` 的0–2.1秒窗口，证据镜没有跨度。这与文字声明的4秒声桥冲突。若只放宽全局唯一性，原组编译器和验证器又逐镜要求完整 `<d>`，会将一句话变成两次发声。因此本次修改同时修正结构覆盖、H3输入、编译、验证与默认字幕。

该证据来自 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T203006581794-4d6dac7e-model.output.json`；没有改写原项目、原Trace或替导演补写已保存候选。

## 成熟实现依据

- [Adobe Premiere 的 J/L cut 文档](https://helpx.adobe.com/premiere/desktop/edit-projects/trim-clips/perform-j-cuts-and-l-cuts.html)明确将声音和画面在不同时间切换作为对话剪辑方法。L cut 可在切到下一画面后延续前一段声音。
- [Final Cut Pro 的 split edit 文档](https://support.apple.com/guide/final-cut-pro/create-split-edits-ver1632d82c/mac)直接举例：切到听者画面时，上一位说话者的对白继续。编辑保持媒体同步，同时独立调整声音与画面边界。
- [MiniMax H3 官方 base guide §4.4](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt#L125-L142)要求跨镜保持说话人编号，说明跨切连续声音，并为一句话跨切提供 `<scenetrans>`；完整T2VA示例在首镜写一个完整对白块，第二镜说明尾词延续。它没有要求每个视觉镜头都重复整句。
- [H3 reference guide §5.1/5.4](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt#L213-L278)沿用同一对白和跨切语法，区分主体、声线与实际音频引用。

这些资料证明表达方式和公开接口的适用性，不证明某一次 H3 生成一定能正确发声。MiniMax Design、即梦及Kling的内部对白数据模型和调度实现没有公开依据，本次不推测或仿造其私有实现。

## 实施

仍以当前 Shot/beat 的 `dialogue_line_ids`、ClipGroup 顺序、ScriptLine 原文为内容真相；没有增加另一份人工填写的声音时间表。

1. `dialogue_sequence.ordered_dialogue_line_ids` 在同组相邻镜的边界折叠同一ID，作为一次对白的剧本覆盖。Storyboard、scene、分组草稿、草稿提交/收尾及导演局部修复共用该语义。空镜与组界打断覆盖；不同ID同文不合并，A→B→A仍有三次发生，原逐次覆盖合同继续识别不合法重复。导演/schema说明要求续接镜和实际承载beat保留同ID，不能只写自然文案、留空ID或为延续发声虚构新的剧本行。
2. `providers/h3_dialogue_spans.request_dialogue_spans` 派生一次请求的发声跨度：起止为该次发生覆盖的首尾实际beat时间，每个 `part` 保留镜头内坐标与beat索引。`generation_unit_dialogue_spans` 根据实际 source windows 排序并加偏移；不按入参数组或显示镜号猜顺序。空beat保留原单镜语义，整个空Shot不隐含延续。
3. H3确定性编译在起始镜写一次完整 `<d>`，后续镜保留视觉边界并说明同一声音继续；只在真实跨镜处加 `<scenetrans>`。完整原句不拆字、不猜切点恰好说到哪个词。语言编译输入将完整对白正文放在起始镜，后续用派生的 `continuing_dialogue` 关联同一次发声。默认语言路线与可选官方 Context IR 共用输入和校验。
4. 组验证器按请求中的实际发声次数、原文、顺序和起始镜校验；续接镜不再要求重复 `<d>`。旧组prose仍只需原有Shot窗口，不新增“每个beat都必须打印精确marker”的门。不同ID同文及真正复述仍分别校验，稳定声线规则不变。
5. 当前正式分镜中的连续一句不能通过单镜编译、手写probe override、旧逐镜route或已排队任务拆成独立请求。入口在调用语言/视频前给出 `H3_DIALOGUE_REQUEST_INCOMPLETE`，要求使用完整当前生成组；跨两个独立组的同一句需调整组边界，或使用已经取得的连续音轨进行编辑。旧未分组projection不重新解释为声桥。Provider请求字段、模型、分辨率/画幅和提交恢复协议未改。
6. 默认字幕按正式ClipGroup逐组派生：跨镜句只生成一个暂定caption，按各part的镜内时间映射到实际采用片段并截到片段边界；不同ID同文仍为两条。无分组的legacy入口保留原逐镜行为。实际音频对齐和用户剪辑仍走既有工具，此处设计时窗不声称已经听审。

本地 H3 Skill 同步一次发声/跨切说明；上游原文与归属未改。还移除了本地两处“对白原文写进overall_soundscape”的矛盾说明，使其与公开H3格式及现有校验一致。

## 验证及边界

- 新H3回归覆盖真实2.5+1.5秒拆镜、非零beat起止、颠倒入参数组仍按source windows、五种现有路线、一次对白、重复/漏写/错移拒绝、无全beat marker的旧prose、不同ID同文、A→B→A及空镜。
- 实际本地服务回归执行typed局部patch→组准备→保存Prompt/Route→重建Service→enqueue，得到一个任务和正确的两镜窗口；Fake语言仅编译一次。单镜编译/probe/route及旧任务窗口缺失不会绕过合同。没有发送任何真实Provider请求。
- 结构侧专测覆盖 scene/draft/assemble、Tool局部split与跨组失败的原子保存、Draft追加→用户编辑→导演修复→finalize；默认字幕覆盖跨镜一次显示、短采用区间、同文不同ID、无ID不因prose扩展。
- 最终9文件联合124项通过（12.45秒）：`test_h3_cross_shot_dialogue.py`、`test_h3_dialogue_continuation.py`、`test_h3_generation_unit_language.py`、`test_h3_group_operation.py`、`test_h3_context_ir.py`、`test_h3_generation_unit_worker.py`、`test_official_minimax_h3_skill_integration.py`、`test_timeline_dialogue_continuation.py`、`test_storyboard_dialogue_continuation.py`。命令为在 `backend` 使用 `.venv/bin/python -m pytest` 执行这9个文件。7个相关Python源码/测试文件Pyright无错误、无警告，所改文件 `git diff --check` 通过。

当前修复保证结构与请求表达一致，不能由编译通过推断声音质量通过。下次真实成片需对实际原声检查：原句是否完整且只说一次、切画面时声音是否持续、声线是否一致、字幕是否与实声匹配；静态接触表不能代替听审。已经保存的“仅文字声桥”候选仍需产品Agent通过正常局部修改把覆盖ID写回，运行数据不会在代码升级时自动猜改。
