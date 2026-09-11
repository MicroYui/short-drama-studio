# 当前分镜留存与精确引用包装

2026-09-12。范围是同一视频前调试任务的素材规划 Worker，不是新的完整 E2E 验收。本次不调用 Provider，不写实验项目或运行数据库，不重启后端；原 Trace 保持不变。

## 真实请求与往返

- 项目：`project-5b8286dc63e6`；任务：`studio-job-20f086971171`。
- Worker：`work-order-5aa6b485cec14f60acdf9803f9d69ee1`。
- 请求：`model-8c5e2af16a10`，发生在 t9 `artifact.patch_asset_plan` 校验失败后。
- 输入：`20260911T214133535572-320f4ba1-model.input.json`；供应商记录：`20260911T214147599914-ec32646f-provider.language_generation.json`。均在 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`。

原请求本地 o200k 估计 **125,166 tokens**，其中正文 118,201、instructions 6,965；供应商实际输入 **125,176**、输出 457、耗时 **14.05928 秒**，没有图片输入。请求正文为 308,527 字符。

| 原输入部分 | 本地 tokens |
| --- | ---: |
| working_set | 99,347 |
| 其中 read_observations | 67,082 |
| 其中 entities | 25,895 |
| 其中 latest_handoff | 1,036 |
| tool_schemas | 8,663 |
| available_tools | 4,479 |
| context | 2,232 |
| work_order | 1,661 |

逐字段独立编码的数值不保证相加与整体编码完全相等。失败回执约 1k，不是本次 125k 的主要来源。

t1–t8 依次为规划上下文、完整资产计划、当前项目摘要、10 项工作切片、图片任务清单、视觉输入报告清单、工作切片的精确正文分页、最新计划索引。t9 失败是 `REUSE_EXISTING` 缺少已完成的精确版本；随后 t10 读取失败参数、t11 以候选语义成功保存，不能把失败调用当成已登记完成。候选/完成与 QA 发现性另见本轮对应修复，本文件不改变这些合同。

该 Worker 本段 24 次语言输入的 `response_protocol_feedback`、`tool_schema_feedback` 都为空。超出 21 个 Worker turns 的 3 次语言调用是首次发现 Tool schema：规划 context/plan/index/image.capability，image.list/qa.list_visual_input_reports，以及 reconcile。它们实际共计供应商输入 234,640、输出 729、耗时 29.275335 秒；后续定义已保留，没有证据表明它们是重复发现或 JSON 修正。具体 Tool 是否选对应单独按用途判断。

## 缺口一：当前正文被压缩，旧来源正文却完整留存

t1 `artifact.get_asset_planning_context` 读取当前分镜 **v10**（`production-document-version-2275fa97b356`）；t4 `artifact.get_asset_work_context` 按现有 AssetPlan 指定的分镜 **v6**（`production-document-version-0b577c62b1de`）读取。两份都是实际读取结果，各自来源有效，不能按相同镜号把它们视为相同内容。

正常模型投影把两个读取中的 20 镜列表整理成索引和精确 snapshot pointer，但原 Shot entity 只从工作切片和单镜当前读取建立。因此，后续正常输入完整保留的是 v6 的 20 镜；当前 v10 完整正文需要另行回读。

六个镜头确实不同：

| 镜头 | 当前 / 旧修订 | 实际差异 |
| --- | --- | --- |
| shot-yqq-03 | 3 / 2 | 连续对白的 audio_intent、timeline_beats |
| shot-yqq-03-evidence | 2 / 1 | audio_intent、dialogue_line_ids、timeline_beats |
| shot-yqq-08 | 2 / 1 | 实际媒体 references |
| shot-yqq-12-reaction | 2 / 1 | timeline_beats |
| shot-yqq-13 | 2 / 1 | 时长、动作、构图、音频和连续性等 |
| shot-yqq-14 | 2 / 1 | 时长、动作、构图、音频和连续性等 |

修复复用原有 Shot entity：当前规划读取进入相同 fold，完整保留本次实际返回的 Shot、修订与当前 Storyboard identity，标记 `CURRENT_STORYBOARD_READ`。较低修订不能覆盖新修订；相同修订的 pinned 读取也不覆盖已知 current 来源。原 pinned READ 继续保留原身份及精确快照，未改写成当前来源。目标 Shot 成功写入后的失效和无关镜头保护沿用原规则；最新主动读取只在 handoff 展示一次，下一轮再保留实体。

## 缺口二：每个精确 marker 重复携带同一 API 包装

原输入有 76 个 `context_ref/pointer` marker。各 marker 反复携带 Tool 名称、project/run ID、snapshot ID、pointer、分页默认值和通用说明。仅在内存删除这些重复 `read/note` 包装、保留实际 locator，可减少约 9.1k tokens；这只是定位实验，不是最终实现的完整收益。

最终从 marker 产生处修改，不遍历或改写用户正文：

- marker 保留 `context_ref`、JSON `pointer`、实际必要的估计/索引元数据；共享正文附精确 `visible_at`。
- 模型顶层只保留一份 `context_read`，明确 `agent.read_context` 的 project/run、snapshot_id 映射、原 pointer、offset/limit、next_offset 分页，以及“快照不自动等于当前”的边界。
- 该说明置于预算外置候选之外；最终预算生成的新 marker 使用同一合同，之后按真实发送文本重新计数。
- snapshot 仍是原有持久化内容，不增加一套历史副本。没有修改 Tool 参数或响应 API、持久模型或旧 Trace。

采用依据：[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 的按需引用与高价值当前信息保留，以及 [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 的相关返回内容、明确工具用途和 token 效率。本次没有采用全局 last-N 或用摘要替代需要编辑的事实。

## 同源离线重建

`backend/tests/replay_asset_planner_context.py` 用只读 SQLite 提取同一 Worker 的前 9 个真实 turns，保留原请求的目标、任务输入、instructions、Tool schema 和当时 progress；原快照只复制到临时测试 store。按最终实际 fold → 正常读取投影 → ModelInputCompiler 重建并计数。

| 项目 | 原请求 | 修复后离线重建 |
| --- | ---: | ---: |
| 估计完整输入 tokens | 125,166 | **117,165** |
| input_text 字符 | 308,527 | **280,874** |
| Shot 实体 tokens | 20,349（旧来源） | **21,383（当前来源）** |
| 完整当前 Shot | 以旧来源正文为主 | **20 镜当前正文** |

净减少 **8,001 tokens，约 6.4%**；恢复当前事实所需的 **1,034 tokens 增量已计入**。没有触发总预算溢出外置。修复后供应商 usage 和实际耗时尚未测得，不能把离线差值称为新真实整轮提速或少一次调用。

修复后的 117k 不含另一套 inline v6 全镜正文：旧工作切片的 `storyboard.shots` 已由正常读取投影替换成短索引和精确快照 pointer，`storyboard.identity` 仍明确为 v6；当前实体保存 v10 全文。最新主动读取仍完整交付一次，随后按 retained READ 合同投影。这一已具备的外置不需要再加一层当前/历史缓存。

精确同值的制作规范与资产计划索引在多个 READ 中仍有重复；当前/旧分镜又包含不同内容。这一轮只修已证实的当前正文优先级和包装冗余，不为追求更低数值删除规划五组镜头所需的信息，亦不扩成跨工具通用内容缓存。

## 实现与验证

- `production_manager.py`：当前规划 Shot fold、当前/历史修订优先级、最新 handoff 单份正文。
- `agent_runtime/context_markers.py`、`tool_context.py`、`model_input.py`：源头紧凑 marker、共享可回读合同、最终预算及计数。
- `test_asset_planner_context.py` 与真实来源 fixture：两种读取顺序、20 镜全文、重启真实 Worker 输入、目标写入失效/当前重读、旧来源可回读、总预算外置和用户 JSON 不变。
- 原相关测试同步到共享 API 说明和更小 marker 后的预算触发方式；真实 Registry 分页读取仍验证准确内容。

最终相关 **56 项通过 / 6.28 秒**，上述 4 个源文件及新测试、重建脚本和 2 个同步测试共 **8 个文件 Pyright 0 错误、0 警告**。独立只读复核未发现具体缺陷。测试和离线重建不代替真实产品续跑验证。
