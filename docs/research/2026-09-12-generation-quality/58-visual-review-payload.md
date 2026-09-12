# 视觉检查请求：保留制作证据，精简输出协议

状态：2026-09-12，独立工作树已实现并完成离线回放，随`426f8dc`统一加载；[60新空项目](60-second-fresh-pre-video-end-to-end.md)已于12:52暂停，基础图片与局部编辑有独立实图核对，完整准备和效率仍未通过。

## 实际问题与公开方法

《最后一版》在[54](54-fresh-pre-video-end-to-end.md)所记的新空项目验收中，35次视觉QA构成实际制作成本。收尾最近三次图片生成累计329.2秒，而对应三次QA累计699.4秒。它们包含有效观察，不能简单删除；也没有依据把全部耗时归因于输入长度。

实际第一份单人像QA的文字payload为29,644字符，完整输出schema的紧凑JSON为16,008字符。候选没有参考图、不能给本地平面合成建议，却仍携带纸张清除、墨迹归一化、平面层、保护区域等全部定义；同时让模型输出之后必被运行时覆盖的证据、范围、布局和源ID。这些是协议冗余，不是人物身份或画面质量依据。

[OpenAI官方延迟优化指南](https://developers.openai.com/api/docs/guides/latency-optimization)分别讨论减少输出、输入、请求次数及并行，并明确输入缩短通常不意味着同比例缩短延迟。因此这里仅压缩不参与判断的协议，主要返工仍由[56](56-efficient-production-methods.md)的制作方法与交接处理；保持gpt-6-astra/xhigh，不把更低推理强度或更小图像当作默认提速办法。

[OpenAI官方图像指南](https://developers.openai.com/api/docs/guides/image-generation)说明GPT Image的mask提供生成引导，不能保证严格按形状边界编辑。该语义与本次S05原始生成图／本地遮罩结果差异吻合；精简审图请求不改变本地保护区语义，也不替代观察原图与合成图的真实可见差异。原始结果交接另见[57](57-edit-candidate-handoff.md)。

## 最小修改

- QA模型只填写观察与可选修复建议；evidence_references、region_evidence、review_focus、candidate_output_layout、裁切source及编辑guide的source ID继续由运行时按精确候选填写。公开Tool结果结构保持兼容。
- 根据已经解析的实际图像资格生成输出schema：不能本地裁切、编辑或平面合成时，对应建议为null，并移除不可达的定义。没有以故事关键词判断工具是否可用，也没有为了取得几何新增发现或审图步骤。
- 可用时仍提供完整crop/guide/plane协议，包括坐标、角点对应、保护区、白点、paper_ink及显式旧印字清除的限制和默认值。只删除重复字段名的title，保留约束、说明、默认值和全部必要子定义。
- 请求JSON改为紧凑序列化；正文中的换行保持为JSON字符串转义，解析后的剧本、描述、意图和参考信息不变。当前Bible很小，未为追求缩减数字删减风格、身份或空间事实。
- 整合[57](57-edit-candidate-handoff.md)后，若同次masked EDIT已登记原图，检查合成图的请求携带该原图的精确资产/版本引用、实际状态和文件存在性。它不自动进入image_map，不增加发图，不代表已观察或采用。旧任务没有raw ID时不添加新字段，原有35份回放仍一致。

## 真实请求回放

只读取得《最后一版》35份实际QA请求和33份成功模型返回；两次请求体超时没有可重放的模型结果。使用原图片文件、原候选版本、原检查范围、原Shot、原Bible和原参考映射，通过修改后的inspect_asset_image运行；语言client仅返回已保存的原文，没有访问Provider。

35份schema重新编译后的文字输入，用现有离线o200k计数从350,872降至220,833 tokens，减少37.06%。其中3份没有平面来源的请求从21,671降至8,256；32份保留完整平面协议的请求从329,201降至212,577。这个口径仅为QA的input_text，不含系统提示词、图像tokens、Worker决策或输出，不是全任务成本减少37%。

33份回放均确认：除output_schema与JSON空白以外，asset、candidate_creation、review_focus、Bible、Shot、preparation、decision_guidance和image_map逐值一致，实际图片数一致；原recommended/score/summary/issues保留，原有效可选几何保留，源身份仍由运行时确定。离线回放保留旧判断，不能证明新模型在短协议下必然作出同样判断，也没有测出实际延迟改善。

回放脚本及逐调用结果位于忽略输出目录：`output/quality-e2e-2026-09-12/replay_inspection_payload.py`和`observations/inspection-payload-replay.json`。脚本参数为待验代码的backend绝对路径，源库使用mode=ro/query_only，不写回作品。

7个相关测试文件85项通过，覆盖本地与远程候选、不可用修复、真实尺寸/几何、精确源绑定、QA范围、完整平面语义与Store重开；改动Python源Pyright为0 errors。一次既有schema比较只因title删除而失败，已改成比较所有非title内容，数值约束、默认值与说明仍严格相等。统一整合后继续做交叉检查，再进入新空项目；两次请求体超时及实际新QA质量仍须真实验证。

整合新增两项Registry检查，覆盖已登记原图可用及已拒绝/失效/文件缺失时均准确报告，不改变项目或自动补发原图；保持实际候选和既有基图共两张输入。首次新增测试误预期只有候选一图，已核实基图原本就是有效参考，改为精确校验两张ID，未删掉真实参考以适配测试。独立复核确认来源与未观察边界，整批结果见[59](59-second-pre-video-consolidation.md)。
