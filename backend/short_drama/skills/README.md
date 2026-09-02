# 视频制片 Skills 目录

这里保存的是 Agent 可读取、产品和创作人员也可以直接审阅的制片方法。它不保存项目状态，不直接调用 Provider，也不定义另一套 Tool。

## 从哪里看完整流程

- [`workflows/end_to_end.md`](workflows/end_to_end.md)：所有视频类型共享的“目标与输入 → 内容/剧本 → 资产 → 场次与分镜 → 候选生成 → 检查与局部修复 → 时间线 → 验收与导出”方法。
- `formats/<format>/SKILL.md`：该视频类型长期稳定的创作原则。
- `formats/<format>/playbooks/default.md`：该类型叠加在共享流程上的详细制作方法；每步说明输入、判断、Tool、产物和返工，但不是第二套固定工作流。
- `formats/<format>/roles/*.md`：编剧、导演、审校、资产审核等专业 Agent 的角色 Prompt。
- `formats/<format>/references/*.md`：只在相关专业角色需要时加载的深入方法，避免把全部细节重复塞给每个 Agent。AI 短剧的创作维度位于 `formats/ai_short_drama/references/ai_native_forms.md`，观众行为与节奏诊断位于 `audience_behavior_and_rhythm.md`。
- `providers/<provider>/SKILL.md`：把 provider-neutral 镜头事实翻译成特定模型语法的 Provider Skill。
- `providers/<provider>/references/*.md`：模型 Prompt 结构、引用方式和维护边界；易变 API 参数仍以 Tool Schema/Capability 为准。

## 运行时如何使用

1. 项目创建时把所选视频类型保存为 `ProductionProfile`。
2. Production Manager 根据当前项目、用户目标、已完成工件和记忆编译 `ContextPack`。
3. Planner Prompt 由共享流程、所选格式 Skill、完整类型 Playbook、所需角色 Prompt 和当前 Tool Catalog 共同组成。
4. Agent 生成可替换的 `PlanRevision`，自行跳过、合并、重排或并行步骤，并选择要调用的 Tool 或要委派的专业 Agent。
5. Harness 只校验依赖、参数和状态，持久化进度并在获得新结果后继续规划；它不把上述制作方法解释为固定节点图。

角色文件可以在 frontmatter 中声明 `references: [...]`。Loader 只把该角色真正需要的方法附加到它的 Prompt。例如 AI 短剧编剧读取叙事与文本方法，资产审核读取 Style 与连续性方法，避免所有角色都携带全部资料。

H3 Prompt Compiler 还会读取 `providers/minimax_h3/SKILL.md` 及其 `prompt_references`。这层只翻译字段结构、时间线、对白和媒体标签，不负责选择剧情、镜头或提交 Provider 请求。

桌面安装包通过 `electron-builder.yml` 的 `short_drama/skills/**/*.md` 规则携带这些运行时文档。以后若 Skill 引入非 Markdown 资产，需要同时更新打包过滤与回归测试，不能只验证开发目录。

因此，AI 短剧、数字人口播、动漫和通用视频共用同一套项目、剧本、资产、生成、检查和时间线 Tools。它们的区别来自格式 Skill、角色 Prompt、用户目标与当前工件，而不是复制四套底层能力。具体视觉风格写入项目 Bible，不由 ProductionProfile 硬编码。

## 哪些顺序仍写在代码里

这里的 Markdown 描述创作方法，不取代两类为了正确恢复而保留的内部状态机：

- 长文本首次规划由 Agent-owned `PlanningRun` 按持久化游标推进大纲、分场写作、审校/修订、逐场导演和最终写入。Production Manager 通过 `planning.advance` 每次推进一个单元，完成后再回到动态计划。
- 图像、视频、语音等 Provider Task 仍按提交、轮询、下载、成功/失败等真实外部状态推进。

这两类状态机只保证作业顺序和恢复，不为不同视频类型各写一套端到端 workflow。项目进入正常制作后，下一步仍由 Agent 的 `PlanRevision` 决定。

## 关于经验

格式 Skill 只保存跨项目稳定成立的指导，不存放某个合同、融资、办公室等具体样片经验。`references/` 保存的也是可复用方法，不是某个项目的样片记录。项目执行中形成的经验由 AgentRuntime 的 `ExperienceMemory` 按项目、Skill 和证据保存，并在相关任务中按需召回。
