# Phase 17：独立 Revision Judge 与版本建议

## 目标与边界

Phase 16 已证明 Script Critic 的报告可能非单调变化：修订后总问题数可能回升，或总数下降但 HIGH 问题增加。因此 Phase 17 在这类风险出现时插入一个独立 `Revision Judge`，比较最近两版完整大纲及各自的审稿证据。

本阶段遵守以下边界：

- 只在最新报告相对上一轮出现“总问题数增加或 HIGH 增加”时调用；正常改善不增加模型调用；
- Judge 不按轮次和问题数量机械打分，而是阅读两版实际内容；
- 输出只是一条可解释、可忽略的建议，不自动替用户换稿；
- 后续 Scene Director 仍使用当前最新版 `outline_json`，避免隐式改变已经形成的规划状态；
- Judge 不可用、输出无效或快照缺失时直接记录提醒并继续；
- 本阶段只调用 GPT-5.6-sol，不调用 GPT Image、MiniMax-H3，也不建立 Project 或媒体任务。

## 工作流

长篇规划状态机增加一个条件节点：

```text
SCRIPT_CRITIC
  ├─ 仍可修订 → STORY_REVISION
  ├─ 无回退风险 → SCENE_DIRECTOR
  └─ 最新报告回退 → REVISION_JUDGE → SCENE_DIRECTOR
```

`REVISION_JUDGE` 从已有 PlanningRun artifacts 读取：

1. 最近两份 `PlanningReviewSnapshot`；
2. 与两份审稿各自 `revision_round` 精确对应的 `PlanningOutlineSnapshot`；
3. 原始输入、制作类型和用户自由填写的目标时长。

Judge 比较因果闭合、开场钩子与结尾机制、技术或制度规则、观众可见证据、人物动机、场次效用、信息密度，以及修订是否引入新矛盾。问题数量和严重度只作为触发与参考证据，不作为版本得分。

## 持久化与前端

`PlanningRunRecord.revision_judge` 持久化：

- 推荐版本与备选版本；
- `LOW / MEDIUM / HIGH` 置信度；
- 简短结论；
- 可核对的判断依据；
- 采用该版本的取舍；
- 固定为 `advisory_only=true`。

剧本质量历史 API 同时返回该建议。桌面剧本页在跨轮轨迹下展示推荐、依据和取舍，并明确提示“建议可忽略，当前流程没有自动替换大纲”。PlanningRun 进度页也显示独立 Judge 节点。

## 工程验证

集成测试覆盖：

- `4 → 2 → 3` 的非单调报告会进入一次 Judge；
- Judge 推荐第 1 轮时，系统仍保留第 2 轮大纲继续分镜；
- Judge 结果跨 Service 重建保留；
- 单调改善时 Judge 调用次数为 0；
- Judge Provider 失败时 PlanningRun 仍能完成；
- 整个剧本规划流程仍不创建图像任务或 H3 Provider run。

阶段出口为 713 项后端测试、Pyright、TypeScript 和 Vite/Electron 生产构建通过。

## 真实 GPT-5.6-sol 验证

直接复用 Phase 16 已保存的大纲和审稿快照，没有重新生成剧本：

| 样本 | 最近两轮报告 | Judge 建议 | 置信度 |
|---|---|---|---|
| 《午夜董事会》 | `7/1 → 5/2` | 第 2 轮修订稿 | HIGH |
| 《死亡证明寄到离婚前夜》策略复跑 | `5/1 → 7/1` | 第 2 轮修订稿 | HIGH |

两次建议都不是“最新版默认获胜”。《午夜董事会》的结论认为新版更清楚地区分原始音轨、合成播报和纪要模板，并补足权限边界，但同时指出开场台词与最终真相、交易实体命名和离线诱捕状态仍有矛盾。《死亡证明》样本则认为新版补全了任务派发、权限边界和现场证据链，同时指出反派冒险亲办的即时后果、离婚诱骗功能和高潮信息密度仍未完全解决。

这两次调用只验证 Judge 能使用真实版本证据给出有取舍的建议，不证明推荐结果已经达到人类剧本评审基准。验证后原数据库仍为 0 个 Project、0 个 image task、0 个 generation task、0 个 provider run 和 0 个 H3 route plan。

## 后续视频路线实验

当前 AceData H3 合同和现有 Adapter 已支持 `video_url / reference_video`，但“有输入入口”不等于它一定能稳定完成镜头续接。后续应单独比较：

1. 当前首帧或首尾帧基线；
2. 前一条已选 Take 作为 reference video；
3. 只截取前一镜尾部运动窗口作为连续性参考；
4. 先做低保真人物走位、机位和运镜预演，再把预演视频作为运动参考。

重点观察人物身份与场景几何、动作和视线连续性、摄影机路径、错误继承、画质损失及返工范围。它应进入 Director/Route Agent 的可覆盖路线建议，而不是成为所有镜头的固定前置步骤。实验前不新增“门、区域穿越”等场景专用字段；现有 `H3MediaInput`、镜头依赖、视频观察工具和 `RouteEvidence` 足以承载首轮验证。

这项工作不会从零盲试。当前 LTX Desktop 上游源码已经提供“输入媒体 → 控制视频”的预处理边界、逐帧 depth/pose 处理接口、conditioning cache 和 video conditioning 调用链；后续会先借鉴这些工程分层，再为 H3 制作简单的走位/机位预演输入。只学习控制视频的生产与管理方法，不接入 LTX 权重，也不假设 H3 `reference_video` 与 LTX IC-LoRA 具有相同控制强度。
