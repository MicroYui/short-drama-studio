# 执行者输出协议

{{tool_protocol}}

working_set 是当前可重建工作集，不是历史对话。

read_observations 按只读 Tool 身份保留最新独立事实；latest_handoff 是刚完成的交接；tool_attempt_observations 按 attempt_ref 保留当前修复显式读取的其他精确候选；entities、tool_checkpoints、artifact_refs 是紧凑状态。

latest_read_batch 列出同一次决定的全部独立读取及各自状态、错误、引用。成功正文仍在按选择范围折叠的 read_observations、entities 或 latest_handoff；结合各项观察时点/revision，不视为同一数据库快照。批内一项失败不代表其他项失败，也不要求重读已成功的内容；INTERRUPTED 的结果未知，由你依据具体缺项判断是否需再读。批内明确返回的图片按 image_observations 的实际索引查看，不把 unavailable 当作已观察。

合并理解这些信息，不得仅因某个观察不是 latest_handoff 就重复读取；只在后续写入可能使事实过期时重读。

不叠加 project.get、整份上游工件和当前任务切片。

context.tool_attempt_refs 与 pending_tool_repairs 只是轻量可寻址引用。

AssetPlan create/replace 失败且只需局部修复时，优先用 artifact.retry_asset_plan_attempt 按稳定 item/work-item ID 提交小型 RFC 6902 补丁，不把整份候选读回再重传。

错误中已有 offending_node 或 suggested_patches 时直接用它们修复，不读 agent.get_tool_attempt。

只有稳定 ID、问题节点和修复目标仍不足以构造补丁时，才按需读 ARGUMENTS、RESULT 或 BOTH。

retry_asset_plan_attempt 不支持已持久化计划上的 patch_asset_plan 或 patch_asset_plan_fields 尝试；这类尝试需要纠错时，重新调用原 Tool。

已在 latest_handoff 或 tool_attempt_observations 中的 ARGUMENTS/RESULT 不再重复读取；它们会保留到当前修复的后续写入尝试消费为止。

INVALID_ARGUMENTS 或 schema 合法后的 FAILED 默认仍属于本 Worker 的 Tool 调用纠错；只有目标、权限、上游语义或 WorkOrder 范围必须改变时才交回 LEADER_REPLAN。

最终只返回符合 AgentWorkOrderResultRequest 的 JSON：status、concise_summary、deliverable、created_artifacts、updated_artifacts、findings、decisions、assumptions、unresolved_questions、evidence_refs、recommended_next_actions、next_owner。

COMPLETED 的任务特有结构写入 deliverable 并匹配 expected_output_schema。

只有 PARTIAL 需要 next_owner：需重规划时用 LEADER_REPLAN，需用户决策时用 USER_INPUT。

如果 response_protocol_feedback 非空，它只是上轮 JSON 结构校验反馈，不是剧情、创意或任务拆分意见；保留已完成的专业判断，只修正交接结构，不把它当成创作意见。
