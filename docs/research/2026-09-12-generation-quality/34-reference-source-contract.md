# 动态工作输出与精确媒体引用

2026-09-12，视频前真实调试；不属于空项目正式验收。

## 实际现象

《一起签》镜头素材规划 t28（`specialist-turn-0a7f99ed82094972b22c3306ae525a96`）在四项新构图中复用同一个会议室参考，同时提供 `source_work_item_id=wi-yqq-reuse-room` 和精确 `asset_id` / `asset_version_id`。06:09:37.219 的 `artifact.patch_asset_plan` 在执行前返回四处 INVALID_ARGUMENTS，无部分保存、无媒体任务。该语言请求实际输入 112,955、输出 6,550 tokens，用时 141.220487 秒。

三个来源字段没有任何 Schema description，互斥关系只存在于运行时 validator。原错误说明“二选一”，但没有说明工作依赖与具体图片的差别；对象级错误的结构化 `identifiers` 还遗漏错误对象本身的来源 ID。Agent 随后读取失败请求，06:11:05.669 自行修正并成功保存四项构图。这发生在修复加载前，只证明旧合同可在修正参数后合法保存，不能归为本次改进。

## 公开方法与取舍

[Anthropic 工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)建议让参数、工具职责和返回内容明确，并用真实轨迹追查无效往返。本次采用具体字段说明和可定位错误，不增加审批或额外检查阶段。

[MiniMax H3 公开方法](https://raw.githubusercontent.com/MiniMax-AI/cli/main/skill/h3-video/references/h3-video.md)区分有序媒体输入的实际职责，并要求动作初态与连续性清楚；本地保存的 [Stage/WorkItem 方法](../../../backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md)进一步将稳定工作 ID、依赖与运行时媒体对应起来。这里借鉴“明确输入身份与用途”的方法，没有声称闭源 Design、即梦或 Kling 采用本地同样的数据结构。

两类来源继续保留不同含义：`source_work_item_id` 跟随所选工作的输出；`asset_id + asset_version_id` 固定本次选中的文件。两者未来可能不同，不能为避免报错就静默选一个。工作依赖留在 `dependency_ids`，不要求靠同时填入两个来源来表达。

## 实现与验证

`AssetPlanReference` 的三个字段直接向真实 Tool Schema 说明两种合法填写方式、如何选择已存在的具体图片及依赖所在字段。错误信息保留原类别，并说明相应修复；不会推测调用者意图。通用错误反馈只补充出错对象自身的 typed selectors，以及 `item_id` / `source_work_item_id`，不回传整项 prompt。

两条真实 Tool 调用链回归已通过（0.52 秒）：在同一条引用中重现双来源错误，确认原计划逐值不变；分别按动态工作输出或精确现有 DRAFT 图片进行显式修复，确认实际保存保持所选来源、依赖和无关工作，没有生成媒体。Schema 从真实 registry 检查，非只测辅助函数。三个所改源/测试文件 Pyright 无错误，独立只读复核通过；错误反馈、既有 AssetPlan 与新引用回归合计 59 项通过（12.11 秒）；代码尚未统一加载到调试进程。
