# Phase 18：H3 连续性视频参考与机位预演

## 目标与边界

本阶段验证 H3 `reference_video` 能否帮助同场景相邻镜头承接人物、场景、动作和摄影机关系，并比较三种由 Route Agent 可选的参考视频：

1. 前镜完整已选 Take；
2. 前镜最后 4 秒无声运动窗口；
3. 4 秒低保真二维机位预演。

这些都是候选路线，不是生成前置门槛。没有前镜 Take、没有批准关键帧、Agent 不可用或用户明确忽略建议时，Direct、Frame 和其他合法 H3 路线仍可继续。实现没有增加门、区域穿越或反打等场景专用领域字段，也没有文档 SHA、素材 hash 冻结或额外审批链。

## 从 LTX Desktop 复用的工程分层

本阶段没有接入 LTX 权重，也没有假设 H3 与 LTX IC-LoRA 的控制语义相同；只复用了上游已经成熟的“控制媒体是一等工件”分层：

- `backend/ic_lora_preprocessing.py` 将图片或视频归一化为带 FPS、帧数和尺寸的控制视频，并把预处理与模型调用分开；
- `backend/handlers/ic_lora_handler.py` 负责输入校验、帧网格、FPS 重采样、canny/depth 处理、缓存和最终 `video_conditioning` 绑定；
- `backend/state/conditioning_cache.py` 证明预处理产物可以独立缓存和清理，而不是每次 Provider 调用都临时重做；
- `backend/services/depth_processor_pipeline/`、`backend/services/pose_processor_pipeline/` 与 `backend/services/video_processor/` 把 depth、pose 和逐帧媒体变换放在可替换 Service 边界。

当前 H3 不提供等价的 depth/pose 控制合同，因此首版只实现不会假装具备模型级控制强度的媒体工具：无声裁切、尾窗、批准起始关键帧归一化和二维 `zoompan` 机位预演。以后 H3 合同若增加更强控制输入，可复用同一工件和 Service 边界，不必修改 ShotPlan。

## 已实现工作流

```text
已选前镜 Take + 当前镜头计划 + 可选批准首帧
  → H3VideoGuideBuilder 生成三类无声 768×1344/24fps 工件
  → Route Agent 比较 Direct / Frame / Reference / Video Guide 候选
  → H3 768P 生成
  → 镜内抽帧 + 相邻镜头边界检查
  → Director Agent 建议重生成、换关键帧、拆镜或换机位
  → 用户可忽略，AUTO_DRAFT 可继续
```

主要实现：

- `H3VideoGuide` 保存来源 Take、来源镜头版本、媒体路径、时长、机位预设、预演基底、来源 QA 和普通风险说明；
- `H3VideoGuideBuilder` 生成 `PREVIOUS_TAKE`、`TAIL_WINDOW` 和 `CAMERA_PREVIS`，全部去除源音轨，避免复制前镜对白；
- `CAMERA_PREVIS` 优先从当前镜头已批准 `FIRST_FRAME` 起步；没有该资产时才退回前镜边界帧，并把基底来源明确写入工件；
- Route Agent 能看到镜头意图、候选、来源 QA、`previs_base_source` 和风险，自行选择或不选；确定性 fallback 不会把视频参考强制为默认；
- `AUTO_DRAFT` 在同一场内按连续性 wave 推进，使后镜规划时能看到前镜已选 Take；不同场仍可并行推进；
- 视频观察器继续用通用镜内时间序列和镜间边界证据分类问题，不针对本样片写答案；
- `same-scene-boundary-video-guide-v1` 进入回归案例，后续调整 Route Agent、关键帧或 H3 Adapter 时可复测。

正常确定性校验仅覆盖媒体存在、H3 合法输入组合、任务状态、版本引用和时间线可执行性。镜头是否适合视频参考、是否拆镜、是否换机位等创作判断仍由 Agent 建议和用户覆盖。

## 真实 H3 768P 对照

样本为《明天别来了》同一走廊场景的相邻镜头：前镜是 15 秒正面跟拍，目标镜头是 7 秒反打停步镜头。基线和所有变体使用同一目标镜头意图；本轮只改变参考视频。

| 路线 | Task | 观察结果 |
| --- | --- | --- |
| 已有 Frame 基线 | `task-0b5426a43ca04428bd34fb57b58fb02e` | 反打与空间关系成立；切点工牌从右手跳到左手，结尾未完成先迈步动作 |
| 前镜完整 Take | `task-a08679de3f1449d5b90922ee66665206` | H3 重启前镜行走，同一走廊视角继续，未建立目标反打 |
| 前镜 4 秒尾窗 | `task-de53a10048e247e39528480dbc71bc2a` | 仍重启行走并重复前镜覆盖，边界终态没有被当作单纯状态锚点 |
| 旧预演：前镜尾帧基底 | `task-307b3b9759194e94b326fb2022d5c33b` | 得到后背视角，但人物距离和站位被重置，重复接近过程 |
| 新预演：当前镜头批准首帧基底 | `task-54e57a12c77f4d92b67981c80497f1a6` | 反打、人物身份、走廊、服装和工牌持握连续；没有重启行走或重复前镜；结尾仍未完成计划中的先迈步与跟随 |

首轮三变体清单保存在：

`~/Library/Application Support/ShortDramaStudio/outputs/short-drama-projects/project-d0c0bbe83404/experiments/phase18-video-guides/20260824T050554Z/manifest.json`

修正后预演清单保存在：

`~/Library/Application Support/ShortDramaStudio/outputs/short-drama-projects/project-d0c0bbe83404/experiments/phase18-video-guides/20260824T051502Z/manifest.json`

可复现入口为 `backend/scripts/run_phase18_h3_video_guides.py`。脚本默认只做本地 dry run；显式传入 `--submit` 才提交付费 H3 任务，并能用 `--resume-task KIND=TASK_ID` 恢复已知任务。

## 当前能力画像

这一个样本足以否定“前一镜视频天然优于关键帧”的假设，但不足以给所有镜头类型计算稳定率。当前 Route Agent 使用以下经验边界：

- 原始前镜视频会被 H3 当作要继续生成的内容，Prompt 中写“不要重复”也不可靠；
- `PREVIOUS_TAKE` 和 `TAIL_WINDOW` 只在目标确实延续同一动作、相近覆盖和摄影机动势时值得候选；
- 反打、换机位或新动作优先比较 Frame 路线；若要测试机位动势，`TARGET_START_FRAME` 基底的预演比前镜尾帧基底更合理；
- 二维预演可以帮助保住目标构图并传达粗摄影机路径，但不能证明人体走位可行，也不能保证 7 秒内完成终态动作；
- 若视频 QA 发现重复动作、空间连续性、接触连续性、人物朝向或构图问题，Director Agent 比较重生成、换关键帧、拆镜和换覆盖机位，再更新镜头依赖；旧 Take 不进入正式时间线。

这些都是可解释、可忽略的推荐。缺少证据时保持 Direct/Frame 候选，不用固定规则代替导演判断。

## 工程验证与未决项

离线测试覆盖：

- 三类工件均为 768×1344、无音轨且时长合法；
- 当前镜头批准首帧存在时，预演优先使用该版本并生成不同工件 ID/路径；
- 没有首帧时退回前镜边界帧；
- Route Agent 可选择尾窗，也可选择 Direct/Frame，`override_allowed` 始终为真；
- 前镜 revision 改变会使已选 guide 的路线计划失效；
- `AUTO_DRAFT` 后续 wave 能看到前镜 guide 候选，仍沿用原恢复与返工机制。

仍需在后续普通质量积累中验证，而不是马上扩张系统：

- 真正“同动作、同覆盖”的续接镜头中，完整 Take 与尾窗是否稳定优于 Frame；
- 长镜头 Extend/续段能否从视频参考获得净收益；
- 多人遮挡、快速动作和手物接触下，预演是否仍能保住构图；
- 跨剧本足够样本后的默认推荐稳定率。

因此 Phase 18 的结论是“视频参考已成为可用 Agent Tool，并形成了有证据的选择策略”，不是“视频参考成为所有连续镜头的默认必经步骤”。
