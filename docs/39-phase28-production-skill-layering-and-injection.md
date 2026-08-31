# Phase 28：Production Skill 分层与统一角色注入

## 结果

Phase 28 把此前逐次实验累积的 Production Skill Prompt 收敛为三个独立层次：

1. **Production constitution**：品类长期稳定的创作原则；
2. **role rubric**：只注入当前 Agent 职责所需的策略；
3. **retrieved experience**：仅在当前剧本上下文实际命中时追加的历史经验。

运行时公式为：

`ProductionProfile → constitution + selected role rubrics + retrieve(experiences, current context)`

它仍然是 Agent Harness 的上下文编译能力，不是固定 workflow、Tool allowlist 或审批门。`ProductionProfile` 是项目中唯一持久化的选择；具体 Prompt 在每次 Agent 调用前按当前工件重新编译，因此旧项目不需要迁移，用户修改剧本后也不会继续携带已经不相关的案例规则。

本阶段没有调用 GPT Image 2、MiniMax-H3 或其他付费媒体 Provider。

## 逐项修复

### 1. 移除核心 Prompt 中的单剧本过拟合

原抖音短剧策略曾直接累积“录音、文件、签名、融资、付款流程”等特定失败案例。它们可能帮助某一版样片，却会让完全无关的题材也受到同一套负面约束。

现在 constitution 和 Writer/Planner/Critic 等 role rubric 只保留跨题材稳定原则，例如：

- 快节奏来自可见动作、反应、选择、关系变化和剪辑推进，不来自生硬压缩对白；
- 长镜头可以成立，但镜内需要有与当前意图一致的内部进展；
- Critic 审核实际作品，不按答案键、固定镜头数或“每 N 秒新信息”判定；
- 建议始终可由用户修改或忽略。

案例经验移入独立 `ProductionExperience` Catalog。当前小型 Catalog 使用普通可检查的词项召回：强触发词命中即可召回；较宽泛的辅助词必须至少两个共同出现，避免只写到“办公室”或“关系”就套用旧的悬疑、融资案例。经验只对声明的 ProductionProfile 和 Agent 角色生效，并明确不能覆盖用户来源。

### 2. 按职责编译最小 Prompt

| Agent / Tool | 注入内容 |
| --- | --- |
| Story Architect | constitution + Concept + Writer + Planner + 相关经验 |
| Scene Writer / 局部剧本修改 | constitution + Writer + 相关经验 |
| Script Critic | constitution + Concept + Writer + Critic + 相关经验 |
| Review Router | constitution + Review Router |
| Story Repair / Revision | constitution + Writer/Critic 或 Writer/Planner + 相关经验 |
| Scene Director | constitution + Planner + Director |
| Director Advice | constitution + Director |
| Asset 生成与语义评审 | Asset Reviewer；不重复整段 constitution |
| H3 Route Agent | Route Planner；不在此重写故事或导演意图 |
| GPT→H3 Prompt Compiler | Video Prompt Compiler；只忠实翻译锁定镜头和媒体绑定 |
| Scene / Episode Reviewer | constitution + Episode Reviewer |

这修复了 Scene Director 只拿到 Planner 策略、Director rubric 没有真正进入场级分镜的问题，也避免每个 Agent 都读取一整份越来越长的品类 Prompt。

### 3. 清除下游写死的“短剧”策略旁路

资产 Prompt、资产语义评审、同源参考视图、H3 Route Agent、GPT→H3 Prompt Compiler、Scene Reviewer 和 Episode Reviewer 现在都读取项目当前的 `ProductionProfile`。`GENERAL_VIDEO` 会得到自己的角色策略，不再被抖音短剧的密度、冲突或节奏偏好污染。

H3 最终 Prompt Compiler 是翻译边界，不是第二个导演：路线已经由上游选定，它不能新增动作、切镜、冲突或压缩对白。此前列举门、进出场等单一案例的禁止项已改为通用的“不得新增未声明的大动作、复杂交互、出入画或场景转换”。

### 4. 保持观察工具中立

视频抽帧与时间段观察、Take 相对比较、Phase 27 的参考图/关键帧/输入兼容性观察继续先报告可见证据，不注入 Production constitution 或创作角色 rubric。品类判断发生在 Critic、Director、Route Planner 或 Episode Reviewer 中，而不是让观察 Tool 为抖音短剧或通用视频预设答案。

因此链路保持：

`中立观察 → 按当前 Skill 解释与推荐 → 用户可忽略 → 调用独立 Tool 执行`

### 5. 保持 AI-native 与兼容性

- `ProductionSkill` 不拥有任务状态、Provider 调用、版本依赖或时间线副作用；
- Tools 仍可被用户或 Agent 单独调用，PlanningRun/AutonomousRun 只是默认无人值守编排与恢复；
- `GENERAL_VIDEO` 与 `DOUYIN_SHORT_DRAMA` 共享同一项目、资产、H3、QA 和时间线实现，但各 Agent 使用不同 rubric；
- 观察工具、Schema、真实 ID、依赖计算和 H3 4–15 秒技术合同保持品类中立；
- 没有增加 hash 冻结、审批门、动态 Skill 服务或第二套状态源。

## 实现位置

- `backend/short_drama/production_profiles.py`：constitution、role rubric、经验 Catalog、上下文召回和 Prompt 编译；
- `backend/short_drama/production_planner.py`：Story Architect、Critic、Router、Revision、Scene Director；
- `backend/short_drama/hierarchical_script.py`：Scene Writer 与局部 Scene Patch；
- `backend/short_drama/reference_planner.py`：Director Advice；
- `backend/short_drama/asset_inspection.py`、`asset_catalog.py`：资产生成、派生与评审；
- `backend/short_drama/h3_route_planner.py`：H3 Route Agent；
- `backend/short_drama/providers/h3_language_prompt.py`、`h3_prompt_skill.py`：H3 Prompt 编译边界；
- `backend/short_drama/episode_review.py`：场级与整集 Review。

## 验证

定向测试覆盖：

- 空上下文不会加载案例规则；匹配题材才召回相应经验；宽泛单词不会误触发；
- Scene Director 同时收到 Planner 与 Director rubric；
- 资产、H3 Route、H3 Prompt Compiler 和 Episode Reviewer 收到各自角色策略；
- GENERAL_VIDEO 不包含抖音短剧策略；
- 视频观察 Tool 不包含 Production Skill Prompt；
- 旧项目仍只依赖已有 `production_profile` 字段，无持久化迁移。

验证结果：

- Production Skill、经验召回和分层剧本定向测试：40/40；
- 资产、Director、H3 Route、整集 Review 与观察中立性集成测试：51/51；
- Production Skill、GPT→H3 Prompt Compiler 与 H3 Route 补充测试：53/53；
- 后端全量回归：774/774；
- Python Pyright：0 error；
- TypeScript strict typecheck：通过；
- Vite 前端、Electron main 和 preload 生产构建：通过；
- `git diff --check`：通过。

由于当前 shell 的 Corepack 仍会绕过公司 npm registry 直连 npmjs.org，前端验证直接调用项目已安装的 `node_modules/.bin/tsc` 与 `node_modules/.bin/vite`，没有重装或修改依赖。构建仅提示本地 `caniuse-lite` 数据较旧，不影响结果。

## 下一步

原计划中的“带时间证据的 Repair Planner 与轻量 H3 Capability Playbook”顺延到 Phase 29。它可以消费 Phase 27 的视觉输入报告和现有 VideoInspection，但仍应采用同样边界：观察负责证据，Production Skill 负责解释，Repair Planner 给可覆盖方案，执行由现有 Tools 完成。
