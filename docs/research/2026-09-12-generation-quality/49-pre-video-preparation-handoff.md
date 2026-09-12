# 视频前准备的范围交接与缺失计划反馈

状态：2026-09-12 独立工作区完成修改及局部回归；尚未加载到真实运行。当前《一起签》仍是包含工程干预的调试案例，新空项目端到端尚未开始。

## 实际问题

近景视频输入编译工单 `work-order-5f47ef21087143c69d54f76fb53eb3fa` 只负责 `shot-yqq-08` 和 `shot-yqq-12-reaction`，明确不得改整组或其他镜头。两镜分属现有 ClipGroup，Leader 同时要求保存可执行路线。该工单实际初始 Schema 已包含 `route.prepare_h3_group`，因此不是工具不可发现。

08:59 的时间码误拒另见 [48](48-h3-time-range-equivalence.md)。本工单 t7 重试单镜编译成功，返回 `candidate_id=null`。09:02:22，t8 将该完整 Prompt 与三图顺序放进 `shot_directives`，遭遇 `Authored prompt/media requires an explicit generation-unit candidate`。当前 single-shot 编译与 generation-unit 路线交接不是相同合同，不能仅删掉校验让已编译内容被忽略。

09:03:18，t9 读取 `route.get_generation_plan` 失败，错误只有 `'video.generation-plan'`。实际参数只有 project_id，没有伪造 document ID；项目此时确实尚未编译 GenerationPlan。这个内部 kind 名称不足以说明缺项及可用准备入口。随后 Worker 转走旧单镜自动路线：t10 成功保存 `h3-route-plan-253a32034792415980b77a0807819c8a`，t11 再读它检查。该路线保留同样三张媒体和原句，但实际 Prompt 来自单镜确定性拼接，不是 t7 已通过的英文编译结果。此时仍没有视频任务，保存 READY 不证明当前全部组准备完成。

09:09:31，另一反应镜再次以相同的 authored 单镜参数保存，出现相同校验错误（事件 9709）。自定义 Pydantic 校验的跨字段条件没有写在原 `prompt_override` 和 `media_inputs` 的字段描述中，本轮将该条件直接补进实际 Tool Schema，而不只留在上次错误或角色方法里。

## 公开方法与最小改动

[Anthropic 工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)建议根据真实任务记录改进工具边界、说明和有用的结果上下文；[Agent 构建](https://www.anthropic.com/engineering/building-effective-agents)主张使用足以解决问题的简单组合。本次直接检查已有 Tool、Skill、实际输入和回执，不假定闭源 MiniMax Design、即梦或 Kling 的内部实现。

已有 `route.prepare_h3_group` 默认 `enqueue=false`，能形成当前组的输入、语言编译结果和路线；需要明确 `enqueue=true` 才创建视频任务。它收到组内 shot_id 时会准备整个现有组。这个范围与“只负责其中两镜且不修改组”的工作单不能混为一谈。

修改限定为：

- Leader 依据本次目标选择绑定、编译或生成；视频前任务可以用组合工具的准备模式。只分配组内局部时，交付该部分的精确输入或编译结果，由组负责人完成整组路线，不下相互矛盾的范围要求。
- Compiler 方法说明完整组准备与单镜编译的不同结果。保留逐项工具选择，不强制所有任务走组合工具；单镜结果不虚构 generation-unit candidate，也不因回写受阻改走会丢弃该结果的自动路线。
- Tool 说明明确 shot_id 对现有组的实际覆盖范围、只准备参数及单镜编译的下游边界。单镜成功回执的 validation_notes 同样告知未创建组候选及交接方式。
- 缺 GenerationPlan 的读取仍不写数据、不调用语言或视频；错误给出“尚未编译”的具体状态，以及按当前组准备或显式编译计划的入口。没有自动补建文档或扩大 Worker 权限。

## 验证

在独立工作区运行 `test_h3_group_operation.py`、`test_h3_generation_unit_language.py`、`test_production_workflow_skill.py`、`test_no_mock_usage.py`，**43 passed / 10.50 秒**。新增真实 Service→Tool 测试覆盖缺计划读取的具体反馈，并确认 ProjectDocument、路线、任务和 Fake 语言调用数均不变化；既有测试继续覆盖只准备、明确入队、重启复用和原样组编译交接。四个相关 Python 文件 Pyright 为 0 errors，`git diff --check` 通过。最后补充的两个字段仅改 Schema 描述，未改参数校验行为；统一整合时再生成接口 Schema。方法文字的效果仍需真实运行及后续新项目验收，不由这些测试推断。
