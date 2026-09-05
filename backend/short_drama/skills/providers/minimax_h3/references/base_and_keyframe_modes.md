# H3 基础与关键帧模式

## DIRECT / T2VA

使用三个字段，并保持此顺序：

1. `integrated_multimodal_description:`
2. `overall_soundscape:`
3. `non_diegetic_music:`

没有图像锚点，因此第一字段必须自己建立首帧：画幅、构图、主体身份与外观、人物相对位置、环境、照明、道具状态和动作起点。之后再描述连续动作、摄影机、声音同步和结束状态。

DIRECT 适合探索性或连续性风险较低的镜头，不意味着可以模糊描述。若角色身份、场景结构或特定道具必须与前镜严格一致，上游应先考虑关键帧或参考路线。

## FRAME_FIRST / I2VA

Prompt 以官方首帧对齐句开头：

`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`

之后仍使用 T2VA 的三个字段。首帧中的身份、服装、场景、光线、主体位置和构图是已批准起点，不得重新描述成不同画面。正文重点说明“从该画面开始发生什么”，包括最初微动作、主要动作、摄影机发展、声音和结束状态。

不要把 `<Picture 1>` 当成额外插入镜头，也不要重复发明图中不可见的人物。首帧本身有错误时，应修复资产，不通过文字要求模型忽略它。

## FRAME_LAST / L2VA

Prompt 以官方尾帧对齐句开头，并将时间写成任务的精确时长：

`How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the {duration}.00-second mark of the target video.`

`<Picture 1>` 只代表最终一帧。正文从已锁定的 ShotPlan 推导与尾帧相容的开场状态，说明人物、物体、摄影机、光线和声音如何逐步到达尾帧；不得把尾帧描述成 0 秒画面，也不得反向播放动作。

L2VA 适合终态结果比开场更关键的镜头，例如姿态落点、道具最终状态、揭示结果或精确落幅。若尾帧无法由镜头声明的动作自然到达，应修复镜头或资产，而不是用 Prompt 掩盖矛盾。

## FRAME_FIRST_LAST / FL2VA

Prompt 以首尾帧对齐句开头，并将末尾时间写成任务的精确时长：

`How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the {duration}.00-second mark of the target video.`

之后使用三个基础字段。正文必须说明从 `<Picture 1>` 到 `<Picture 2>` 的连续路径，而不是只重复两张图的内容。至少覆盖：

- 哪些人物或物体移动，哪些保持稳定；
- 姿态、表情、持物和空间距离怎样逐步变化；
- 摄影机如何从开场构图到达结尾构图；
- 哪个动作节拍促成最终状态；
- 声音如何与中间过程同步；
- 结束时怎样精确收敛到尾帧的姿态、状态、光线和构图。

若两张关键帧在身份、服装、空间拓扑、持物或光线方向上冲突，Prompt 无法可靠修复，必须返回输入兼容性检查或重新制作关键帧。

## 多镜与时间标记

基础模式允许描述多镜，但当前项目仍以 `ShotPlan` 为权威：

- 单镜计划只写 `[Shot 1]`，保持连续摄影机和动作。
- 已锁定多镜时，后续镜头用 `[Shot N] At HH:MM:SS.mmm` 或当前验证器接受的精确格式。
- 时间严格递增且不超出总时长。
- 每个切点前后的主体位置、视线、持物、声音和动作承接都必须明确。
- 不以切镜掩盖无法生成的复杂动作；若镜头方案本身不可制作，返回导演层拆分。

## 当前边界

项目当前支持 MiniMax H3 官方 Skill 所列的五类输入模式：DIRECT/T2VA、FRAME_FIRST/I2VA、FRAME_LAST/L2VA、FRAME_FIRST_LAST/FL2VA 与 REFERENCE/Ref2VA。路线必须由 Route Agent 或用户从已注册候选中显式选择；Prompt Compiler 不自行改变输入模式。
