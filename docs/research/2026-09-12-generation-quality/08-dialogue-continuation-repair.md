# 跨时段连续对白的 H3 编译修复

## 实际问题与当前内容语义

本次缺陷来自暂停调试项目《一起签》的第 1 镜：`shot-yqq-01` r1 长 5.5 秒，0–2.2 秒与 2.2–5.5 秒都引用 `line-yqq-01`。正文明确要求同一句连续完成、只发声一次；台词为“报价出错了。说明你签一下，就说你没核对。”。来源为[导演原始写入请求](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182002442353-37029e53-model.output.json)。

[ShotPlan.validate_timeline_beats](../../../backend/short_drama/models.py) 已将按时段展开后相邻的同一 `line_id` 合并，要求合并后的顺序与镜头对白顺序一致。因此它表示一句跨时段延续，而非两次朗读。旧 H3 确定性编译器却在每个 beat 写出完整 `<d>`；单镜校验也要求每个 beat 包含完整原句。导演内容本身可以正确，后续编译却主动制造重复，还可能拒绝语言编译器给出的正确连续表达。

本轮保持原内容合同：A→A 是一次；A→B→A 是三次，其中 A 说两遍；不同 ID 即使文字相同也分别发声；空白 beat 不改变当前模型的展开序列，因此 A→空 beat→A 仍是一次。没有跨镜自动合并，也没有修改当前项目、剧本或旧 Trace。

收尾复核还发现同一编译边界的声线缺陷：`_dialogue_description` 过去在每个镜头重新从 S1 分配。当前第 1 组中的赵衡和许岚分别在镜 1、镜 3 发声，因此都会被标为 S1。两个当前 ScriptLine 均未绑定 `speaker_character_id`；保留人物名字不能消除同一请求内的声线编号冲突。本轮一并修复。

## Primary research 与采用范围

- [MiniMax-H3 官方 base-en §4.4，固定上游提交 d21241f](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt)：对白标记内部承载语言与实际说出的内容，角色和声音身份在标记外说明，同一请求中的声线编号跨镜保持稳定；一句话跨真正的切镜时，使用专门的连接标记并说明音频连续。本次是同一镜头内的阶段变化，不应制造第二次发声，也不应添加虚构的切镜标记。
- [MiniMax-H3 官方 ref-en](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt)：参考生成沿用同一对白语法；完整对白放在叙事正文，声音总览不再次逐字复制。由此采用“一次实际发声对应一个完整对白标记”，不按 beat 数或原文去重。
- [Runway 官方 Image to Video Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)：参考图提供外观与构图，文字应清楚表达运动、先后与时间关系。这里借用其连续动作表达原则，保留原 beat 的可见动作、表演和声音关系；没有把 Runway 的产品能力或语法当成 H3 能力。

此结论针对已存在的分镜与 H3 语法映射，不新增创作审批、固定对白速度、自动拆句或必须重写分镜的步骤。未尝试猜测每个字在连续一句中的精确发声时刻。

## 实施

- [h3_dialogue_spans.py](../../../backend/short_drama/providers/h3_dialogue_spans.py) 从当前 ShotPlan 临时推导每次发声的 line ID、首末时间和开始/延续 beat 索引，供确定性编译、语言输入与校验共同使用；没有第二份保存状态。
- [h3_prompt_skill.py](../../../backend/short_drama/providers/h3_prompt_skill.py) 的单镜和整组编译在开始 beat 写一次完整台词，声明其覆盖范围，后续 beat 只标明同句延续。范围是现有 beat 的容纳窗口；原 `sound` / `audio_intent` 中更细的发声起止继续保留，不强迫说满整个窗口。
- 校验检查实际 `<d>` 次数、原文和顺序，防止“原文仅在解释文字里出现”被当成保留台词；拒绝重复、漏句、移到错误的显式时段，以及声音总览里的第二次对白。单镜保留原有 beat 标记合同。整组继续支持既有 `[Shot N]` 窗口内的自然段表达：始终核对镜内发声次数和顺序，只有完整 beat 标记已存在时才进一步检查开始位置，不新增每个 beat 必须精确列标记的格式门。
- [h3_language_prompt.py](../../../backend/short_drama/providers/h3_language_prompt.py) 给单镜补全 `locked_dialogue.id`，两种输入都增加短小的 `dialogue_spans` 映射。整组映射使用生成片段绝对时间，原 beat 局部时间不改写。连续发声规则放在两种 payload 的 `production_rules.dialogue`，LANGUAGE 和已有 Context IR 复用路径都能收到，避免 Context IR 只复用事实时丢掉系统指令里的语义。
- 单镜、整组确定性编译与语言输入共用 `request_speaker_ids`。编号按本次请求的实际镜头窗口顺序分配，有角色实体绑定时以绑定为准；不同实体即使同名仍分开，无绑定名称只在本次请求内按精确字符串分配声线，不关联已有同名实体、不创建 Bible 对象。`locked_dialogue.speaker_id` 明确交给语言编译器，跨镜不重置。
- 对已明确写出的直接发话标注检查声线冲突，同时兼容历史仅写名字、以及使用其他稳定编号的 Prompt。独立复核曾发现“发话人 S1 看向沉默听者 S2”会被最近标签猜测误判；已删除该猜测，只处理能明确解析归属的直接发话语法。复杂自然段不推断听者为说话人，也不强制作者改成某种格式。

## 离线验证与验收边界

[针对性回归](../../../backend/tests/test_h3_dialogue_continuation.py) 覆盖真实原句跨两个 beat 的单镜/整组 DIRECT 与 REFERENCE 编译、两条语言校验入口、重复/漏句/错误位置/声音总览重复、A→B→A、不同 ID 同文、空白 beat、无 beat 旧镜头、组内时间偏移和既有整组自然段格式。

新增声线回归覆盖多角色跨镜、传入镜头列表倒序、同名不同实体、实体与无绑定同名不合并、同实体改显示名、请求局部编号、明确声线冲突、无编号/其他稳定编号旧正文及带听者编号的合法自然段。实际运行上述新文件与 `test_h3_generation_unit_prompt.py`、`test_h3_generation_unit_language.py`、`test_language_provider.py`、`test_mature_skill_compilation.py`、`test_h3_subject_reference_truth.py`、`test_h3_composition_references.py`、`test_h3_context_ir.py`，共 **138 passed / 8.88 秒**。三个改动源文件的本地 Pyright 为 **0 errors / 0 warnings**。同时同步了旧语言输入测试对既有 `image_reference_role: null` 字段和新增对白规则的期望。

只读打开实际 SQLite 数据，用纯编译函数重建当前第 1 镜及当前已保存的第 1 组；没有通过 Service 执行保存，没有上传媒体或调用 Provider：

| 当前内容 | 编译结果 |
| --- | --- |
| 第 1 镜，5.5 秒设计 / 6 秒请求 | DIRECT 2,266 字符，REFERENCE 2,474 字符；两者均仅一个对白标记，校验通过。 |
| 第 1 组 `clip-yqq-g01`，镜 1–3，12.5 秒设计 / 13 秒请求 | DIRECT 5,886 字符，REFERENCE 6,056 字符；两者均为两个真实对白标记，第 1 镜原句只出现一次；赵衡为 S1、许岚为 S2，校验通过。 |

上述 REFERENCE 验证未加入真实媒体上传请求，只验证当前内容的 Prompt 编译与校验行为，不代表真实 H3 生成、唇形、声音连续性或画面质量已通过。当前项目继续只属于调试；所有视频前链路完成整改后，仍需按用户要求从空项目重新进行完整前置 E2E，前置成熟前不提交 H3。
