# Phase 9：自动音频、对白与声线 QA

## 目标与边界

Phase 9 在 Phase 8 的 `manifest-v3` 和 H3 原生音轨之上补齐导出前的自动音频检查，不重新生成视频，也不把 Azure、外部 TTS 或某个 STT Provider 设为必经步骤。系统以 `AudioSemanticService` 协议接收任意语音实现；桌面版当前默认使用按需加载的本地 Whisper-small 与 WavLM speaker-verification 模型，未配置语义服务时仍完成 FFmpeg 音轨、信号和字幕时间检查。

检查结果是通用 QA 证据：`AUTO_DRAFT` 会自动运行、保存并继续到导出；问题进入运行告警和 Director Agent 上下文，用户仍可忽略、换 Take、重生成、替换音频或维持原方案。音频检查器不直接决定门、区域穿越或其他场景专用修复，也不会因为建议未采纳而阻止草稿完成。

## 已实现闭环

| 能力 | 实现 |
| --- | --- |
| 音轨提取 | 对 `TimelineManifest.audio_clips` 逐镜使用 FFmpeg 提取 16 kHz 单声道 PCM，并保存可复查 WAV |
| 信号诊断 | 时长、峰值/RMS、削波比例、静音窗口、首尾静音和能量型语音区间 |
| 字幕时间 | cue 是否位于 Manifest、是否映射到音轨、是否重叠、阅读速度及对白窗口语音覆盖率 |
| 锁定对白 | 对每个 subtitle cue 提取对白窗口，经 `AudioSemanticService` 转写后与 `ScriptVersion.spoken_text` 比较 |
| 文本归一化 | NFKC、大小写与标点过滤、繁体转简体；数字和否定词仍保留为关键差异，不用模糊相似度掩盖语义变化 |
| 说话人/声线 | 先核对 cue 与锁定剧本的说话人标签，再对同一预期角色的多镜声纹向量做相对一致性与跨角色分离诊断 |
| 自动编排 | 真人与 Mock `AUTO_DRAFT` 均在 `TIMELINE` 后进入 `AUDIO_QA`，再继续 `EXPORT` 或完成 |
| Agent 连接 | `AudioInspectionReport` 持久化到 SQLite；Director Advice 可读取指定镜头的音频问题并给出可忽略建议 |
| 产品界面 | QA 页展示报告、逐句转写、归一化后逐字结果和跨镜声线结论，也可手动重跑当前时间线 |

本地默认模型通过环境变量替换：

- `SHORT_DRAMA_TRANSCRIPTION_MODEL`，默认 `openai/whisper-small`；
- `SHORT_DRAMA_SPEAKER_MODEL`，默认 `microsoft/wavlm-base-plus-sv`，置空可关闭声线模型；
- `SHORT_DRAMA_AUDIO_DEVICE`，默认 `auto`，Apple Silicon 优先 MPS，否则 CPU。

模型在第一次语义音频检查时才加载，不增加桌面服务的固定启动时间。协议边界允许以后接 Azure STT、其他本地模型或已验证的 Provider，而不改 `TimelineManifest`、报告结构和 `AUTO_DRAFT` 状态机。

## Phase 8 真实成片复检

2026-08-18 使用已经存在的 9 个 H3 768P Take 和 `project-ab654bb32260-manifest-v3` 运行检查，没有提交新的 H3、GPT Image 或语言模型生成任务。

| 项目 | 结果 |
| --- | --- |
| Project / Manifest | `project-ab654bb32260` / `project-ab654bb32260-manifest-v3` |
| AudioInspection | `audio-inspection-678c3700161f4331b9568f6ded20d701` |
| 音轨 | 9/9 可解码，时长、削波、静音和 Manifest 映射技术检查 0 失败 |
| 锁定对白 | 5/5 归一化后逐字一致，CER 均为 `0.0` |
| 林夏 | 3 组同角色比较，最低相似度 `0.827002`，其他角色最高 `0.504654`，`PASSED` |
| 周衡 | 1 组同角色比较，最低相似度 `0.795320`，其他角色最高 `0.504654`，`PASSED` |
| 最终问题/告警 | 0 / 0 |

第五句被 Whisper 转写为繁体“現在你還信自己嗎?”。初版比较会误判为差异；这次没有为样片写特殊答案，而是在通用归一化层加入繁体转简体，并增加回归测试。修复后它与锁定文本“现在，你还信自己吗？”正确判为一致。

## 结论边界

- `5/5` 是这一部已通过成片的真实结果，不代表 H3 在其他剧本、语速、多人抢话或模型版本下已经达到稳定逐字率；E007 仍需另外两次独立运行。
- “逐字一致”指去除不可听出的标点、统一繁简体后的字符一致，不声称 ASR 可以验证标点表演。
- WavLM 结果证明本片中按剧本标签分组的多段声音相对一致且与另一角色分离；它不是生物识别，也不能在没有已登记角色参考音频时证明现实中的具体身份。如果一个错误声音始终稳定地说完某角色全部台词，仍需要参考音频或人工听审识别。
- 能量型语音区间可发现静音和明显时间错位，不能独自区分对白、音乐与环境声。雨声、呼吸、表演节奏、主观音色和口型仍由听审、视频观察或后续专门工具判断。
- 本轮结果支持继续保留 H3 原生音频作为当前基线，不构成永不需要 reference audio、真人录音或外部 TTS 的结论；只有后续证据显示它们能提高最终音画质量时才接入。

## 工程验证

- 新增 Fake `AudioSemanticService`、持久化、HTTP、Director 连接、削波和繁简体归一化测试；
- `tests/test_audio_inspection.py` 与 `tests/test_real_autonomous_draft.py`：`14 passed`；
- 后端全量：`665 passed in 111.03s`；
- TypeScript 与全量 Pyright：0 errors；
- OpenAPI JSON/TypeScript 类型重新生成成功；
- Vite 前端、Electron main 与 preload 生产构建成功。
