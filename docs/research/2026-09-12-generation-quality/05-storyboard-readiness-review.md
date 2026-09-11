# 《一起签》实际分镜的制作准备检查

本次只读检查的是产品 Agent 从空项目写出的当前剧本与分镜，不是开发者另拟的故事。项目为 `project-5b8286dc63e6`，导演工单为 `work-order-e523f7976b014a3aa367ec1ffb4185b1`。观察截至 2026-09-12 02:28:20（北京时间）：14 镜已写入当前 ProjectDocument，`shot.finalize_storyboard` 成功；导演随后自行检查并提出第 12 镜局部修正，修正 Tool 尚为 `REQUESTED`，不能记成已保存。

依据为当前 SQLite 项目记录、当前资产 brief、导演真实模型输出与 Tool Trace。这里只判断文本与已有能力的对应关系，没有进行真人演读、图像/视频生成、播放听审，也没有改变项目内容。

用户已更新验收范围：先调试全部视频生成前链路，逐项研究并整改，再从空项目重跑完整前置 E2E。当前任务暂停；修复后继续本项目只算调试记录，不算稳定验收。前置链路成熟前不得提交 H3。

## 当前分镜与具体观察

| 检查点 | 当前实际内容与判断 | 后续验证边界 |
| --- | --- | --- |
| 对白与动作能否容纳 | 14 镜分别为 5.5、3、4、4.5、5.5、2、2.5、5、5、5、3、4.5、3、2 秒，总计 **54.5 秒**，不是精确 54 秒。七句原对白共 **83 个汉字**，按实际标注的发声窗口合计约 23.2 秒，约 3.3–4.0 汉字/秒；其余时间用于动作、停顿与反应。第 9 镜留 5 秒给看人、看空栏、看旧报价、松手，没有明显的对白总量超载。 | 这是可表演性推演，不是实际声音测量。镜 12 的 1 秒转纸、3 秒签字和镜 14 的 1.2 秒取帽扣帽较紧，需在真实素材中确认完整动作与收束；不能用整体倍速掩盖局部动作未完成。 |
| **连续准确签名：待整改** | 镜 10 要求 3.3 秒不切镜写出“赵衡”，同时保留空白经办栏，末尾仅 0.8 秒露出完整结果。镜 12 要求 3 秒不切镜写“许岚”，保留复核栏原签，末尾仅 0.5 秒展示双签。这同时要求持笔接触、字形随笔画出现、签对栏、另一栏不变和可读结果，属于本次最高概率的生成返工点。 | “展示已有签名证据”与“持续看清真实书写”不能合并为同一能力承诺。剧本只要求先后签字和责任留下记录；当前分镜额外选择了连续清楚书写，是否成立需要后续适当的制作方法和真实验证。此检查不代替 Agent 修改镜头。 |
| **不存在的跟踪修复：待整改** | 镜 12 的 `local_rework_risk` 写了“必要时用准确图版局部跟踪合成签名”。现有 `image.compose_text_plate` 只能用本机字体按行排版 PNG；`timeline.add_asset_overlay` 只静态叠层，没有纸面透视跟踪、手部遮挡或笔画动画。当前实际 Builder/POST Skill 已说明这个限制，但产品生成的分镜仍把它写成了后续修复办法。 | 不能把静态字版成功当成运动纸面签名已有可靠兜底。需要整改真实导演输出和后续制作判断中这项能力误用，并在新空项目复跑确认，不只增加一段笼统提示或改本案例后即宣布解决。 |
| 静态插入证据 | 镜 3 展示报价单**已有**红改与“确认：赵衡”，明确不写字，末尾静止 1.4 秒；镜 6 展示说明双空栏，放笔后静止 0.8 秒。它们比连续签字更适合已有字版/静态插入路径。但纸页顶朝东、供西侧赵衡阅读，同时机位坚持南侧斜俯，观众会侧向看纸面；“有文字”和规定停留时长不能单独证明看懂了证据。 | 后续按真实画幅中的字面大小、角度、手部遮挡和停留确认。镜 3 不需要持续真实书写，不能被误扩成再生成签字动作。 |
| 轴线、两纸与笔帽连续性 | 当前人物为许岚东座、赵衡西座，南侧机位下赵左许右，赵看右、许看左。报价单旧签名始终不变；说明依次双空→仅复核赵衡→经办许岚与复核赵衡，签全后才收纸。笔帽为开场盖尖→许左手放东南桌沿→中段保持原处→结尾扣尖，与当前笔 brief 一致。第 12 镜曾写“转半圈”后标题仍朝东，和许岚应正向阅读矛盾；**产品导演已在自己的回读中发现，并请求仅改为朝西**。 | 不重复人工修改导演已经提出的修正。恢复调试后确认该请求实际成功保存，再观察新项目是否仍出现同类错误。此前笔帽冲突曾由开发观察后发送补充要求，不能宣称这次笔帽一致完全由产品自行纠错。 |

## 可复查来源

- 当前记录：[本次运行数据库](../../../output/quality-e2e-2026-09-12/runtime/short-drama.sqlite3)，`projects.document_json` 中的 `shots`、`assets`；原文对白为当前 `script-v3.lines[].spoken_text`。后续调试会继续改变数据库，历史文本以对应 Trace 为准。
- [第 1—3 镜实际写入请求](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182002442353-37029e53-model.output.json)、[第 4—7 镜](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182152701969-44898348-model.output.json)、[第 8—9 镜](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182319233490-1d10cc69-model.output.json)。
- [第 10—11 镜签字和交笔](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182447553383-d167898f-model.output.json)、[第 12—14 镜签字、收纸和扣帽](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182706800539-cd29ba8f-model.output.json)。
- [finalize 成功记录](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182718982112-acb86353-tool.finished.json)、[导演自主发现朝向错误并提出局部修正](../../../output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/20260911T182820006816-af5b7e50-model.output.json)。
- 当前可用能力：[字版请求模型](../../../backend/short_drama/image_models.py)、[本地字版渲染](../../../backend/short_drama/asset_workbench.py)、[静态图版叠层](../../../backend/short_drama/studio_overlays.py)、[实际 POST 职责](../../../backend/short_drama/skills/shared/roles/post_director.md)。本轮已通过 `shared_role_prompt` 与 `compile_production_method_skill_prompt` 读取 ASSETS/ASSET_BUILDER 和 POST/DIRECTOR 实际加载正文，确认限制并非只藏在未加载的来源文件。
