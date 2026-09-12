# 第六轮发现与下一批集中修复

状态：2026-09-12，第六轮19:49:52自然结束，最终两项来源过期，完整验收未通过。本文件记录实际失败归因及集中实施范围，不代表已修复。运行基线492e271，期间产品源码、Skills及运行内容未由观察者修改；结束并保存旧代码最终验收后才开始本批实施。100k–110k上下文保持正常使用，不以缩短输入为目标。实际过程见[第六轮记录](70-sixth-fresh-pre-video-end-to-end.md)。

## 应集中处理的实现问题

| 问题及实际影响 | 最小方向 | 已保存真实回放来源 |
| --- | --- | --- |
| S13正确PARTIAL引用同工单成功READ，却因无artifact_refs整份交接被拒；后续Leader只拿到旧读取摘要 | 成功READ按明确观察回执保留空refs与精确来源，不虚构产物；真正结果合同错误复用现有反馈，完整保留未接受decision，并在提交真正成功后才清反馈 | fresh6-shot13-replan-fixture.json；fresh6-saved-read-and-result-feedback-design.md |
| 已保存分镜草稿可被read_current读取，QA却只查正式镜头而失败 | QA复用当前草稿解析，记录真实draft来源、版本和用途；不改其他正式镜头接口，不把草稿观察伪装成正式revision | fresh6-draft-qa-contract-failure-fixture.json；fresh6-draft-qa-source-design.md |
| 候选字版明确提供content_state，仍被全局brief.required_visible_text为空拒绝 | 当全局未声明固定文字时接受明确候选状态；已声明的固定内容仍继承，实体载体合同继续保留；不迫使Agent改全局brief来生成一个后续状态 | fresh6-paper-first-qa-text-plate-failure-source.json |
| 读取生成组复用了执行校验，旧计划导致连当前来源和修订差异也无法读取；已在组3/6/1重复触发 | READ返回请求的旧身份、具体差异与标清来源的当前纯内存观察，不隐式写计划；保存/编译/执行继续检查实际来源 | fresh6-unit-context-failure-fixture.json；fresh6-unit-context-failure-review.md |
| 组5自身三镜/七图不变，前镜S13只调整前段表现、交接终态未变，已保存操作却因整镜visual变化被判不同来源 | 编译与来源比较共同采用现有continuity终态；缺项时回退完整前镜。明确这些字段的内容职责，并让既有局部修改入口能表达相关终态；不增加完整性声明或额外模型证明 | fresh6-h3-source-h3-group-operation-5bf72d921a364079a5734b08b2248097.json；fresh6-previous-terminal-scope-design.md |
| 第4组新操作保存后立即失效：refresh补齐S12已有引用后，来源解析把构图媒体owner资产当成新增可见主体 | 正文cue、reference_plan和编译统一使用实际逻辑subject；媒体owner仍保留为文件归属，不能靠忽略visible_assets或强改缓存来掩盖 | fresh6-reprepared-group04-refresh-visibility-diff.json；fresh6-final-pre-video.json |
| Worker主决策只能读文字，生成/裁切后需要另一轮工具选择和嵌套视觉调用才能看图；S04第一次裁切建议也没有真的保留门框 | 成功产图回执携带当前精确候选及必要对照的图片附件，让下一次已有Worker决策实际看图；复用语言client视觉输入，不自动生成QA通过或采用结论 | fresh6-worker-image-observation-review.md；fresh6-worker-visual-input-design.md |
| 纸资产默认封面优先无条件current，显示被拒的局部合成；实际三个可用状态和镜头绑定均存在 | 无效current的默认预览改选已有可用母版，卡片与详情统一；明确状态且保留坏图可查看，不改采用指针、不扩版本界面 | fresh6-paper-ui-selection-review.md/source.json |
| H3首稿反复超过供应商7000字符上限，产生真实额外模型等待 | 用完整六段、多镜实例替换抽象紧凑句，明确各段职责、减少重复及QA元说明；保留原对白/时窗/动作，不截断，不增加新的5000字符门槛 | fresh6-first-h3-prompt-review.md；fresh6-clip03-six-section-organization-review.md |
| 路线list/get的READY只是保存时快照，最新镜头变化后没有返回当前来源检查；Leader收到六组READY时，第5组已有来源差异 | 附加只读的当前来源状态及具体原因，保留原保存状态与修订，不写库；历史路线不统一成为当前组阻断 | fresh6-route-readiness-snapshot-review.md |
| Leader收尾多次仅为发现与已完成工件直接相关的Tool schema调用语言模型 | 按已持久化的正式分镜、生成计划、H3路线或操作类型，预载少量相关合同并复用现有缓存；不按目标关键词路由、不限制工具选择，也不缩减正常100k输入 | fresh6-leader-schema-preload-review.md |
| 第4组原提示词已覆盖修后的省略转场，完整prompt/九图仍在输入，但prepare回执没有plan_h3所需candidate_id；Leader查找后放弃复用，重新编译又生成7951字符超长首稿 | 在现有plan_h3提供精确已存路线/修订/decision引用的显式复用分支；由Agent核对后选择，程序取原prompt和有序媒体并检查当前目标镜头、时窗、对白、文件与Provider合同，不自动采用过期来源 | fresh6-leader-context-pointer-review.md；本轮最后路线保存专项记录 |

三份最小实际输入裁片已整理在`output/quality-e2e-2026-09-12/observations/fresh6-replay-candidates/`，带原fixture定位和Fake入口。它们尚未进入backend/tests，也未执行真实产品回放。使用临时图片搭建文件存在性测试不能冒充图像质量验证。

## 合理拒绝和制作失败分别处理

- 第6镜复用参考条目时，source/role/root相同，但请求覆盖了原tags/description；旧条目与明确接口说明都在实际模型输入。原子拒绝合理，Agent自行追加旧用途后恢复，没有重审或生图。可补错误的changed_fields与现有复用/更新路径，不放宽共享内容保护。
- 分镜父work_unit.id与子group.work_unit_id不一致，及“周明”两处正文锚点歧义，均有真实不确定性。应补精确schema说明、预期ID/候选相邻文字，避免不必要的get_tool_attempt往返；不能随意选择一处或默改显式冲突。
- agent.read_context的正确根pointer是空字符串，Agent自行改为/output而失败，不是此前信息丢失。19:39收尾再次发生：实际completion快照根是arguments/result，正确定位器仍在输入；恢复先读空根，首屏全为arguments，再读/result，合计65.793秒。新完成回执的结果定位直接指向/result、参数仍精确可读；错误给实际可达字段，不宽松猜改请求。原6385字符prompt、九图顺序和路线在前后四份输入中逐值保留。
- 手纸局部合成确实可能截断Provider新轮廓。已有raw/final同次比较在纸签名、S10两次实际发挥作用，Agent直接采用已观察可用raw；不为已解决的选择另建融合框架。S04门框被裁掉是实际几何建议错误，不以所有QA都能取消概括。
- S01两次原机位的窗墙关系未收敛，导演自主改成合法窄过肩覆盖，新图一次通过。S11两次构图也因纸向与落笔关系失败，现改近轴单手特写，仍保留补写行动；须以新实际图片和连贯故事验收，不能靠降低检查要求过关。
- 新S11已实际通过。S12原先仍描述切回署名末段、新S13却省略署名；原第4组编译说明和最终提示词已消解冲突，Leader随后自主同步S12@3及第4组说明。最终正式18镜与保存payload一致，56秒/12句对白顺序完整，未见剩余纸笔交接冲突。保留原先漏同步的问题及后来成功恢复两项事实。局部覆盖变化需要处理受影响邻镜的transition/beat说明，不能把编译时的临时覆盖当成正文已改好。现有终态字符串也不能机械证明与任意visual语义一致；本批按正常内容编辑合同处理这一限制。

## 成熟公开方法的采用边界

Tool错误应给可操作字段和正确调用方式，且可由真实错误频率反推接口设计。这里复用现有工具与结构化反馈，不引入任意代码执行或新调度框架。[Anthropic工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)、[工具加载与调用实例](https://www.anthropic.com/engineering/advanced-tool-use)。

视觉结果直接进入下一次Agent决策是公开可复用的模式。Claude文档支持Tool结果中的图片内容，也说明操作结果可附当前截图；OpenHands公开SDK则用同一用户消息中的ImageContent和TextContent向视觉Agent提供图像。本产品复用现有语言client的图片输入，保留精确版本、用途与观察尺寸；不迁移Provider、不把观察等同检查通过。[Claude工具结果](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Computer use结果观察](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)、[OpenHands图片输入](https://docs.openhands.dev/sdk/guides/llm-image-input)。

失败节点不应迫使已完成的独立结果重做。LangGraph的持久化说明提供了这类完成写入保留的公开实现参考；本产品已有Store/回执，修正信息丢失的边界即可，不为这一问题整体迁移框架。[LangGraph持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)。

H3多参考按身份/构图/道具分责、逐镜时间和动作状态明确，但工作流解释不需要反复写进最终摄影提示词。第3组的离线六段例子5211字符，保留原三句、四镜时间与动作；只是人工组织示例，不能宣称自动首稿或视频已成功。[MiniMax-H3官方参考方法](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)。

空间关系反复失败时可以让布局成为可见输入，必要时只补当前机位一张空场参考，再复用已正确前景；也可以由导演调整非必要背景覆盖。现有image.enqueue、create_edit_guide、crop已足以尝试，不能把空场派生或多视图库设为每镜硬步骤。新视角是生成推定，不能声称恢复不可见真实结构。[Seedream官方介绍](https://seed.bytedance.com/en/blog/beyond-generation-it-understands-design-introducing-seedream-5-0-pro)、[Kling角度编辑说明](https://kling.ai/explore/kling_ai_image_generator)、[Runway参考图进阶方法](https://help.runwayml.com/hc/en-us/articles/41170686463635-Advanced-References-Use-Cases)。

## 后续执行与证明

本轮已自然结束并保存完整结果，现把确认的问题作为一批实施；旧运行不热改或恢复。完成真实失败回放、相关业务回归、产品strict及受影响的前端检查后统一加载，再以真正空项目重跑视频前目标。总耗时、质量修复、工具错误、额外模型往返和无工程干预完成能力一起评估；测试通过与单张好图不能替代完整任务证明。只有完整视频前链路达到用户要求后才验证H3视频。
