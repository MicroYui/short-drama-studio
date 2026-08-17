# Phase 3：来源中立资产工作台与 GPT Image 2

## 目标与边界

Phase 3 在现有 `ShortDramaService + SQLite + Product API + Electron` 主线上补齐正式资产生产，不增加第二套工作流或任务运行时。

首版支持：

- 角色、场景、道具和通用参考媒体；
- 本地文件导入、外部 URL 导入、GPT Image 2 新生成和基于既有版本的编辑；
- 稳定 `Asset` 身份、递增 `AssetVersion`、父子派生、候选/批准/拒绝状态和普通来源记录；
- 供应商中立 `ImageProvider`，首个实现为 OpenAI `gpt-image-2`；
- GPT Image 2 与 GPT-5.6-sol 分别支持用户填写连接地址与 API Key；模型名固定，不增加候选模型选择；
- 本地连接配置、持久化图像任务、媒体落盘、用量与费用估算、失败与输出恢复；
- 桌面端预览、版本历史、生成/编辑、批准/拒绝和任务重试；
- 为《别相信我》建立林夏、周衡、雨夜便利店和红色旧录音笔的首批可审片候选。

首版不实现内置素材库搜索、登录、权限、团队协作、发布流程或第二个图像/视频模型。外部素材库文件仍可通过本地文件或 URL 导入。

## 领域模型

`Asset` 是跨镜复用的稳定身份；`AssetVersion` 是某次具体素材：

- `kind`：`CHARACTER / LOCATION / PROP / REFERENCE`；
- `current_version_id` 指向最新工作版本；
- `approved_version_id` 只指向审片通过的正式版本；
- 新导入或新生成版本一律为候选，不自动批准；
- `parent_version_id` 表达上传后修整、候选再编辑等普通派生关系；
- 来源记录保存导入方式、原文件名或 URL、Provider、模型、提示词和创建时间，不增加额外哈希锁或证据链；
- 只有包含可用本地路径或外部 URL 且状态为 `APPROVED` 的版本，才可作为后续 H3 正式 Frame/Reference 输入。
- H3 请求保留 `asset_version_id`，服务端重新确认它正是资产当前批准版本，再解析真实路径或 URL；手工路径和 URL 仍可用于实验。

## 图像任务与恢复

图像任务与视频任务共享同一项目和 API，但使用独立的 `image_tasks` 表，避免把镜头、时长和视频状态字段硬塞进资产任务。

状态为：

`QUEUED → SUBMITTING → SUCCEEDED`

以及 `FAILED / CANCELLED / SUBMIT_UNKNOWN / OUTPUT_MISSING`：

- 请求在本地持久化后才进入 `SUBMITTING`；
- 明确 4xx 为 `FAILED`；网络中断、超时或 5xx 使提交结果不可判断时记为 `SUBMIT_UNKNOWN`，不自动重复付费调用；
- Provider 已返回但最终文件写入或解析失败时记为 `OUTPUT_MISSING`，优先从本地 staging 文件恢复；
- 显式重试建立新任务并保留旧任务，不覆盖历史；
- 成功只创建 `DRAFT` AssetVersion，必须由用户审片批准。

## ImageProvider

通用请求只表达生成/编辑意图、提示词、尺寸、质量、背景和输入版本；OpenAI Adapter 负责转换到：

- `POST /v1/images/generations`；
- `POST /v1/images/edits`；
- 模型固定 `gpt-image-2`；
- 编辑使用高输入保真，并支持多个本地 Data URL 或外部 URL；
- 默认输出 PNG、高质量，尺寸允许使用与 H3 Frame 路线一致的 `768x1344`。

多图编辑还可选声明 `primary_input_version_id`。指定后，工作台将该图作为第一张输入，关键帧 brief 将它解释为需保留机位、空间布局、对象数量、机械连接、左右方向和手部接触面的主画布；其他输入只用于补充身份或外观。该字段不是所有编辑的强制规则，桌面端允许明确选择“不指定主画布”。

OpenAI 当前响应包含图像 Base64 和 token usage。费用记录按调用时快照估算：标准调用中图像输入 `$8/1M tokens`、文本输入 `$5/1M tokens`、图像输出 `$30/1M tokens`；同时保留原始 token 数，实际账单仍以 Provider 为准。

Azure OpenAI 使用独立路由解析：用户可直接填写 `/openai/deployments/{deployment}/images/generations?api-version=...` 完整 endpoint，Adapter 保留部署名和查询参数，编辑时只把末尾切换为 `/images/edits`，并使用 `api-key` 而非 Bearer 头。GPT Image 的 Azure 官方文档要求 `2025-04-01-preview` 或更高版本；配置中更早的日期版本会在保存时升级到该最低版本。

2026-08-15 真实探针表明当前 Azure `gpt-image-2` 部署不支持 `background=transparent`，会返回 `invalid_value`。桌面端在 Azure endpoint 下禁用透明选项，后端也会在付费请求前拒绝该组合；道具默认改用深灰中性背景。

## Provider 连接

- GPT Image 2：支持标准 OpenAI Base URL 和 Azure 部署级完整 endpoint；前者用 Bearer 头，后者用 `api-key`；
- GPT-5.6-sol：支持 OpenAI-compatible `/v1`。验证先查 `GET /models/{model}`，遇到 404/405 再查 `GET /models`并在列表中确认 `gpt-5.6-sol`；
- `localhost / 127.0.0.1 / ::1` 回环地址允许无 Key 连接，远程语言 endpoint 仍必须配置 Key；
- 标准图像地址通过模型查询验证；Azure 图像 endpoint 使用缺少有效 prompt 且 `n=0` 的 schema 无效探测，使 Azure 检查凭证、部署和 API 版本，但不生成图像；
- API 响应只返回是否已配置及来源，不回传密钥；用户可只更新 URL 而保留既有密钥，也可只更新密钥而保留 URL。

## 首批资产验收

首批候选至少包括：

1. 林夏：短黑发、米白风衣、克制警觉的真人身份参考；
2. 周衡：深灰西装、左眉浅疤、表面平静的真人身份参考；
3. 雨夜便利店：右侧玻璃门、左后收银台、冷白顶灯和蓝色雨夜反光，标牌无文字；
4. 红色旧录音笔：紧凑矩形录音设备、磨损红壳和小指示灯，明确禁止天线、对讲机轮廓和文字 Logo。

每个结果需要能在桌面端打开、查看来源与父版本、批准或拒绝。交付时保持 `DRAFT`，由用户确认后才成为正式 `approved_version_id`。

### 2026-08-15 真实生成结果

Azure 连接和本地 GPT-5.6-sol 连接都已在打包桌面应用中通过无生成验证。随后完成 5 张真实 GPT Image 2 候选：

| 资产 | AssetVersion | 初步视觉结论 | 估算费用 |
| --- | --- | --- | ---: |
| 林夏定妆 | `asset-version-8b1cc2751da5` | 短黑发、米白风衣、警觉表情和雨夜便利店光线正确，手部与服装自然 | `$0.120815` |
| 周衡定妆 v2 | `asset-version-23d828459396` | 写实气质成立，但左眉浅疤方向不够明确，保留为对照 | `$0.120975` |
| 周衡定妆 v3 | `asset-version-72fb5bc022a3` | 观众画面右侧、人物本人左眉的浅疤清楚，气质与服装正确 | `$0.121165` |
| 雨夜便利店 | `asset-version-a07499c3f496` | 右侧玻璃门、左后收银台和冷蓝雨夜空间正确，无可读店名或乱码 | `$0.121105` |
| 红色旧录音笔 | `asset-version-8bb3afb34169` | 细长数字录音笔轮廓，无天线、无品牌文字，不再像对讲机 | `$0.211495` |

成功请求估算合计 `$0.695555`；另有一次透明背景请求在 Provider 立即返回 `invalid_value`，无图像、无 usage 与费用记录。五张图均已落盘且保持 `DRAFT`，四个资产的 `approved_version_id` 均为 `null`，未自动进入 H3。

## 验证

- Provider 离线合约：标准 OpenAI 和 Azure endpoint/鉴权、旧 Azure API 版本升级、本地模型列表降级、生成、编辑、多输入、Base64、错误分类、usage 与费用计算；
- SQLite/API：导入、版本派生、批准替换、任务重试、重启恢复和媒体文件存在；
- TypeScript/Pyright、后端整库、OpenAPI 生成和 Vite 构建；
- 实际桌面交互：保存/验证凭证、导入、生成/编辑、预览、版本审片；
- 首批四类候选在用户批准前均不得自动进入 H3 正式路线。
