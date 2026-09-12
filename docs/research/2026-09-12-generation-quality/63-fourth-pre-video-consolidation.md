# 第四轮视频前集中修复

状态：2026-09-12，第三次空项目已自然失败并结束，不恢复。当前先集中修改和回放，尚未启动第四次空项目，更未创建H3视频任务。

[第三次空项目](62-third-fresh-pre-video-end-to-end.md)在29分10秒后因Leader单次规划输出超限而失败。已完成剧本、15镜分镜及基础图像，5次图片任务全部成功，但尚无具体镜头媒体绑定。65次Tool成功和基础图像可用仍不足以证明成熟交付。本批继续以现成真实请求、结果和模型输入为依据，先收敛已查明的问题。

## 本批范围

| 问题 | 处理范围 | 必须保留的行为 |
| --- | --- | --- |
| Leader规划在12,000输出token用满后直接终止整个任务，输入约43.6k | 紧凑、基于引用的当前可执行分工；实际允许省略默认字段；输出超限可进行一次明确纠偏规划 | Astra/xhigh和原输出上限不变；截断JSON不执行；暂停/新消息仍在决策边界接管；已成功的媒体不重发 |
| 两个角色各有母图及脸图，却各用两个无新增判断的登记回合 | 为同一对象已有的多版本/用途增加很薄的批量ReferencePack登记，保留单条兼容 | 实际选图和用途仍由Agent决定；当前可用DRAFT可登记；不改变current/approved，不编造QA；全验证一次保存、重试复用 |
| 两次最终交接共5,100可见tokens，再写已存路径/尺寸/检查事实 | 允许明确选择本工单已保存工具回执；模型集中提供完成判断、新发现、限制和下一步，运行时附已有精确产物 | Tool成功不自动等于任务完成；保留实际QA用途和限制；旧交接继续兼容，完整原文可回读 |
| 排版参数首次保存后消失，补读项目又只留下current文字v1 | 按精确版本保留文字图版、裁切、图集、来源与本地操作参数；项目模型视图增加本任务明确选择或实际生成的非current版本 | 不加入其他项目或无关历史版本；不猜坐标、不提升审批状态；大结果继续由现有上下文编译器持久化并提供精确指针 |

## 排版与派生参数：已经实现的真实输入回放

源码提交`f1aec07`。真实夹具提取第三次纸张工单t1/t2/t4/t7原输入输出；只移除便利整项目副本，`project.get`保留实际纸张对象，未改写选中图版参数与QA。这个回放不需要新图像或语言Provider请求。

正常模型输入现在保留v2的font_size=240、content_box、实际STHeiti字体及签名线端点；原QA的划线坐标与用途继续原值保留。派生版本也保留精确父图、源图和保存的局部透视参数；裁图保留原图尺寸与像素框，图集保留对应来源。外部生成的长prompt不作为这些结构参数重放。

`project.get`模型视图保留current/approved，以及本任务scope、结构化输入、实际工具版本选择和成功输出明确涉及的版本。非current的DRAFT字版能够在读取中出现，无关旧版本不因此进入工作集。读结果与实体中相同版本只显示一份，读结果退休后仍可保留相应实体。原Tool/数据库没有修改，current、approved和实际检查结果不变。

Tool说明也指明：当前内容索引不承担图片施工参数查询；已知成功Tool的精确参数优先由实际`attempt_ref`读取，不能为了找回已保存参数再付费审图。这补足已有工具的使用边界，没有新增第二套工件存储或通用图像重做流程。

该项及相邻工作集、场次回写、图片候选、工具历史、预算处理共53项通过，17.88秒；相关5个源/测试文件严格Python检查0错误/0警告。包括Store重开后的实际Worker请求、按scope/结构化输入/本工单输出选择非current版本、无关历史排除、原坐标不变，以及预算整理后的无损回读。其他三项合并后仍需统一验证，不能把分批结果加成独立总数。

## 已整合的其他接缝

参考登记提交`e4f9f2d`新增`artifact.add_reference_entries`，同一主体的1–16项已经确定用途在一次短事务内全部验证、一次保存；原单条修改合同保留。真实两个人物各2条与纸张4条登记已经通过实际Registry在临时项目回放，另覆盖并发、跨集共享、缺媒体/错谱系/既有用途冲突以及同内容重试。相关70项通过，10.19秒，7文件严格检查0错误/0警告。

交叉复核发现批量工具每条部分交接仍携带整个批次的引用，可能把无关图带入只选单版本的Worker。提交`0893f9a`将其收窄为实际该条目、版本、参考包及原回执中已有的相关母图；完整批次原文仍可由同一指针回读。26项相关检查通过，4.39秒；这组与上述70项有重叠，不累加。

规划修复提交`5741c83`，详见[输出超限专项](64-planning-output-limit-replay.md)。新的未完成正文只保存常规Trace，历史缺失正文不补造；第一次超限可带明确反馈重新生成一份完整计划，额度不会被schema发现重置。16个专项及完整语言Provider文件共50项通过，6.55秒；另有Leader读取/预算/发现的相邻检查通过。两条旧H3测试仍假设不存在已上线的单镜/整组说明，已按现有合同补齐准确断言，本批未改变H3行为。

完成交接提交`5d4ff7d`，详见[保存结果交接](65-saved-completion-handoff.md)。可选的`saved_tool_result_sequences`引用本工单已成功的Tool，运行时提供实际产物、文件检查、QA用途、制作参数及原文指针；模型仍负责完成判断与额外限制，正式自定义交付合同继续验证。短索引按精确产物集合更新，大索引沿用现有分页；页面登记计数也识别显式选中的结果。该项及5个相邻测试文件共54项通过，10.65秒，6文件严格检查0错误/0警告。

交叉复核抓到一次范围错误：母图的交接会因裁图引用了母图而意外带入裁图。已改为按观察和元数据的直接主体过滤；独立回放确认，只选母图不携带子图，选裁图保留其真实母图来源，QA/用途仍对应实际选中图片。另4项窄回归通过，与前述54项重叠，不累加。

公平比较使用同一真实夹具、同一submit和Leader投影。原两次模型输出为2,504/2,596 tokens；人工写的最小协议示例各132 tokens可以通过，但不是实际模型产物。完整Leader交接及refs反而从2,788/2,853增加到4,430/4,447，因为保留了旧手写报告未完整提供的QA范围、用途及制作参数。已经合并重复读取说明；本批不能据此宣称总token下降或能省下原85秒。实际表达长度、使用情况和速度须由新任务验证。

规划、版本投影和完成交接均已交叉复核，未发现剩余具体阻断。以上均未加载为新的真实制作试验。

## 公开方法与本地采用

- [Anthropic工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)建议围绕实际任务设计工具、合并适合的底层操作并返回有效上下文。本地批量仅覆盖已选定、无新增判断依赖的参考登记，不为减少调用而合并不同视觉检查或跳过创作决策。
- [Anthropic上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)讨论持久笔记、按需读取和必要上下文。这里采用“已存事实由运行时引用，判断由模型补充”，同时保留精确排版与QA坐标；不是简单缩短所有输入。
- [ComfyUI Server Overview](https://docs.comfy.org/development/comfyui-server/comms_overview)说明提交完整工作流及控件值，[Server Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)提供按prompt_id回读历史的接口。本地借鉴保存实际施工参数并按已有任务引用取回的方法，不把UI上的当前候选当作唯一可读取版本；未为此引入ComfyUI新依赖或模型。

以上是公开文档及本项目实际输入支持的工程选择，不表示掌握MiniMax Design、即梦或Kling的闭源通信协议、提示词或内部上下文实现。既有产品和公开H3方法研究继续保留；当前gpt-image-2与真实视频主链H3没有替换。

## 后续验收

整合源码`5d4ff7d`在主工作树执行35个相关测试文件，共555项：554通过，1项因旧Tool描述逐字断言失败，耗时297.00秒。该断言仍要求“不会触发任何真实生成”，而上一批工具说明已改为“不生成媒体”；本次仅将测试对齐当前相同含义的描述，没有修改产品行为。随后整个`test_tool_registry_coverage.py`的3项通过，3.61秒；其他554项未重复运行，不将重叠结果相加。

整合命令在backend目录使用`.venv/bin/python -m pytest`选择以下文件并传`-q --tb=short`；完整调用与结果保存在忽略目录`output/quality-e2e-2026-09-12/fourth-consolidation-tests.json/.log`。

```text
test_planning_output_recovery.py test_language_provider.py test_reference_pack_batch.py test_partial_media_handoff.py
test_saved_completion_results.py test_completed_worker_handoff.py test_completion_receipts.py test_asset_construction_context.py
test_image_candidate_context.py test_asset_worker_context_efficiency.py test_harness_tool_context.py test_model_context_budget.py
test_leader_read_context.py test_production_manager.py test_production_skill_loader.py test_mature_skill_compilation.py
test_tool_registry.py test_tool_registry_coverage.py test_scene_write_context.py test_storyboard_write_context.py
test_failed_plan_local_read_continuation.py test_failed_plan_continuation.py test_shot_registration_repair_context.py
test_fresh2_shot_media_registration.py test_shot_media_registration.py test_reference_planner.py test_asset_multi_reference_versions.py
test_studio_method_activity_drafts.py test_studio_harness.py test_studio_delivery.py test_reference_anchor_write_paths.py
test_shot_reference_anchors.py test_shot_write_reference_receipt.py test_asset_inspection.py test_no_mock_usage.py
```

本批22个Python源/测试文件及随后修正的1个测试文件，经本地`.venv/bin/pyright`严格检查均为0错误/0警告。用既有Fake执行`backend/export_openapi_schema.py`及本地`openapi-typescript`同步了可选保存序号与字段说明；`./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/vite build`均通过。构建保留既有Browserslist数据过期、bundle大于500kB提示；未安装或更新依赖。相关文档本地链接与`git diff --check`通过。

合并全部四项、完成真实失败输入回放和相邻检查后，统一加载源码，再通过桌面新建空项目、只提交同一原始brief一次。四个已结束案例不恢复。运行中只读观察实际作品、模型输入和耗时，发现需工程修复的问题时记录本轮未通过，不在同例修后续跑充作稳定验收。

新项目须完成全部镜头媒体绑定和按2K/9:16可执行的生成输入，且实际质量、往返和耗时可接受，才进入视频生成验证。当前没有足够依据预测完成时间或宣称综合性能已优秀。
