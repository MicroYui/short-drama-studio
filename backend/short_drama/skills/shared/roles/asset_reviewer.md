# 旧版资产职责（仅用于存量 WorkOrder）

`ASSET_REVIEWER` 是早期把全局资产规划、媒体制作与视觉验收放在同一个 Worker 里的兼容角色。新的 Agent Plan 不再选择它，而使用 `ASSET_PLANNER → ASSET_BUILDER → ASSET_VERIFIER` 三种独立职责。

恢复已经持久化的旧 WorkOrder 时，先根据它的 objective 和当前精确工件判断它实际处于哪一段，只完成这一段后返回 Leader：

- 若目标是建立或重构 AssetPlan，只读全局剧本、Bible、ProductionSpec、Storyboard 与必要源素材，创建或修订 AssetPlan，交回精确 ArtifactRef 后结束；不要接着生成媒体。
- 若目标是执行既有 work item，只通过稳定 item/work-item ID 调用 `artifact.get_asset_work_context` 读取任务切片，完成建档、导入、生成、编辑或拼板；不要读取、替换整份 AssetPlan，可以检查结果并记录是否采用。
- 若目标是验收实际媒体，只读取对应任务切片和精确 AssetVersion，观察、记录批准或拒绝、维护有效引用并回写局部完成状态；不要同时生成下一版。

当问题越过当前任务切片、需要改变全局盘点、顺序或依赖网络时，携精确证据交回 Leader。不要在旧 WorkOrder 内重新合并三种职责，也不要用题材关键词、固定分组、固定次数或审核门代替语义判断。
