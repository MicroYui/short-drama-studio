# 官方角色卡布局参考的本地映射

MiniMax Design `drama-series` 原工作流要求角色设定图优先把官方双区三视图布局模板放在参考图 1。当前 Harness 不使用私有 `ref_capsules`，而把同一事实映射为版本化 Asset/AssetVersion：

- 模板国内 URL：`https://cdn.hailuoai.com/public_assets/opencode/character-sheet-layouts/drama-character-sheet-three-view-20260701.png`
- 当角色采用 `DRAMA_CHARACTER_DUAL_ZONE_16_9` 的“一图成卡”路线且项目尚无该模板时，不得只在计划中备注“没有模板”就继续。先建立一个 `REFERENCE` Asset，用 `artifact.import_url_version` 登记上述 URL，并在实际可读后将该精确版本记录为布局参考。随后在 AssetPlan 的角色卡 work item 中把它放在 `ordered_references` 第一项，声明 `reference_role=LAYOUT_GUIDE`、`reference_authority=GUIDE_ONLY`，purpose 只允许贡献 16:9 双区位置和版式；该 work item 因真实带图而使用 `REFERENCE_GENERATE`，不冒充纯文本 `GENERATE`。
- 用户身份、选角或服饰图排在模板之后。用户指定的人物图使用 `SUBJECT_IDENTITY/AUTHORITATIVE`；模板人物永远不得贡献脸、年龄、体型、服饰或风格。
- 只有模板版本确实会传给 Provider 时，最终 Prompt 才能写“图1只作位置/布局参考”。如果 CDN 不可读取或没有实际 AssetVersion，完全省略模板 reference 并重新编号，不得指向不存在的图片，也不得仍把无参考 `GENERATE` 称为官方标准一图成卡。Agent 根据真实用途改走下方确定性拼板路线，或明确选择不依赖该标准布局的其他资产形态。不围绕不可读的 CDN 或概率生成的微小版式偏差反复重试。
- 该模板只用于角色设定卡。场景四向卡和单物道具卡只使用原工作流中的文字布局，不复用角色模板。
- 不把远程模板二进制提交进本仓库；项目中只保存 URL、用途与运行时版本引用。

## 两条可执行的版式路线

### 路线 A：布局 guide 引导的一图成卡

- 适用于下游需要一张紧凑身份卡，但不依赖固定像素坐标裁切的情况。必须传入已登记的官方 layout guide，让 Provider 理解“左边大脸，右边正/侧/背三列全身”的结构；无真实 guide 时就选择另一条路线。
- 执行新计划、旧版 AssetPlan 或中断恢复时都重新语义核对当前 work item：只要它仍声称产出官方标准一图卡，`ordered_references` 就应存在精确 `LAYOUT_GUIDE/GUIDE_ONLY` AssetVersion，且执行时转成真实 `reference_inputs`。缺失时先登记模板并用 `artifact.patch_asset_plan` 补全该 work item，再继续未提交的图像工作；不因计划已持久化就继承过时输入。已经提交的历史任务保留真实输入证据，不事后声称它使用了 guide。
- 审图关心这张卡是否实际提供同一人的可读大脸、完整的正面/侧面/背面、稳定服饰与干净中性背景。近似分区比例、轻微的中性背景明暗或色差可以记为非阻断观察，但不得在没有影响身份、视图可读性或后续使用的证据时，单独据此拒绝候选。
- 侧面列默认的职责是让模型理解人物轮廓，而不是强制某一侧。只有左右存在可识别的非对称身份特征（例如单侧疤痕、耳饰、发型或服饰结构），或 Shot/构图/下游裁切明确需要某一侧时，左侧面与右侧面的区别才是阻断问题。不仅因 `requested_views` 中使用了 LEFT/RIGHT 枚举名就推导这种实质需求；还要读取其 rationale、身份锁和服务的镜头。
- 官方原文的“左 50% / 右 50%”在这条概率生成路线中表达版式意图和视觉重心，不扮演像素级测量门禁。若分区偏差已导致脸过小、全身裁切、视图混叠或无法用作紧凑参考，才是实质制作问题。

### 路线 B：分视图生成/导入后本地确定性拼板

- 适用于下游要按固定区域裁切、要求准确 16:9 双区几何、需要统一背景色，或者项目已有经选定的单视图版本时。
- 分别准备并观察四个精确输入：`CANONICAL_MASTER` 槽的大幅正脸近景、`CHARACTER_FRONT`、一个已选定方向的侧面全身，以及 `CHARACTER_BACK`。每张图自身先满足它的语义视图职责，且四张是同一身份、发型、服饰和比例。
- 对这四个已选定的精确 AssetVersion 调用 `image.compose_reference_sheet`，显式传入 `layout_profile=DRAMA_CHARACTER_DUAL_ZONE_16_9`、`show_labels=false`、`background_color=#7c7c7c` 和所需 `target_size`。Tool 按语义槽定序，在本地给出精确几何和版本系谱；输出仍是 DRAFT，需再观察是否因裁切、缩放或输入不一致而影响制作用途。
- 当问题只是一图成卡的分区线不够准确或背景有轻微渐变，但身份和视图已可用时，Agent 根据下游用途决定直接采用路线 A，或为精确版式切换到路线 B；不把微小几何偏差转化为默认的 Provider 付费返修循环。

## 定向修复保留已有身份

- 已有候选图能稳定提供人脸、年龄感、发型、体型或服饰身份，但需要修正视图、裁切、排版或局部状态时，这是定向修复，不是重抽新角色。对同一 Asset 优先用 `EDIT`，把当前可用版本作为 `parent_version_id` 和精确 identity `reference_input`；对尚未建立目标 Asset 但有外部权威身份图的情况，使用 `REFERENCE_GENERATE`。
- 无参考 `GENERATE` 只用于从零建立身份，或 Agent 基于当前语义明确决定放弃原身份候选并记录理由。不得把“保持原人物，只修某一项”的定向修复写成无引用 `GENERATE`。

## 重复载体的物理母版与文字分层

- 当多个同规格小道具、标牌、屏幕外壳或文件载体只因后续确定性文字不同而区分时，先建立一件可干净裁用的无字物理母版。各使用处引用同一精确版本，由后续本地裁切、复制或组装负责重复出现，由独立 `TEXT_PLATE` 负责各自文案。
- 不把“一次生成多件像素级一致的物件”作为图像 Provider 的默认职责。如果已有候选中的单件边缘完整、能独立裁用，它们之间的轻微尺寸或透视差不是全量返修理由。只有结构、材质、组件或可裁用性已无法完成实际下游职责时，才由 Agent 决定定向修复。
