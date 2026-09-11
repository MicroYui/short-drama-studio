# MiniMax H3 能力与任务生命周期说明

本文件供维护者和路线规划 Agent 审阅，不自动注入 Prompt Compiler。原因是模型能力、参数限制和 API 枚举会变化，应由 Provider Adapter、Tool Schema 和 Capability Profile 保持最新，而不是混入格式 Skill 或每次提示词。

## 官方当前描述

截至 2026-09-01，MiniMax 官方文档描述：

- `MiniMax-H3` 支持文字、关键帧和多模态参考输入，4 至 15 秒，768P 或 2K，支持原生音频；
- `MiniMax-H3-Max` 的能力与分辨率范围不同，不能假定与 H3 完全互换；
- 请求必须包含非空文字 Prompt；关键帧引导与全参考引导不能混在同一请求；
- H3 支持包括 9:16 在内的多种画幅；不同输入模式对画幅参数的处理不同；
- 视频生成是异步任务：提交获得 `task_id`，之后查询状态，成功后再取得结果。

官方概述页面与 API 枚举偶尔可能存在时间差。例如概述可能提到某种关键帧能力，而当前请求枚举尚未暴露。实现以真实 Tool Schema、Provider 返回和已验证测试为准，不根据宣传文字猜测参数。

2026-09-12 核对当前接入的 [AceData 视频 API](https://github.com/AceDataCloud/MiniMaxAPI/blob/main/docs/videos.md)：`MiniMax-H3` 请求支持 `768P` 与 `2K`；画幅支持 `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4` 和 `9:16`。纯文本路线指定固定画幅；首尾帧路线省略画幅或使用 `adaptive`，画面比例由实际输入图确定。REFERENCE 可使用固定画幅或 adaptive。组准备、单镜编译和保存的任务现已传递这些选择，旧任务仍默认 768P，旧非关键帧请求默认 9:16。产品项目画幅与单次生成覆盖值分开，不因一次横版生成改写整片设置。

该 API 没有给出 768P / 2K 对每种画幅的精确像素映射。下载结果核对可解码、有效尺寸、实际画幅、时长和音轨，不再用固定竖屏短边范围拒绝 2K；无法由输入确定的 adaptive 画幅如实标明未验证。本地 Fake 请求与真实文件校验不代表实际 2K 生成质量已验收。

## 项目中的职责映射

- `route.h3_capability_profile`：读取当前适配器支持的参数、专业风险及项目观察记录，参数支持与实际质量观察分别表达。
- `route.prepare_h3_group`：按当前组完成输入、编译、路线和可选入队；可选 resolution / aspect_ratio 不增加固定语言决策轮。
- `route.plan_h3`：根据 ShotPlan、资产和当前能力选择 DIRECT、FRAME_FIRST、FRAME_LAST、FRAME_FIRST_LAST 或 REFERENCE。
- `route.update_h3_asset_preparation`：在图像 Worker 建立并明确选用可用关键帧资产后，把 `GENERATING`、`READY` 或 `REJECTED` 状态精确回写到原 RoutePlan；后续 RoutePlan 编译只会消费当前明确选中且可用的图片版本。
- `route.compile_h3_prompt`：把已确定镜头编译成 H3 Prompt IR。
- `route.preview_h3_probe`：在提交前查看最终请求和成本敏感输入。
- `route.execute_h3_plan`：建立真实 MiniMax-H3 生成任务；`video.enqueue` 是确定性 Mock 路径，不会提交 H3。
- `task.advance`：逐步准备、提交、轮询并保存 H3 输出；`task.drain` 只批量推进 Mock 任务。
- `route.list_h3_reconciliation`、`route.resolve_h3_submission`、`route.resolve_h3_not_submitted` 处理 H3 提交结果不确定；`task.recover_output` 可恢复已完成但未落盘的 H3 输出。
- `route.review_h3_take`：把结果与所选路线的重点重新对照。

## 更新原则

当官方参数变化时，先更新 Provider Adapter、类型模型和 Tool Schema，再更新本文件与相应测试。只有稳定的 Prompt 结构和编译方法进入 `prompt_references`；文件大小、输入数量、分辨率、模型变体和任务保留期等易变事实不自动注入每次 Agent 调用。

## 官方来源

- https://www.minimax.io/blog/minimax-h3
- https://github.com/MiniMax-AI/MiniMax-H3
- https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- https://platform.minimax.io/docs/api-reference/video-generation-v2-query
- https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration
- https://github.com/AceDataCloud/MiniMaxAPI/blob/main/docs/videos.md
