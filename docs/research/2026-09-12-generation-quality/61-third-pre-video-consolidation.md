# 第三轮视频前集中修复

状态：2026-09-12，第二次空项目已结束验收并保持暂停；本批源码已合并至`f865c27`，生成合同和说明提交为`3202f3c`并通过统一离线检查。13:35统一加载，13:36启动[第三次空项目](62-third-fresh-pre-video-end-to-end.md)，尚未调用视频。实际失败过程与最终统计见[60](60-second-fresh-pre-video-end-to-end.md)。

第二轮在77分1.35秒后仅保存4/12镜头绑定。16次图片生成成功、166次语言调用成功，仍未完成全部视频前准备。由此不能把Provider成功、基础素材好看、局部修图通过或代码测试通过当作产品成熟。这次先处理已保存真实输入能够复现的缺口，暂停三个旧案例，集中修改后再做新空项目。

## 已查明的问题与修改

| 真实问题 | 修改及复用入口 | 保留的判断边界 |
| --- | --- | --- |
| 导演完整读取后只修改08/09镜，新版使其他10镜和6个生成组从当前输入消失；又需整版读取 | Storyboard保存回执按实际父版本与保存结果计算对象增量；工作集保留来源明确且未改的对象，只替换改变对象、顺序和备注。完整历史保留，但不回灌两版当前正文 | 请求base不冒充实际父版本；未知父版本、来源换版及他人已经修改的对象保守退休，不猜未读取的正文。ArtifactRefs说明可见身份，不能直接当修改清单 |
| 房间、闭夹和笔已分别检查并登记，但Leader在素材子任务结束前看不到这些精确结果 | 从已有Tool记录投影`media_observations`，在现有决策请求中交付已保存的部分成果及实际用途。相关Worker也能看同图的已完成或在途观察 | 不增加每个事件唤醒模型；在途不代表通过。不同用途、参考集合及编辑后版本分开；没有证明所有重复QA均可删掉 |
| 跨Worker报告和详细坐标缺少可直接读取的交接路径 | 原参数与完整结果沿用持久化context snapshot及`agent.read_context`分页；内联预算不足时提供精确读取指针 | 历史报告不证明文件现在可用或已采用，不复活取消工单。读取原观察替代为找旧信息重新付费审图；新视觉问题仍需观察 |
| 新局部补读完成，却被继承的另一条RUNNING任务挡住后续Leader决策 | PlanGraph单独识别本版非空新增步骤是否完成，在旧任务继续执行时推进局部决策；完成全目标仍检查所有相关任务 | 空等待不自动循环，暂停、新消息、重启继续保留；持久化空等待在最后Worker完成后的瞬态允许作为当前状态展示，新提案仍用完整验证 |
| 正文引用依赖模型手数Unicode偏移；首版五处选错 | 在现有cue输入增加可选`text_anchor={exact,prefix?,suffix?}`，只在所属Shot/Beat字段逐字定位，保存为现有code-point偏移。统一正式、草稿、局部与导演提案入口 | 不按alias猜图或文字，不模糊匹配、不归一化原文。重复无法唯一定位明确报错；已有人工偏移、自定义alias保留，同时给锚点/偏移必须一致。旧无锚点的合法范围错位不自动修史 |
| `change.apply`改正文没有同步引用，删除第一处人物名可能错绑后面同名 | 修改实际字段时保留未动时段，仅重定位原出现；移除该文字则解除该出现。编辑范围内原有同名多处时明确歧义，改用精确Shot保存；正式内容、草稿及Take失效同一保存事务 | 不把全串剩余唯一名字当作原引用；不自动更换人物和媒体。不另建引用状态或撤销/版本界面 |
| 一张合格构图连续经历alias、主体、母图、缺多个用途四轮保存失败 | 字段合同明确图片容器、实际主体、图片所有者、用途和可选谱系；同次报告独立的登记与cue关系错误。失败模型输入保留所尝试的具体关系，完整原请求仍可回读 | 失败不写入，参考包主体资格与来源检查保留。自有REFERENCE构图可直接绑定而省略可选登记；一图服务多个明确主体时分别登记真实用途，不伪造审核 |
| 编剧先发现最后未用的变更工具，再发现制作规格；两次等待122.59秒 | Writer初始合同增加已有`artifact.create_assets`；工具目录首句说明`change.apply`实际字段与ProductionSpec可选范围、同步字段 | 不预加载全工具、不强制建资产/规格；首请求约增加1181个估算文本tokens。没有实测模型少走两轮或省下全部122.59秒 |
| 首图额外取景偏好变成检查否决条件；胸上景别和髋部包体同时要求；同几何修订仍失败 | QA按当前声明用途判断并保留原方案偏离；导演在保存前核对景别/可见物相容性。素材方法要求几何失败后比较图示、正确编辑范围、现有构图与局部调整覆盖 | 不删用户要求、故事证据或已采用媒体连续性。没有把“换措辞”当新控制方法，也没有声称现有图像模型能准确完成任意空间布局 |

第二轮的真实masked EDIT是有效资产：索引贴换到正确自由页口，原位置干净，合成结果遮罩外0像素变化，原始Provider输出与最终图均有精确版本并进入后续Worker输入。这个已验证路径继续保留；它只证明该次静态修复，不证明开夹动作或另一个全景站位能够成功。

## 公开实现和方法依据

这些是已取得的公开方法，不能据此声称获知闭源产品的内部架构。

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)将状态字段更新与合并规则分开；本次借鉴对象增量与明确合并语义，继续使用本项目Harness和Repository，没有引入LangGraph依赖。
- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)讨论保留高价值上下文、持久笔记和按需读取。本地对应实际工件、最新对象状态、独立观察范围和完整原文指针，不能用压缩摘要替代已知坐标。
- [Anthropic工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)强调职责清楚、结果含有效信息，并以实际任务、错误、用量与耗时评估；本次以真实四次登记失败和编剧发现序列收敛合同，而不是继续堆所有Tool全文。
- [W3C TextQuoteSelector](https://www.w3.org/TR/annotation-model/#text-quote-selector)提供exact与相邻prefix/suffix的数据形状；[Anthropic编辑工具](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool)使用唯一原文匹配及明确歧义。这里一次cue只指向一个位置，与W3C可匹配全部出现的语义不同；也不复制网页文本标准化规则。
- [MiniMax H3公开写作方法](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md)及[全参考指南](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)区分主体身份与具体图片的构图/状态职责。本地明确主体/图片所有者/用途，不把所有构图强行塞入房间包。公开内容没有证明MiniMax Design如何实现编辑器锚定、在途交接或私有Context-IR。
- [OpenAI图像生成指南](https://developers.openai.com/api/docs/guides/image-generation)仍说明精确布局与mask控制存在限制；[草图转实景方法](https://developers.openai.com/api/docs/guides/image-prompting#turn-a-drawing-into-a-realistic-image)以布局输入补足纯文字。当前页面示例已是GPT Image 2.5，不能当作本项目gpt-image-2已验证能力，未据此替换Provider或模型。

即梦、Kling等先前研究继续保留于本目录；本轮没有找到可核实的新公开实现来解释上述本地失败，不能凭品牌推定其内部工具、prompt或通信方式。本批直接采纳有公开依据且能落到真实失败上的方法。

## 离线回放与独立检查

真实输入夹具从第二轮保存的请求和返回提取，路径作测试用归一化；图片本身、Provider凭证和其他项目正文不进入夹具。测试使用临时数据库和既有Fake，不把回放称为新模型结果。

主任务已验证：相关PM/Skills/批量对象保存219项通过；媒体交接、原完成交接、工具上下文、模型预算及图片检查60项通过；四次失败登记关系与媒体交接8项通过。新增Writer首请求直接携带真实批量建资产合同的实际Worker输入检查通过。各命令覆盖有重叠，不相加宣称独立总数；这些是整合前的分批结果，最终整合检查另记。

交叉检查抓到并修正了两个不能只靠字符串定位主例覆盖的问题：改掉第一处人物名时，不能把原cue移到后面的同名；导演未决提案经JSON往返时必须保留锚点，直到实际Shot/Beat字段解析成功。媒体交接回读使用实际Registry在Store重开后读取原参数与结果；不提供只能由原Worker读取的无效ToolAttempt路径。

最终整合树统一回归：31个相关文件、476项通过，193.07秒；另有`test_no_mock_usage.py`的1项检查通过。命令为`backend/.venv/bin/python -m pytest`，在backend目录选择下列文件并传`-q --tb=short`：

```text
test_partial_media_handoff.py test_shot_registration_repair_context.py test_fresh2_shot_media_registration.py
test_reference_anchor_write_paths.py test_shot_reference_anchors.py test_storyboard_write_context.py test_failed_plan_local_read_continuation.py
test_completed_worker_handoff.py test_harness_tool_context.py test_model_context_budget.py test_asset_inspection.py
test_production_manager.py test_production_skill_loader.py test_mature_skill_compilation.py test_asset_batch_create.py
test_shot_media_registration.py test_reference_planner.py test_studio_delivery.py test_shot_write_reference_receipt.py
test_director_current_content.py test_asset_multi_reference_versions.py test_semantic_change_executor.py test_studio_changes.py
test_scene_write_context.py test_asset_worker_shot_context.py test_storyboard_local_edits.py test_storyboard_agent_interfaces.py
test_failed_plan_continuation.py test_h3_generation_unit_prompt.py test_h3_composition_references.py test_shot_saved_content_receipt.py
```

22个改动源文件及8个相关测试经本地`.venv/bin/pyright`严格检查，0 errors/0 warnings。以现有Fake服务执行`backend/export_openapi_schema.py`，再用本地`openapi-typescript`更新前端类型；变化为可选文本锚点和真实字段说明，没有视频Provider调用。`./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/vite build`均通过。构建仍有既有Browserslist数据过期和大于500kB bundle提示，没有下载或更新依赖。`git diff --check`及本批新增文档的本地链接检查通过。C/B合并冲突已独立复核，媒体观察投影也经过只读复核，没有剩余已知阻断。

这些结果只证明本批合同与保存的失败输入经过检查，不证明未来图像能一次成功、所有往返已消除或产品整体已成熟。下一次通过桌面新建空项目并提交同一原始目标一次。运行中只观察实际内容与记录，不补工程提示、不修后续跑同例。旧三案例保持暂停；视频前未自主完整交付前，仍不创建、入队或执行H3。
