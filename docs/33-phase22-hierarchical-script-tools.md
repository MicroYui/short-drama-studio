# Phase 22：任意时长分层剧本 Tools 与局部 Patch

## 结论

剧本时长不再用“短片”和“长篇”切成两种生成方法。约 4 秒的单节点内容、3 分钟短剧与数十分钟剧本都使用：

`Global Blueprint → Scene Writer → Script Critic/Repair → Scene Director`

时长只影响场次节点数、单场时长和需要反复取回的上下文量，不引入另一套长篇 workflow。具体节点数仍由 Story Architect 按内容决定，不固定成几个时长档或场次配额。界面允许用户填写不少于 4 秒的任意目标时长，默认全部进入同一个可恢复 PlanningRun。

PlanningRun 只是默认无人值守编排器和断点恢复载体。底层是可单独调用的 Agent Tools，用户或上层 Agent 可以跳过默认顺序，直接检索、改写或重新审阅某个场次。

Tools 本身不固定创作风格。`production_profile` 选中的 Production Skill 才会向 Writer、Critic、Director 注入对应策略：高信息密度、短促口语和稍快语速只属于 `DOUYIN_SHORT_DRAMA`；`GENERAL_VIDEO` 保留氛围、留白和长镜头的合法性。未来数字人口播使用独立 Skill，用户自定义品类则使用其选定或自定义策略，不继承抖音短剧偏好。

Phase 23 已将这里的概念落实为显式 `ProductionSkill` 与静态 Catalog，并加入可覆盖的建议式 playbook；PlanningRun 不把它解释为强制状态迁移。

## 成熟实现中迁移的方法

- [LongWriter](https://github.com/THUDM/LongWriter) 的 AgentWrite 先把长文任务分成局部写作任务，再分块产生内容。本项目迁移为“全局蓝图 + 逐场写作”。
- [DOC Story Generation](https://github.com/facebookresearch/doc-storygen-v2) 使用递归大纲节点，生成当前节点时带入祖先、相邻和附近未来节点。本项目迁移为 episode manifest、当前场节点、近场全文和后续节点。
- [StoryDaemon](https://github.com/EdwardAThomson/StoryDaemon) 保留最近场次全文，将较早内容压缩为摘要，并只修订尚未生成的未来计划。本项目当前每场写作使用最近 2 场全文、最多 8 场较早摘要和下一段场节点，不累积整部剧本全文。
- [Re3](https://github.com/yangkevin2/emnlp22-re3-story-generation) 把全局计划、当前故事状态、局部生成和一致性修订分开。本项目继续使用 Blueprint、Continuity Summary、Scene Patch 和 Revision Verifier 的责任边界。
- [Pulpgen](https://github.com/muckelverk/pulpgen) 用结构化 XML patch 修改章节或段落。本项目只借鉴“模型提出 patch，确定性代码合并”，不照搬其每次携带整本上下文的方式。

## 可组合 Tool 边界

### 1. Blueprint Tool

`create_story_architecture` 只负责全局故事承诺、人物、地点、道具、风格、场次节点、可执行动作与连续性输入/输出。

- `AUTO_ORIGINAL / IDEA_EXPANSION`：Blueprint 不再一次生成全剧对白，`lines=[]`。
- `SCRIPT_LOCKED`：用户原台词仍逐字保留并按场分配，Scene Writer 仅做无副作用的场次抽取。

### 2. Scene Writer Tool

`write_story_scene` 一次只把一个场次节点展开为 `EpisodeScenePatch`，不能修改 Blueprint 所有的全局资产、场序和时长分配。它可被 PlanningRun 顺序调用，也可被其他 Agent 单独调用。

`assemble_written_scenes` 由确定性代码重建全局台词索引，模型不负责手写跨场编号。

### 3. Script Document Search Tool

每个完整剧本版本投影为：

- 1 份 Story Bible 文档；
- N 份 Scene 文档；
- `run_id + revision_round + document_id` 的版本隔离。

SQLite FTS5 优先使用 trigram 支持中文子串检索，不可用时回退 `unicode61`。这是普通文档检索层，不增加向量库、图数据库或第二份剧本真相源。

API：`POST /api/short-drama/planning-runs/{run_id}/agent-tools/search-script`。

### 4. Scope Recommendation Tool

`recommend_script_revision_context` 根据用户指令、episode manifest 和检索候选，建议 `SCENE_PATCH / FULL_OUTLINE`。建议必须 `user_overridable=true`，不构成门禁。用户明确指定场次时，用户范围直接覆盖 Agent 建议。

### 5. Scene Revision and Patch Merge Tools

`revise_story_scene` 只输出一场的 `EpisodeScenePatch`；`apply_episode_outline_patch` 只替换被选中场次，其他场次台词与契约保持原值，然后由代码重建台词索引。

API：`POST /api/short-drama/planning-runs/{run_id}/agent-tools/revise-scenes`。

Review Router 触发的默认修订也已优先使用 Repair Plan 中的 `affected_scene_sequences` 执行局部 Patch。只有 Scope Agent 认为标题、中心设定、全局资产或大部分因果主干必须改变时，才使用完整 Blueprint 修订兼容路径。

## PlanningRun 的默认编排

PlanningRun 增加 `SCENE_WRITER`，并持久化：

- `scene_script_json`：已完成的场次 Patch；
- `next_script_scene_sequence`：下一个恢复点。

默认自主运行会顺序调用各 Tool，每完成一场即保存。应用重启只重试当前场，不重写已完成场次。用户在 Blueprint 阶段修改内容时，只清空尚未完成的场次写作结果并从 Scene Writer 恢复；完整剧本后的局部修改仅失效审稿和导演层下游结果。

## 验证

- 4 秒、3 分钟和 60 分钟 Blueprint 都输出场次节点，并由同一 Scene Writer/Assembler Tool 生成剧本；
- 单场 Patch 不改变其他场次台词；
- 剧本文档检索按 run 和 revision 隔离；
- PlanningRun 可在 Scene Writer 中途重启后继续；
- Critic 修订优先仅修改 Repair Plan 指定场次；
- Pyright 和 TypeScript strict typecheck 通过。

## 当前边界

- Script Critic 的整集视角仍是一次调用；真实数十分钟长稿尚需验证是否应再拆为 Scene Critic 摘要与 Episode Critic 汇总。
- 改变全局角色、地点、标题、总时长或大量场序时，仍需要 Blueprint 级修订；这是改动本身的实际依赖，不是所有修改的固定 workflow。
- 本阶段只验证语言规划、检索、局部合并和恢复，不调用 GPT Image 2 或 MiniMax-H3。
