# 56：减少自行扩张的精确状态制作，保留实际质量判断

## 问题来源与边界

本轮在 `3c67c0b` 的独立 worktree `codex/efficient-production-skills` 修改运行职责与方法，不修改产品实现、Provider、原始来源、项目数据或旧工单。没有新建素材、视频或运行服务。改动尚未由新完整运行证明有效。

真实新项目《最后一版》`project-8d268085367b` 的结尾构图工单 `work-order-69365aa1a3ab4ed8b364cd20b892ba05` 负责第9—12镜。用户要求两位成人、一个办公室、45—60秒故事与质量，并未指定左右手或每步静态姿态。正式导演设计与 Leader 工单随后固定第10镜的关盖、取包顺序与手别，第11镜两人右手分别握提带，第12镜低位持包后上右肩；Builder 只许制作图片及绑定，不能改故事、对白、时长、分组或其他镜头。

只读实图报告为 ignored `output/quality-e2e-2026-09-12/observations/resolution-builder-repair-review.md`，截至11:06:27；项目后来由主任务在11:07:42正常暂停，约83分钟、24个图像任务、2镜绑定，未达到低返工目标，未把续跑称作通过。本方法研究只据其中已独立查看的最近三图判断，不将全项目制作都计为无效。

| 候选 | 真实进展 | 剩余用途问题与实际处置 |
| --- | --- | --- |
| v4 `asset-version-8f8a191f29d8` | 正确人物、桌端取包可达空间和本人右手；电脑已闭盖 | 提带已向高握点绷直，更像开始提包，非椅背仍承重的初态。Worker保留为中间状态，未原样重提。 |
| v5 `asset-version-293ec22a0807` | 右手接盖、左手扶底座与包挂椅均成立 | 电脑和钥匙相对v4移位；Worker把它作为新的开盖起态，向前派生闭盖。 |
| v6 `asset-version-4d9f729cec2c` | 电脑闭盖、双手撤回、包仍挂椅、其他桌面道具保留 | 电脑占地较v5缩小。QA 3.7/不推荐基于这对图的明确配准要求；随后Worker请求局部几何，尚未再次生成。 |

三图均无mask，不能称为遮罩错误循环。对应生成329.2秒、三次QA699.4秒；前39条Tool含16次自动advance，不能写成39轮语言决策或39次修图。照片中有真实进展和可复用像素，同时一个6秒镜头已演化成“倒推开盖→正推闭盖未触包”的多张严格同机位状态，值得由设计负责人重新判断必要性。

## 公开方法依据与本地推断

- **MiniMax官方H3事实**：[H3公开Skill](https://github.com/MiniMax-AI/cli/blob/main/skill/h3-video/SKILL.md)及[H3方法说明](https://github.com/MiniMax-AI/cli/blob/main/skill/h3-video/references/h3-video.md)区分首帧/首尾帧与多模态参考，两模式不能混用；动作时间线应交代角色、接触、持物和连续状态，完整的用户分镜应保留。它没有要求每个动作节拍另生成一张照片，也没有公开Main Agent内核。**本地推断**：保留必要物理顺序不等于将Agent自定手势和所有微状态升级成用户不可改要求；设计可以更换，但当前执行者不能静默改掉已保存来源。
- **Kling官方事实**：[Elements说明](https://kling.ai/quickstart/ai-video-character-consistency)把主体参考与起止帧用途分开，用参考保持主体，用prompt描述互动；[VIDEO 3.0官方指南](https://kling.ai/quickstart/klingai-video-3-model-user-guide)分别呈现首帧、元素参考和多镜控制。**借鉴**：先确定参考负责身份还是边界，不以参考数量代替用途。Kling的组合能力不是当前H3接口能力；本轮未接入或测试Kling。
- **即梦/Seedance检索边界**：本轮检索到[火山Seedance 2.0提示词指南](https://www.volcengine.com/docs/82379/2222480)，但正文抓取返回错误或无正文；没有把搜索摘要或开发者社区文章作为已核实的官方具体规则，也未据其推断账户能力。

以上是公开接口/方法与本地制作选择之间的类比；“何时回Leader”及作品是否足够由本产品Agent判断，不能声称来自闭源内部调度。

## 原因与实际修改

此前方法已有复用、少造图、按用途检查、可返回Leader的条款，但返工交接主要以Provider错误或计划缺口举例。真实Provider成功返回，Worker也不断解决小问题，所以“可替代的表现设计正持续增加状态图”没有成为明确的重新判断信号。与此同时，导演逐项写起始/行动/反应/终态的措辞，容易与狭窄工单叠加成逐张照片要求。

本轮替换六个现有文件的23个段落，不新增必填表、Role、Tool、固定重试轮、预算门或批准阶段：

| 文件 | 改动 |
| --- | --- |
| `backend/short_drama/prompts/runtime/leader.md` | 工单区分用户明确要求/已生效编辑、因果与采用媒体连续性、Agent表现方案；指出允许调整的范围与负责角色。收到PARTIAL时比较局部修图与调整覆盖，保留成果，不原样重派或降低标准。短篇写作不默认捆绑多份可选制片文档，真实接口必要合同仍补齐。 |
| `backend/short_drama/skills/shared/roles/director.md` | 保留关键动作顺序与可读结果，只细化影响理解的手别/姿态；示例改为设备关好再离开，避免每步停住并造图。已有采用画面的连续性仍须保护，局部修改仍走现有保存工具。 |
| `backend/short_drama/skills/shared/roles/asset_builder.md` | 不只在Provider报错时考虑交接。修图损伤正确部分或微状态配准扩张时，用现有PARTIAL、LEADER_REPLAN、findings/next actions及真实引用返回；当前工单不授权时不自行改正文或用途。保留明确图像操作、裁切、局部编辑、候选登记与并发说明。 |
| `backend/short_drama/skills/shared/roles/asset_verifier.md` | 按真实用途说明缺陷及观众影响，保留可用与未观察部分；构图参考和精确边界对的标准分开。summary聚焦本次事实/采用范围，issues写实际缺陷，不复述全brief或程序已附元数据，不强制极短回答。按像素、裁框和用途决定是否需要新观察，已有效覆盖的裁用/仅登记不因版本号变新再检；Tool内观察不输出独立工单字段。 |
| `backend/short_drama/skills/methods/drama_series/references/production-method.md` | 保留表演、空间和接触知识，精简身体部位清单；状态设计不自动变成逐拍素材。Agent可调整方案须由负责人连同受影响镜头保存，不把局部图当前不满足的事实改成通过。 |
| `backend/short_drama/skills/methods/drama_series/references/video-compilation.md` | 当前来源与有序引用不变，明确COMPOSITION软用途及整次请求首尾边界，防止组内逐镜/逐beat帧槽误解。 |

实际适配依据为 `h3_models.H3MediaRole`、`h3_group_operation` 的输入策略：REFERENCE里的COMPOSITION指导构图/状态，FIRST_FRAME/LAST_FRAME承担整次请求的边界，不能与多参考混用。软参考不保证像素复制，也不允许身份错置、凭空换手、错误受力或缺关键证据。若确实选择同机位精确状态对，当前v5/v6的占地漂移仍是问题；调整引用用途须先由有权角色明确并保存，不能仅改评语放行。

## 真实输入回放与验证

`tests/replay_efficient_production_methods.py` 读取四份已保存真实请求，只用此提交的段落替换原instructions中实际出现的相同段落；未加载角色/章节不注入，input_text原文、图像数量、原工单和Trace不变。不读取或写SQLite，不执行Tool或Provider。公共角色由当前Loader完整对照，方法按实际章节出现情况核对。命令在backend中使用已有环境：

```sh
.venv/bin/python -m tests.replay_efficient_production_methods --trace-dir /path/to/project-8d268085367b/studio-trace/studio-job-57ebf2b878a5
```

四请求均原始gpt-6-astra/xhigh。表内为o200k文字估计；QA真实usage还包括图像与封装，不能直接与纯文字估计相等：

| 角色/原call | 实际替换段落 | instructions前→后 | 完整文字估计前→后 | 原Provider input/cached/output；秒 |
| --- | --- | --- | --- | --- |
| Leader `model-16e18114fcdf` | Leader 3 | 5371→5512（+141） | 42103→42244 | 42113/5374/10040；182.597 |
| Director `model-5f097296bb1d` | shared 4 + method 2 | 7476→7547（+71） | 25087→25158 | 25097/0/119；5.146 |
| Builder `model-c3917c997b2c` | shared 5 + method 1 | 9667→9680（+13） | 88869→88882 | 88879/9670/702；16.197 |
| Verifier `model-8a539199d75d` | shared 4 | 3988→4259（+271） | 14198→14469 | 27112/3991/8917；192.156 |

精确文件位于该job目录：`20260912T021448292860-243a371b-model.input.json`、`20260912T015606742151-798d99fe-model.input.json`、`20260912T030610722975-ff5e892d-model.input.json`、`20260912T030257450690-02fbe8a9-model.input.json`。新方法没有减少总输入tokens；这张表验证加载与体量，不能推算提速或省去多少次生成。

`test_efficient_production_methods.py` 的5项覆盖实际三角色/阶段组合、Leader运行说明装配，以及精确真实工单与t35完整QA回执保存→重开Store→Worker输入→现有PARTIAL返回。原constraints仍禁止Builder改分镜，原PROP_BASE_POSITION_DRIFT与精确v5证据仍在，视频前停止要求未变。返回值由Fake给定，证明协议可用而非语言模型已学会择路。fixture只摘取本次必要原字段与一个真实完整回执，没有伪造旧成功、修改旧Trace或生产库。

验证：新5项及原Story质量/Workflow/成熟编译/全角色方法测试共68项通过；最后说明调整后新5项再次通过；两份新增Python测试/回放脚本Pyright 0。没有付费调用或真实任务续跑。

## 局限与后续验收

这是方法修正，未新增代码去自动判定“用户硬要求”或计算返工阈值。旧工单仍具原范围，改变只能正常交回负责人；新工单是否合理授予局部设计权限仍需实际观察。它也不保证H3可以完成复杂交接，更不能以少造图替代关键接触、身份、空间、对白与真实视频验证。

后续在原始目标下的新完整制作观察：是否先说明必要参考用途，是否保留正确媒体，是否仅为故事/实际边界所需状态补图，以及返工扩张时是否正常交回具体方案。分别记录制作次数、语言/QA耗时和真实质量，不把工程补prompt、改Skill或重启后的继续当作未经干预的完整通过。
