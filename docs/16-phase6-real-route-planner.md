# Phase 6：真实 H3 制片路线与整集复用闭环

## 结果

Phase 6 已把既有 `ShotPlan`、`ReferencePlan`、批准资产、H3 `RouteEvidence`、失败能力画像和 `VideoInspection` 编译为持久化的整集制片路线。它不再要求用户手工为每个镜头选 Direct、Frame 或 Reference，而是优先复用当前版本中已完成技术检查和人工质量审片的真实 H3 Take；只对缺口镜头编译新请求。

《别相信我》当前 9 个镜头全部找到了与现行剧本、Bible 和镜头修订号一致的已通过 H3 Take。因此本轮没有新增 H3 请求，新生成时长和估算费用均为 0。系统自动选入 9 条真实 Take、运行逐镜头和相邻边界观察、重建 `TimelineManifest`，并导出 57.13 秒的全 H3 单集。

## 路线计划工件

`H3ProductionRoutePlan` 与普通项目文档分开持久化，避免把 H3 特有 role 塞进通用 `ExecutionPlanEntry`。每个 `H3ShotRoutePlan` 保存：

- 当前剧本、Bible 和镜头修订号；
- `REUSE_TAKE` 或 `GENERATE`；
- Direct、首帧、首尾帧或 Reference 路线；
- 可执行提示词、H3 媒体绑定和已审证据 ID；
- 能力风险、建议关注的通用 QA 分类和观察策略；
- 缺失资产、可选首/尾帧准备 brief 和可忽略警告；
- 执行任务、观察报告和问题代码。

计划在执行前检查上述版本是否仍为当前版本。这是普通正确性保护，不是额外冻结、SHA 锁定或审批门。若用户不接受计划，仍可在 H3 实验台修改路线、媒体或直接忽略建议。

## 证据优先的选路顺序

1. 若存在当前版本、媒体可用、非合同冒烟、`REVIEWED + HARD_PASS` 的 H3 Take，直接复用。
2. 否则尝试复用当前版本中已通过的路线条件，但从现行 `ShotPlan` 重新编译提示词。
3. 否则使用 `ReferenceSelector` 绑定已批准参考包。
4. 无可用视觉输入时，Direct 仍是可执行回退；关键帧准备被标记为质量建议，不阻止用户继续。
5. 仅在存在多个真正可执行的缺口候选时，才让 GPT-5.6-sol 在候选集内比较；它不能发明路线、资产 ID 或供应商能力。

`EvidencePurpose` 新增 `PRODUCTION`，把正式制片任务与合同冒烟和质量实验分开。生产 Take 仍经过同一持久化提交、轮询、`SUBMIT_UNKNOWN`、`OUTPUT_MISSING`、人工/语义 QA 与 Director 修复能力。

## 长镜头和连续性观察

路线 Agent 不为“门”、“推/拉”或“区域穿越”新增领域字段。它只根据当前镜头的持续时间、动作节拍、空间交互、历史 QA 和镜头顺序编译 `H3InspectionPolicy`：

- 普通镜头保留开头、结尾、全时段均匀样本和高变化点；
- 时长越长，`max_frames` 在 6–24 帧范围内自适应提高；
- 除第一镜外，同时生成上一镜末段与当前镜开端的 `SHOT_BOUNDARY` 接触表；
- 语义审片仅比较可见证据和已声明意图，稀疏抽帧不能证明某动作没发生；
- 观察问题保存为通用 `QaIssue`，可交给 Director Agent，但不自动推翻既有人工通过结果。

本阶段还修复了真实媒体观察中的两个普通 FFmpeg 边界问题：精确抽帧先转为 JPEG 合法的 `yuvj420p`，并根据源帧率选择靠近片尾但仍可解码的时刻。

## 本次整集 dry-run

| 镜头 | 复用路线 | 时长 | 语义观察 |
| --- | --- | ---: | --- |
| S01 | `FRAME_FIRST` | 5.17s | 无问题 |
| S02 | `REFERENCE` | 8.00s | 低置信：推镜起点早于计划 |
| S03 | `REFERENCE` | 6.58s | 中置信：景别偏松、录音笔位置低于声明状态 |
| S04 | `REFERENCE` | 5.17s | 无问题 |
| S05 | `FRAME_FIRST` | 8.00s | 无问题 |
| S06 | `REFERENCE` | 6.58s | 中置信：末段视线未明显落向柜台 |
| S07 | `FRAME_FIRST` | 7.29s | 中置信：眉疤和反光节拍不够明显 |
| S08 | `FRAME_FIRST` | 5.17s | 无问题；持物、内侧把手和门扇关系稳定 |
| S09 | `FRAME_FIRST` | 5.17s | 无问题；背向走远和左手道具连续 |

这些语义问题是对既有人工审片的补充提醒，不是新的硬拒绝。S02/S03/S06/S07 不因此自动退出时间线；用户可以忽略、换 Take，或将对应 `inspection_id` 交给 Director Agent 比较重生成、换关键帧、拆镜和换机位。

## 真实时间线与导出

新 `TimelineManifest` 只包含 9 条 `provider=minimax / model=MiniMax-H3` 的当前、已通过 Take，总时长 57.13 秒。带字幕和无字幕成片已导出为 1080×1920 / 30fps / H.264 / AAC，并通过：

- 文件存在且可解码；
- 分辨率、帧率、音轨和时长检查；
- 带/无字幕版时钟一致；
- `TimelineManifest` 片段时长之和与成片时长一致。

导出目录：

`~/Library/Application Support/ShortDramaStudio/outputs/short-drama-projects/project-dont-trust-me/exports/project-dont-trust-me-manifest-v5/`

这证明了“已有真实证据 → 自动选路/复用 → 自动观察 → 全真实 Manifest → 技术导出”闭环。它还不等于完成 E007：尚需用户以完整单集而不是单镜头方式观看 57.13 秒成片，并在后续进行多次独立 `AUTO_DRAFT` 和盲评。

## API 与界面

- `POST /projects/{project_id}/h3/production-route-plans`：编译并持久化 dry-run；
- `GET /projects/{project_id}/h3/production-route-plans`：读取计划历史；
- `POST /projects/{project_id}/h3/production-route-plans/{plan_id}/execute`：复用已通过 Take，可选入队缺口生产任务，自动观察并在条件成立时重建 Manifest/导出；
- H3 页面新增整集路线面板，展示复用/新生成、路线、风险、抽帧量、边界观察、资产 brief、费用估算和可忽略问题。

## 未完成的通用闭环

- 对从零开始且没有可复用 Take 的项目，路线计划已能编译生产任务和关键帧 brief，但图像生成后的自动语义批准、H3 异步运行到候选收敛、以及 Director 定向重试的一键续跑仍需后续实现；
- 当前语义观察已经在真实 H3 视频上运行，但其漏报率、误报率和对最终观看意愿的预测能力仍是 `UNKNOWN`；
- 当前整集没有新增 H3 样本，因为所有镜头已有可复用证据；只有后续出现明确的能力空白或新修复假设时，才值得消耗新 H3 额度。
