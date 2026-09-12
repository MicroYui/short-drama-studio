# 当前场次写回与旧镜头读取的工作集修复

本次只在 `3c67c0b` 的独立工作树实现和离线验证。两个真实案例已经暂停，不修改其数据库、素材、原 Trace，不恢复旧案例验证，也没有调用真实 Provider。

## 两个实际缺口

新项目 `project-8d268085367b`，Job `studio-job-57ebf2b878a5`。

1. 编剧 `work-order-7e5ea45e81f34c02a1e1b8d2f23c9108` 的 t2 `script.save_scene` 于 09:50:21 保存《最后一版》场次 `scene-4da82135c813`，对应 `project-8d268085367b-script-v2@2`。正文 623 个 Unicode 字符、9 条结构化对白已真实保存，但 Tool 输出只有 ID、变更范围和通知。下一次 `model-bd40038d6d7b` 输入的写回交接进一步只保留标识符/receipt，全输入没有正文。t3 因“恢复后的工作集没有完整正文”自主调用一次 `artifact.read_current`。实际没有恢复服务或无限读取循环；这里的“恢复”是正常下一次 Worker 决策。回读内容与 t2 保存值一致。这次语言决策约 15.37 秒，不能据此宣称新实现已在真实运行节省这些时间。
2. 开场 `work-order-1c5c9702bb324b5b96aa7ca3a83d05bb` 的 t3 读取 S03 `shot-last-version-03@1`，当时引用没有具体图片。t12 `shot.save_current` 于 10:30:37 成功保存 revision 2 和实际身份/构图图片（`asset-version-a1e1bacbcc6b`、`asset-version-2690746552ca`）。后来的 `model-4a41b73a7921` 输入同时出现正确 `saved_shot@2` 和 t3 `read_observations` 的旧完整 rev1。旧 `artifact.read_current` 结果没有 ArtifactRef，原按 ArtifactRef/version 的失效判断漏掉它。尚未证明它已造成错误回写或额外回读；这是实际并列的冲突事实，不与独立 QA scope 的已修复覆盖问题混同。

对应输入文件：`20260912T015021712129-8fff4c9a-model.input.json`、`20260912T030108977853-e4d43488-model.input.json`。它们仍在原 Job Trace 目录。新夹具 `current_scene_write_context.json` 保留 Writer t2/t3；`current_shot_write_context.json` 保留开场 t1/t2/t3/t12。仅裁掉读取中的共享索引和 Bible，正文、保存参数、实际引用和回执不改。临时 Registry 回放只替换项目地址及服务生成的场次 ID，实际正文和 9 条对白逐值核对。

## 采用的方法与实现

本次重新读取 Anthropic 官方 [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 和 [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)，沿用本仓库既有做法：保留当前决策需要的实际对象内容，工具返回有用且明确的结果，大内容保留精确地址再按需回读。没有增加历史缓存系统、固定读取阶段或循环限制。

- `script.save_scene` 新增 typed `SceneWriteOutput`：`script_ref` 和 `saved_scenes[]`。每项含实际保存的 `StructuredScene`、该场次的 `ScriptLine[]`、`structure_stale`。从服务返回的保存后文档提取，仅含本次主场/关联场；未变化的已选场也返回当前值，不新建版本。省略 lines 时保留保存后的已有对白，明确结构是否待同步，不把提交意图冒充保存事实。
- 正常 Worker 输入把它折叠为按 Scene ID 的 `scene` 实体，携带原 script_ref。完整正文不放进重复的通用 receipt；立即写回和后续其他 Tool 后都只有一份。后续当前读取按场次更新，已有完整 READ 可见时不重复放实体。不同场次和显式快照分页分别保留。
- 成功局部写按实际 `changed_scene_ids`、`changed_shot_ids` 和 Shot refs 清除旧目标内容。当前读取没有 refs 的历史形态也按所选对象处理。一次读取同时包含 scene+shot 时，只移除失效部分，保留另一部分的真实来源。未知影响范围的整剧本写不能证明旧场次仍有效，则清掉旧当前场次正文；显式历史快照页仍是历史，不伪标新版本。
- 冲突/失败不替换原观察，也不改变底层并发保护。其他人未被本 Worker 观察到的更改不会凭空同步；`expected_text`/`expected_revision` 仍保护保存。实际读取当前值后，旧保存实体退出当前正文。
- 总请求继续走既有 `ModelInputCompiler`。大场次被外置为 `context_ref`/JSON pointer；重开服务后用真实 `agent.read_context` 分页拼回，逐字相等。原 Tool turn 和快照不改。没有从旧写请求补造 `saved_scenes`。

同批按另一修复分工，在 `_compact_image_task_state` 的现有字段表加入 `provider_output_version_id` / `provider_output_version_number`，使已登记 raw 候选与默认合成候选分开可见。本切片不改变图片生成、登记或默认选择行为。

## 输入量与验证边界

固定上述两个真实模型输入的其他字段，只替换本次相应工作集投影，以随包 o200k 估计完整文本（含原 instructions）：

| 输入 | 原估计 | 修复投影估计 | 变化 |
| --- | ---: | ---: | ---: |
| Writer t2 后：加入实际已保存场次/9 条对白 | 26,766 | 28,884 | +2,118 |
| 开场 t31 后：移除 S03 的旧 rev1 读取 | 83,927 | 83,098 | −829 |

这是单字段投影对比，不是新版本整轮运行，不包含工具说明变动的差异，也不是 Provider usage。Writer 增量是必要正文恢复，不能只以更少 tokens 判断正确。是否减少真实写后回读和总制作时间，留给修复整合后的新空项目验收。

新测试覆盖实际 Registry 场次保存→Store 重开→真实 `LanguageSpecialistAgent.execute` 输入、关联场次/省略对白、并发失败与随后当前读取、后续实际/旧回执更新、无变化保存、大正文分页和原记录不变；Shot 测试回放真实 t3/t12，验证 S03 旧读取退出而 S01/S02 保留，也覆盖真实混合 scene+shot 读取的局部失效、失败写保护。

验证：8 个相关文件共 **78 passed / 26.44s**（新两文件、场次语义修改、Shot 写回、资产 Worker Shot 上下文、独立 QA scope、Harness Tool 上下文、总请求预算）。随后仅补入生产同一持久化适配器的等值断言并重跑两个新文件，**17 passed / 8.64s**；两源和两测试文件 Pyright **0 errors / 0 warnings**，`git diff --check` 通过。没有改变公共 HTTP 保存请求、共享 Skills、QA scope、预算或运行调度；Tool 内部新增 typed 输出不承诺旧 Trace 已经具备该字段。
