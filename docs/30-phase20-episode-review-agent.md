# Phase 20：多分钟成片的整集 Reviewer 与 Director 闭环

> 历史阶段文档，仅记录当时实现，不是当前运行入口。现行架构见 [Phase 29](./40-agent-skill-tool-runtime-migration.md)，共享生产流程见 [`end_to_end.md`](../backend/short_drama/skills/workflows/end_to_end.md)。

## 目标与边界

逐镜视频抽帧可以发现人物、道具、动作和构图问题，相邻边界检查可以发现接续问题，但它们不能独立回答：故事是否容易理解、场次之间是否存在因果断裂、人物或道具状态是否跨场漂移、某段停留是否让整集节奏失去作用。

Phase 20 在已有 `TimelineManifest` 之后增加分层审阅：

`Scene Reviewer → Episode Reviewer → Director 建议/关联镜头局部返工 → 重建时间线`

它直接复用现有 `AutonomousRun`、VideoInspection、Take、Director、H3 Route Plan 和 TimelineManifest，不建立第二套 QA runtime。报告始终是可忽略建议，不是创意分数、审批门或导出门；模型缺失、输出失败或用户不同意都不会阻止交付。

本阶段不重新启用自动声音 QA。Scene Reviewer 只可在锁定剧本或已有可见证据已经说明声音来源错误时报告 `SOUND_SOURCE_SEMANTICS`，不会自动调用 STT、声纹或音频质量模型。角色参考音频仍是 H3 的可选输入能力，手动 AudioInspection 也继续保留。

## Harness 与上下文分层

### Scene Reviewer

每次只处理一个场次，Harness 注入：

- 当前场次目标与锁定对白；
- 按时间线顺序排列的 ShotPlan、入选 Take 和 clip 时长；
- 现有 Take QA 与最新 VideoInspection 观察；
- 最多 12 张对应镜头的抽帧接触表；
- 当前项目类型和目标时长。

Prompt 明确要求先报告证据再做推断，不允许固定节拍数、固定信息间隔、固定镜头数或为了“显得严格”而把润色偏好写成失败。没有证据的问题应保持沉默。

### Episode Reviewer

整集层不重复读取所有图片，只接收锁定剧本、时间线顺序和已经持久化的场次摘要/发现。它只汇总跨场或整集问题：故事可理解性、场次因果、跨场连续性、人物/道具状态、已有证据支持的声音来源语义，以及可关联到具体剪辑材料的节奏问题。

这种分层避免把 2–5 分钟所有帧和镜头一次塞进超长 Prompt，也让应用重启后可以从 `next_scene_index` 继续。

## 持久化工件与普通工程控制

`EpisodeReviewReport` 保存：

- 对应的 Manifest 和剧本版本；
- 已完成的场次审阅与下一场游标；
- 整集发现、证据、关联场次/镜头和可忽略建议；
- 当前阶段连续失败次数；
- 已应用或跳过的关联镜头返工；
- 汇总、警告、状态和模型信息。

确定性代码只做以下事情：

- 确认报告引用的是项目内真实存在的场次和镜头；
- 确认 Manifest 引用的 Take、抽帧文件和剧本版本可读取；
- 一次自动推进只执行一个场次审阅、一次整集汇总或一个镜头修复；
- 同一审阅阶段连续失败三次后保留 `FAILED` 报告并继续导出；
- 自动局部修复继续沿用每镜最多三次的普通防循环上限。

这些控制只保证执行正确、可恢复和不会无限付费重试，不判断作品是否好。没有 hash/SHA 冻结、答案键、评分阈值、强制批准或场景专用字段。

## Director 修复闭环

只有 finding 同时满足“明确关联镜头”和 `repair_recommended=true` 时，AUTO_DRAFT 才会把证据交给现有 Director Agent。Director 可以选择重生成、换关键帧、拆镜、换机位或不给出可执行修复；系统不会根据 finding 直接写死操作。

每次只考虑一个关联镜头。若 Director 应用了局部修复，受影响镜头和依赖按现有版本语义失效，Orchestrator 回到 `PLAN_ROUTES`，重新生成、检查并建立新的 TimelineManifest 和 EpisodeReviewReport。旧 Take 和旧报告仍保留，但不进入新正式时间线。若没有可执行修复、已达单镜上限或用户忽略建议，报告保留可见并继续完成交付。

## 离线闭环验证

验证使用真实 SQLite repository、持久化 `PlanningRun` / `AutonomousRun`、Fake GPT Image、Fake H3 媒体、VideoInspection、Director、TimelineManifest 与 FFmpeg，仅替换外部 Provider，不绕过状态机。

| 场景 | 预期与结果 |
| --- | --- |
| 5 分钟无问题 | 4 个 Scene Reviewer 各只接收本场 5 镜，随后执行 1 次 Episode Reviewer；没有 finding，不调用 Director，不为了凑返工修改镜头 |
| 关联镜头问题 + 重启 | 场次审阅保存一部分后重建 Service；从持久化游标继续，只修复明确关联的镜头 2，其他镜头 revision 不变，随后重新生成报告 |
| Reviewer 连续失败 | 同一阶段三次 Provider 失败后报告记为 `FAILED`；已有逐镜证据保留，运行不进入 `NEEDS_ATTENTION`，仍可导出 |
| 问题持续存在 | 同一镜头在每次新时间线仍被报告；恰好应用三次 Director 修复，第四份报告只保留建议并记录跳过，不再继续循环 |
| 无语言 Provider 的手动审阅 | 返回持久化 `UNAVAILABLE` 报告；现有 Manifest 保持可用，前端明确显示语义审阅为 UNKNOWN |

这些测试证明的是上下文边界、关联修复、持久化恢复和防循环语义，不代表 Fake 视频具有真实整集观看质量。

## 真实三版语言探针

在离线链路稳定后，从 Phase 8–10 的三版真实《别相信我》数据库建立只读克隆，只调用 GPT-5.6-sol；没有提交 GPT Image、H3、STT、声纹或音频质量任务。每版只有一个场景，因此分别执行一次 Scene Reviewer 和一次 Episode Reviewer。Prompt 中没有注入 A/B/C 的人工结论。

首轮结果：

| 人工版本 | Reviewer 结果 | 与人工结论的关系 |
| --- | --- | --- |
| C / Phase 8 Run 1 | 1 个 MEDIUM：镜头01同时出现手持红色录音笔和柜台上的第二个近似红色物体 | contact sheet 中确实可见；用户此前认为 C 无明确严重问题，与 MEDIUM、可忽略语义不冲突 |
| B / Run 2 | 3 个可见连续性发现：录音笔外观变化、镜头03→05放置状态重置、镜头06→07抬眼动作重置 | 都有抽帧证据；但没有召回人工指出的“录音笔播放变成女主本人说话”，因为现有静态证据不能确认声音来源且本阶段不运行声音 QA |
| A / Run 3 | 1 个 MEDIUM：镜头03→05录音笔放置动作重置 | 未召回人工指出的首镜竖向空间拼接问题，构成真实漏报 |

随后做了一次不含项目名或目标镜头的通用 Prompt 对照，只增加“关注场景几何、拼接和背景连续性”的普通视觉清单。结果仍未召回 A 的竖缝，报告数量反而从 `1 / 3 / 1` 增加到 `3 / 4 / 4`，并产生相近诊断重复。因此该 Prompt 改动已撤回，没有为了救单一样片进入默认策略。

真实探针支持的结论是：分层 Reviewer 能汇总若干真实、可定位的道具和动作连续性问题，但单次多图语言审阅的召回和稳定性不足，尤其不能替代逐镜观看或声音来源验证。它应继续作为 Director 的可忽略建议和回归证据，而不是质量判定器。

## 结论与后续

Phase 20 把“抽帧工具应该由 Agent 使用”落实为一条可恢复的整集闭环：工具产出观察证据，分层 Reviewer 汇总问题，Director 比较修复策略，Orchestrator 只执行关联局部变更并重建时间线。用户默认不需要参与中间过程，同时仍可查看或忽略所有建议。

下一阶段若继续提高视觉召回，应改进可复用的逐镜观察 Tool、证据分辨率或 Agent 调用策略，并用跨剧本回归集比较，不再围绕 A 的竖缝增加提示词。声音来源语义保持当前边界：只有未来用户重新要求该类 QA 时，才设计能观察时间、画面和声音来源关系的独立工具。
