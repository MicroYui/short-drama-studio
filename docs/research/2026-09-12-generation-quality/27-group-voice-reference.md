# 默认生成组输入保留当前角色声线

2026-09-12。本轮修复组合准备入口遗漏既有有效声音绑定的问题，仅在临时项目及本地PNG/WAV上使用Fake语言客户端；未调用真实语言、图像、视频或官方Context IR Provider，未读写本轮运行项目数据库。当前《一起签》没有角色声线绑定，不会因本次代码修改自动新增音频。

## 已复现的接线缺口

`ProjectDocument.voice_references` 仍是有效的角色→声音资产选择，`update_character_voice_reference` 可以建立、更换或清除；绑定没有另一份版本状态，实际版本由声音资产当前选择决定。旧单镜路线按 `ScriptLine.speaker_character_id` 读取本镜真实说话人的绑定。组准备默认只收集镜头/ReferencePlan中的已选引用，没有补入这些声音，因此相同正式绑定在组路径中被静默遗漏。

修复前的有界本地重放确认：有效绑定仍存在，旧单镜参考路线包含1条音频，默认组编译及保存的路线均含0条音频，走DIRECT且没有声线遗漏提示；手工显式传音频则能进入组路线。重放只使用Fake语言，0个媒体任务。原失败断言保留在 `output/quality-e2e-2026-09-12/reproduce_group_voice_reference.py`，它标注为修复前脚本；当前回归见下文。原先音频单独输入的Fake成功不代表H3支持这种输入。

这不是“每部片都必须有声音参考”的要求。没有绑定时仍使用既有原生音频能力；角色名字出现在描述中，也不能替代结构化的说话人归属来引入全局声音。

## 官方依据和适用边界

[MiniMax官方H3视频指南](https://raw.githubusercontent.com/MiniMax-AI/cli/main/skill/h3-video/references/h3-video.md)要求为实际参考媒体说明用途，并区分提供音频时的声音/画面同步。参考音频需同时有至少一张参考图或参考视频；最多3份音频，每份2–15秒、合计不超过15秒，所有参考文件合计不超过12个。这是模型/CLI公开规则，不能写成当前账户的实测结论。

[AceDataCloud当前视频API文档](https://raw.githubusercontent.com/AceDataCloud/MiniMaxAPI/main/docs/videos.md)提供 `content` 中的 `audio_url` 与 `role=reference_audio`，当前适配器继续使用该公开接口；首尾帧模式不能与参考媒体混合。其文档没有单独重述“音频必须伴随视觉”的条件，因此本轮将MiniMax公开模型约束放到已有输入校验，未伪造供应商账户验证。该端点当前公开时长范围是4–15秒。

本地 `角色→<Audio N>` 是应用根据实际有序输入构造的明确映射，不意味着供应商会自动读取项目的全局声音绑定。声线参考用于声音身份和质感，实际说词仍来自保存的ScriptLine；不要求照读参考录音。MiniMax Design、即梦、Kling的私有内部声线选择实现未知，本次没有推测其内部协议。

## 最小实现

- `h3_route_planner.generation_unit_voice_reference_inputs` 在当前组顺序中读取实际对白的typed说话人，按首次出现顺序补入其绑定；同一人物只补一次。不包含其他组才发声的人物，也不凭展示名字猜归属。已有镜头AUDIO引用若明确绑定到同一人物资产，优先于该人物的项目默认声线。
- `prepare_h3_group` 只在 `media_inputs` 省略时使用上述补充。显式列表（包括空列表）保持调用者选择，不能又偷偷加回全局音频。绑定版本损坏、拒绝、失效、缺文件或类型不匹配会报告人物/资产与具体原因；不自动清除绑定、不换回native、不擅选视觉。明确音频却无参考图/视频时，在语言编译前返回具体缺视觉错误。
- 共享 `resolve_current_asset_media` 的AUDIO分支接受当前选择、CURRENT且DRAFT/APPROVED的音频；检查实际本地文件及已知subject。当前DRAFT优先于旧APPROVED，旧未选DRAFT不能混进输入。无current指针的旧记录才兼容approved指针。IMAGE和VIDEO规则未改。旧单镜声线解析也同步使用当前音频版本，避免新旧入口优先级冲突。
- 解析后的同一媒体列表进入缓存文件状态、语言/可选Context IR编译输入、保存Route和原AceData请求序列化；没有第二套声线真相。`<Audio 1>`/`<Audio 2>`沿用既有有序媒体绑定函数，与人物及原句保持关联。
- 独立复核补出一个具体非法输入：通过普通导入将当前声线换成1秒WAV，旧绑定仍有效，而原共享校验没有使用已知时长。现有 `minimax_h3_compiler` 增加纯 `validate_h3_audio_durations`，由默认组补充、service最终媒体解析及Worker提交前复查共用。已知单条小于2或大于15秒时报告人物/资产/版本及实际秒数；已知合计超过15秒也明确失败，不能悄悄丢掉后续参考。没有metadata的输入保持未知，不当成已验证的0秒，不新增ffprobe或模型调用；没有扩建音频分析机制。
- 组时长在实际准备入口检查，报出组ID、实际秒数、4–15秒限制及 `shot.patch_storyboard` 局部调整路径。不添加创作保存门、不自动拆组；时长16秒的分镜仍可正常保存，视频准备给出可操作反馈。

所有失败都发生在真实视频提交之前。本轮不改变Provider接口、原生声音默认、声线管理API或既有生成组恢复方式，也没有增加强制声线制作步骤。

## 本地验收

新 `backend/tests/test_h3_group_voice_references.py` 使用真实本地图片/音频文件，覆盖：当前DRAFT覆盖旧APPROVED；旧未选DRAFT和未知subject拒绝；只包含本组真实发声角色；不同角色跨组范围；两位说话人的实际编译和保存Route的有序AUDIO；当前镜头替换、显式列表及显式清空优先；无关角色坏绑定不阻挡；明确坏绑定不静默忽略；有音频无视觉在模型前失败；实际导入1/16秒WAV后默认与显式输入拒绝；未知时长保持未知；已保存Worker任务提交前复查；音频合计时长及16秒组反馈。

完整组合回归经真实ToolRegistry、语言编译、保存Route及请求序列化；Fake调用仅一次，已保存operation恢复复用该prompt；2K/9:16保持，媒体任务始终为0。旧音频only Fake夹具改为带实际PNG，保留人物映射、同名误导及合计时长的原验证目的。

验收结果：新专测17项通过（6.39秒）；10个相关文件联合通过160项，新增Worker测试的夹具遗漏已选图引用，修正夹具后17项专测全绿，共覆盖161个不同用例。未因旧断言恢复音频only或旧APPROVED优先规则。相关文件为 `test_h3_group_voice_references.py`、`test_voice_reference.py`、`test_h3_group_operation.py`、`test_h3_subject_reference_truth.py`、`test_h3_route_planner.py`、`test_h3_context_ir.py`、`test_h3_cross_shot_dialogue.py`、`test_short_drama_h3.py`、`test_official_minimax_h3_skill_integration.py`、`test_h3_generation_unit_worker.py`；命令是在 `backend` 运行 `.venv/bin/python -m pytest`。8个所改Python文件Pyright无错误、无警告，限定文件 `git diff --check` 通过。独立只读复核覆盖默认/显式选择、DRAFT当前优先、人物与编号、时长共享校验和Worker复查，未发现剩余具体接线缺陷。

以上证明输入选择、顺序和持久交接，不证明音色、口型或声音质量。真实视频阶段仍需对实际音轨确认说话人、声线、原句、节奏与跨切连续性；不能用静态画面检查代替听审。
