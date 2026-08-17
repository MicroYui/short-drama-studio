# Phase 5：无人值守运行器与视频观察工具

## 结果

Phase 5 已把四种输入统一为一个生产入口：什么都不给、只给图片、一句话和锁定剧本。`AUTO_DRAFT` 会在没有中间用户操作的情况下完成结构化策划、输入资产登记、Mock 镜头生成、候选选择、`TimelineManifest` 和可选导出；`GUIDED` 仍可只建立可编辑项目。

本阶段同时补齐了 Agent 观察视频的通用工具。它不是为 S08 或门场景写的校验器，也不预设“应该推门还是拉门”。它读取当前 `ShotPlan`、ProjectBible、参考计划、视频时间序列和相邻镜头边界，输出可观察事实、置信度和通用 `QaIssue`，再交给 Director Agent 比较提示重试、换关键帧、拆镜或换覆盖机位。所有建议仍可忽略。

## 统一输入与规划

| 输入模式 | 最小输入 | 规划行为 |
| --- | --- | --- |
| `AUTO_ORIGINAL` | 无 | 内部生成一集真人都市短剧 |
| `IMAGE_SEEDED` | 至少一张图片 | 将可见人物、场景、道具、构图或风格作为视觉种子，再补全剧情 |
| `IDEA_EXPANSION` | 一句话或梗概 | 扩写为 45–60 秒结构化制片计划 |
| `SCRIPT_LOCKED` | 剧本文本 | 逐行保留说话人与对白，只规划镜头和制作 |

GPT-5.6-sol 可输出 `ProductionPlan`，包含台词、人物、地点、道具、风格、6–10 个镜头及每镜连续性起止状态。未配置、连接失败、JSON 不合规或锁定对白被改写时，系统自动使用明确标记的本地规划回退；不会把回退结果伪装成 GPT 输出。

用户图片会复制或登记为批准的 `REFERENCE` 资产，并进入每个镜头的通用参考绑定。`IMAGE_SEEDED` 将其标记为必需视觉种子，其他模式标记为可选参考；具体选图、关键帧和执行路线仍由后续 Agent 决定。

## `AutonomousRun` 状态

运行记录保存以下普通产品状态：

`NORMALIZE_INPUT → PLAN → IMPORT_INPUT_ASSETS → GENERATE → QA_AND_REPAIR → TIMELINE → EXPORT → COMPLETE`

状态只有 `RUNNING / COMPLETE / NEEDS_ATTENTION / FAILED`。Provider 缺失、可恢复任务或内容建议不被扩大成审批系统；普通草稿不会因为用户没有查看中间工件而暂停。

## 视频观察工具

### 1. `probe_video`

使用 FFmpeg 验证媒体可解码，并读取时长、尺寸、帧率和音轨。它负责技术事实，不评价剧情或表演。

### 2. `sample_video`

按视频总时长生成受上限控制的候选帧，保留：

- 镜头开始与结束；
- 覆盖完整时段的均匀样本；
- 与前一候选帧图像差异较大的变化点。

短镜头会取得更密的样本，长镜头覆盖更长时间但仍受 `max_frames` 限制。18 秒回归样例在不逐帧解码的情况下保留 12 张跨全时段样本。`VISUAL_CHANGE` 只表示画面变化较大，本身不是质量错误。

### 3. `inspect_video`

生成两类接触表：

- `INTRA_SHOT`：当前 Take 的时间序列，用于观察身份、构图、接触、朝向、道具和空间是否在镜内漂移；
- `SHOT_BOUNDARY`：上一入选 Take 的末段与当前 Take 的开端，用于观察剪辑点状态是否衔接。

配置 GPT-5.6-sol 后，观察器读取接触表和结构化制片工件，输出 `VideoObservation` 与通用 `QaIssue`。提示明确要求：稀疏抽帧不能证明某动作从未发生；不得凭空增加动作、松手、方向或空间要求；只有可见证据与已声明意图或时间顺序冲突时才报告问题。未配置模型时，技术工件仍保留，语义结论标记为 `UNAVAILABLE/UNKNOWN`。

报告保存在 SQLite `video_inspections` 与项目媒体目录中，桌面候选页可触发检查，QA 页可查看接触表、语义状态、问题数与摘要。

## Director Agent 连接

`DirectorAdviceRequest` 可以引用 `inspection_id`。系统会把该报告的问题与 Take 原有 QA 合并，再让 Director Agent 比较四种通用修复策略。观察器不直接修改镜头，Director 建议也不阻止用户继续原路线；`AUTO_DRAFT` 对没有通过 Take 的镜头最多执行两轮“建议 → 应用 → 旧 Take 失效 → 重新生成”，`GUIDED` 仍可应用或忽略。

## 当前证明与边界

- 已证明：四种输入契约、图片资产与参考绑定、无人值守 Mock 生成到 Manifest、被拒 Take 触发有限轮次 Director 修复并重新生成、视频工具持久化、长时段覆盖、相邻镜头边界接触表、语义问题进入 Director 建议。
- 未证明：观察模型对真人复杂动作的召回率、真实 H3 每镜自动选路、修复后自动重新生成到收敛、全真人单集质量。
- 本阶段未新增 H3 付费调用；Mock 结果只证明编排与工具链，不代表真人视觉质量。

## 验证

- 后端完整回归：631 passed；
- Pyright：0 errors / 0 warnings；
- TypeScript：`tsc --noEmit` 通过；
- Renderer、Electron main 与 preload：Vite production build 通过；
- OpenAPI JSON 与 TypeScript 类型已重新生成。

下一阶段把已验证的 H3 路线和 RouteEvidence 接入执行计划，使观察报告驱动真实但有次数上限的 `QA → Director → regenerate` 循环，并完成第一轮全真人单集。
