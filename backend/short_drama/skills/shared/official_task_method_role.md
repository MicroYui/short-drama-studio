# MiniMax 上游工作流与任务方法的角色适配

这里同时服务两层来源方法：MiniMax Design 3.0.11 的 `drama-series` / `ad-tvc` / `mv` 是完整项目工作流；MiniMax-H3 公开目录的 8 个 Skill 是任务或表达方法。它们都以版本化原文和薄适配器进入本系统，不代表复刻 MiniMax Design 的私有运行时。Agent 只读取与当前职责相关的精确章节，不把整份上游 Markdown 重复塞进每个 Prompt。

选择方法时，先判断是否需要一个 Design 项目工作流作为主骨架，再按任务需要组合 H3 方法。这是 Agent 对完整目标、ProjectDocument 和已有工件的语义判断，不是题材枚举、trigger words、关键词路由或固定选择数量。组合后的结果写入同一份 ProjectDocument 及其版本化工件；本地适配层只完成 ProductionDocument、WorkOrder、ShotPlan 和 Tool Schema 对齐，不重造第二套内容真相。

交互按以下优先级解释上游步骤：用户已明确给出的值直接采用，不重复询问；用户明确要求在某个工件或阶段确认时，只在该语义停靠点进入 `USER_INPUT`，即使其余流程无人值守；除此之外，用户明确要求不交互或交由 Agent 决定时，Agent 补齐其余选择、记录决定与假设后继续；其余情况只在真正缺少会实质改变作品且无法自主获得的信息时询问。这是语义决策，不通过关键词或次数门禁实现。

上游的产品操作只作为方法来源；当前通过实际 Tool 更新同一 ProjectDocument，不伪造目录中不存在的调用。`drama-series` 的系列钩子、付费点等默认必须服从当前 `EndingContract`；单篇完结不因选中该 Workflow 而被改成强制留钩。

Design 的 `clip_group` 表示“多个分镜行合成一个生成视频片段”，而本地 `ShotPlan` 是剪辑/叙事镜头，`timeline_beats` 只是同一连续镜头内的时间阶段。适配时保留 ShotPlan 边界，另在 ProductionDocument 中记录 `clip_group_id -> ordered shot_ids` 的生成组合，不把 `clip_group` 展平为 beat，也不把 beat 伪装成切镜。

每集维护一份绑定 `storyboard.current` 的完整分镜；`scene_nodes` 是场次索引与连续性聚合。按场次或生成组逐步写入用于恢复和实时呈现，不拆出彼此独立的内容真相。

分镜写作在本地 Harness 中可以通过 `storyboard.draft` 逐个 ClipGroup 持久化，以获得可恢复进度并避免一次模型响应承载整集；完成后确定性装配为上述单一正式文档。这是执行层适配，不改变上游最终交付形态。

角色卡和场景卡的方法不替换已有资产 Tools。Agent 可以一次生成一张标准参考卡，也可以先生成相互一致的单视图，再使用 `image.compose_reference_sheet` 确定性拼成标准多视图或场景 coverage；两条路线由 Asset Worker 依据资产重要度、已有证据和连续性风险决定。
