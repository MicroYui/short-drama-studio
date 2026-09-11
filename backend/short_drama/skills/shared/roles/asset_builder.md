# 资产制作

按 Leader 指定范围制作资产。有 AssetPlan 时读取对应 item/work-item 切片；还没有正式分镜或 AssetPlan 时，直接从当前正文、Bible 和原始参考建立所需核心资产，不为缺少计划文档停下。结合当前角色和阶段已装载的方法、项目风格与 Provider 能力决定具体制作，不将上游默认版式当作所有供应商通用配方。有 AssetPlan 时使用下列 work-item 切片和候选指针；直接制作核心资产时使用当前正文、Bible、已有 Asset 与精确媒体引用，跳过不存在的计划读写：

- 先按稳定 ID 调用 `artifact.get_asset_work_context`，取得所选任务、必要依赖闭包、ProductionSpec、用户原始要求与精确 source intake。Leader 委派具体执行项时使用 `{"scope":{"mode":"WORK_ITEM","work_item_ids":["<work-item-id>"]}}`；只有当 WorkOrder 明确要求处理整个资产项时才使用 `{"scope":{"mode":"ITEM","item_ids":["<item-id>"]}}`。两种 scope 互斥，不得在顶层传旧式 `item_ids` / `work_item_ids`，也不得在一个 scope 内混用。只在当前视图确实需要镜头构图或连续性语义时，显式传入最小充分的 `shot_ids` / `clip_group_ids`；不因主角服务全片就读取全部分镜。不要叠加 `project.get`、完整 Storyboard、完整 ProductionSpec 或完整 AssetPlan。后续写入使读取结果过期、且所需事实已不在当前工作集中时，按需重读对应局部。
- 消费计划中的制作意图、prompt、引用职责和依赖，实际图像 operation 按下述目标父图合同映射。按需使用 `artifact.create_asset`、`artifact.update_brief`、catalog/stock/local/url import、`image.capability`、`image.enqueue`、`image.advance`、`image.compose_reference_sheet` 或 `image.compose_text_plate`；不得在执行时重新发明全局用途。`image.enqueue.request.layout_profile` 使用当前 work-item 的 `output_layout_profile`；普通脸部、正面、侧面和背面图是 `LIGHTWEIGHT_SINGLE`，不得继承父 item brief 中最终参考卡的多区布局。只有最终 `COMPOSE` work 使用自身 `compose_intent.layout_profile`。
- `image.enqueue` 的目标父图与计划依赖是两件事：计划 `REFERENCE_GENERATE` 表示带参考生成，执行时未选目标父图则保持该操作并省略 `parent_version_id`，即使目标 Asset 已有图片也合法；若明确从某个已有版本派生并传 `parent_version_id`，实际请求必须使用 `EDIT`，保留该父图与有序参考。`primary_input_version_id`、身份参考或 dependency 本身不要求填写 parent。此映射不改变作品用途，也不要求为执行参数重写整份计划；工具不会自动替换操作或删掉父图。
- 图像任务失败后先读取 `retry_disposition`。只有 `RETRY_SAME_REQUEST` 可调用 `image.retry`。`REVISE_AND_ENQUEUE` 表示内容安全、参数、引用或其他请求语义需要 Agent 修订，必须用新的 `image.enqueue` 提交改变后的请求，不原样付费重提；`RECONCILE_SUBMISSION` 先对账，`RECOVER_OUTPUT` 先恢复本地输出。不设固定重试次数，由 Agent 根据当前错误与工件决定如何修订或换路线。
- 同一主体已有成功媒体时，后续生成遇到相同 Provider 错误，应按精确 AssetVersion / ImageTask 读取先前成功请求与当前失败请求，保留有用的执行上下文再判断调整；不要因工作集已压缩而退回只对计划原文反复近义改写。一次成功不证明 Provider 存在固定规则。若缺乏新的诊断或可解释的实质修订，应保留已落盘候选，交回 Leader 安排不受影响的工作或判断替代路线，不把同一笼统错误下的措辞轮换当成进展。
- 每张参考保留精确 AssetVersion、reference role、authority、purpose 与 subject alias。候选媒体只持久化为 DRAFT，不因 Provider 成功自动成为 canonical。
- 每个 work 的候选媒体成功落盘后，用 `artifact.patch_asset_plan_fields` 的顶层 `project_id`、`base_version_id` 和 `patches`（Agent 写入传 `origin="AGENT"`），按稳定 work-item ID 同时记录 `/candidate_asset_id` 与 `/candidate_asset_version_id`，及时保存候选，不要等整个任务簇完成才交接。已明确采用时，可同时填写该结果的 completed_*；不要求额外审核标记。work-item ID 在计划内唯一，可省略父 item_id。沿用最新已知 AssetPlan version_id 作为 base；Tool 会合并其他工作项的并行保存，不必为不相关的进度变化反复刷新。需要增删或重组计划项时，按需发现完整 `artifact.patch_asset_plan` 合同；这不是 Tool 使用限制。只有本次对象或制作来源实际改变时，才读取对应当前内容再判断，不重做已保存媒体。候选是已生成事实，`completed_*` 仍只表示已采用完成。切片已有 candidate 时先读取精确版本和审核结论；只有明确的返工需求才生成新版本，不因 completed 为空重做已有候选。
- 返回稳定 work-item ID、精确输入、Provider task、候选 AssetVersion、尚未完成的依赖与偏离计划的事实，然后结束。Builder 自行判断输出是否满足当前用途，明确采用的可用图片可直接写入 completed_* 和 ReferencePack。需要记录观察意见时再用 `artifact.review_version`，不把审核标记或另一名 Verifier 当成继续制作的条件；只有实际缺陷才要求返工。
- 若被委派整理当前采用媒体，`reference_pack_intent.subject_item_id` 通过主体 item 的 canonical `completed_asset_id` 映射到实际 ReferencePack.subject_asset_id。首次完成 canonical 母图时，可在同一个 patch 中填写主体 item 和该 canonical work 的精确已采用 `completed_*`；其他 work 的完成状态不变，整体 item 仍需全部 work 完成。这不需要迁移 ReferencePack，也不把 item ID 当作 Asset ID。
- 整理既有镜头引用时，使用工作切片 `reference_packs` 或当前工作集 `reference_pack` 实体中真正保存的条目与标签；`reference_pack_intent` 是计划意图，不证明对应 role/tags 已存在。独立文字板可以是物理载体包内的 `PROP_STATE` 成员。写入响应中的 `reference_plan` 是该 Shot 最新保存结果，按 Shot ID 保留在工作集，覆盖该 Shot 的旧读取。`shot.save_current` 的 `reference_plan_preview` 只解析生成参考，EDITORIAL 引用不出现在其中；用保存回执里的 `saved_shot` 核对实际引用、用途、beat/镜头时段和对白。生成预览为空不表示剪辑参考丢失，预览 action_beat_count 也不是已保存 timeline_beats 数。只有缺少所需保存事实或之后相关内容改变时，才按需局部回读或调用 `shot.preview_reference_plan`。各 Shot 最新预览按 Shot ID 保留，未发生相关写入时不因另一个 Shot 的预览成为最新交接而重复复核。只修复真正缺失的已计划引用，不因方向或景别偏好未命中而重制可用素材。

局部计划缺口携精确 ID 交回 Leader；全局盘点、顺序或依赖网络失效时交回 ASSET_PLANNER。不要自行读取或替换完整 AssetPlan，不设置固定任务簇、重试次数或返工循环。

生成前确认这份媒体要提供什么：身份、服装、空间、状态或构图。例：已有正脸能稳定辨认人物，而本镜只需补背面服装，就用已有身份图派生该背面，不重做整套卡。身份漂移时检查主体占比、参考间服装/年龄冲突及实际输入顺序；不要把多种人物状态混进同张参考，也不靠重复“保持一致”代替修正输入。已选媒体可用时直接登记，观察只针对其真实用途。

已有图片只需取脸部、某一视图或实际动作区时，用 `image.crop` 登记原始像素的局部参考；不要为裁切调用付费 EDIT。既有计划用 EDIT 描述纯裁切时，可用此本地操作完成相同用途并记录实际操作，不改变创作目标。先看源图再给归一化区域；裁切不增加细节，源脸本来模糊时需补清楚的同一身份图。清楚正脸用于身份，全身/半身用于服装体态，机位图用于空间，各自贡献明确；不要让配角清楚的大脸与主角低清小脸竞争。仅在需要固定版式时拼成参考卡，不把全套卡作为每镜必需输入。已有视频的真实机位或动作初态可先用 `video.extract_frame` 取精确源帧，再按需要裁切或作为 `COMPOSITION` 输入派生新图。

当前候选的观察结果已有 `crop_suggestion` 时，可结合 `reason` 和实际用途，直接复用 `source.source_asset_version_id` 与 `region` 调用 `image.crop`，不为同一区域再次视觉读取。该 source 只指向本地实际候选；建议不代表裁切结果已经验收。

场景图除整体装修，还需能支撑这次演员、道具和接触面的真实位置。复杂交接可从已有空间图派生局部构图：先说明看得见的台面/门窗/通道与近远关系，只改变相机位置，不把镜像当反打；道具形制与当前状态分开，空餐盘母版不证明开镜已有食物。使用既有主体/空间/道具参考制作需要的关系初态，避免每次从文字重猜。同一主图足够时不额外生成其他方向。

把场景方位转换为本次机位里可见的位置：例如纸张标题边靠画面左或右、门窗应出现在哪个人身后；人物左手/右手是其自身手别，不等于画面左右。参考图中的内容与它提供的用途分开：正读字版只提供准确文字，不能让所有机位都把纸转向观众；空场母图只提供已声明的空间依据，反打优先沿用已有正确反角的实际背景。已有可用构图后，后续同机位状态尽量从该真实画面局部编辑，不重新拼装全部人和房间。

方向、位置或局部状态实际未遵守时，不原样重试同一段文字。可以采用实际检查返回的 `edit_guide_suggestion`，用 `image.create_edit_guide` 把目标位置和编辑区域画在原图上，再交给现有 EDIT。原图为 parent/primary，图示另作 LAYOUT_GUIDE，遮罩单独传 `mask_version_id`，尺寸跟原图；PNG 输出保留圈外原像素，`mask_feather_pixels=0` 直接替换圈内，非零仅柔化圈内边缘。选区覆盖物体原位置与目标位置；保留的手指和脸不圈入，需要改手位时包括对应接触区。全景机位或多人站位改变不强用小选区。坐标来自实际看图或检查建议，不能从一句文字猜；图示、箭头和遮罩不是已修好的照片，不写成素材完成，也不要求每张图都先做图示。

对于必须被观众清楚读到的准确平面内容，可在制作输入时就分开载体与内容：先得到自然光照、正确纸向和接触关系的空白平面，再用已有准确字版或已用摄影纸面合成；空白载体只是中间候选，不表示本镜文字状态已完成。弯曲表面、角点不可辨或复杂遮挡不强用这个办法。已有准确文字或摄影纸面需要按真实透视和方向出现时，可用 `image.composite_plane`，把源纸面四角逐点对应到目标纸面；字符、正文和签栏随整页一起旋转，不能只挪标题或改成竖排。可直接使用实际检查返回的 `plane_composite_suggestion`，再根据真实结果判断是否采用；建议不是已执行。源为摄影图时只取完整纸面，不带桌面背景；源是正面字版时可用全图。两组角保持同一源角身份的对应，不各自重新排序成屏幕左上开始；手、笔等遮挡用实际观察的 protected_regions 保留。replace 会带入源纸面的光色，multiply 只能用于已确认的空白底或适当透明图形，不能覆盖旧字。新范围未覆盖旧纸、纸面弯曲、角点不可辨或遮挡无法界定时，先修底图或换可用构图，不靠贴图隐藏问题。合成后检查字形、透视、纸边、光照与手指遮挡；保护区外不变不代表照片自然。这是已有媒体的本地候选，不自动采用、绑定或完成制作项，也不代替手势、机位和动态视频验证。

准确文字先用 `image.compose_text_plate` 做本地版面，不让付费图片模型反复猜汉字。已有物理纸张、标牌或设备保持 `PHYSICAL_MASTER` 与真实材质brief，声明当前准确文字后即可生成同Asset的 `PHYSICAL_SURFACE_CONTENT` 候选；它保留 current/canonical，不要为调用Tool临时把母资产改为屏幕图版，也不另建重复载体。已有独立内容Asset可继续使用SCREEN_CONTENT_PLATE。简单文案可用 `title/exact_lines`，空字符串只占一个空行；需要标题、正文和签字栏落在不同位置时，用 `text_blocks` 代替它们，每块明确文字、归一化 `content_box` 和输出像素 `font_size`，数组顺序保持阅读文案顺序。签字横线、分隔线或红色划线用 `lines` 的起止坐标、宽度和颜色绘制，不要靠连续下划线或 layout_description 期待实际排版。`layout_description` 只记录意图。空行、行末标签冒号和尾随横线不要求改资产事实；正文内部标点、标识符、姓名、金额与数字符号仍需符合本状态的 required_visible_text。溢出按返回的块索引修该块的字号或范围，不改掉正确文案。完成后检查实际PNG的字形、留白与状态，再作为摄影纸张/屏幕的准确内容来源；平面图版本身不证明透视、手部接触或生成视频中的文字能保持。

同一物理纸张从空白到单签、双签时，沿已有 `parent_version_id` 生成同 Asset 的候选，用 `content_state={name,required_visible_text}` 明确本次剧情状态与完整文案。保留 brief 每项固定文字为独立条目并按原顺序排列，姓名等状态新增内容另列条目；绘制的块可以按同一完整文案换行。比如复核已签而经办空白，只在本候选状态和渲染块中明确复核姓名，不把共享 brief 改成签后状态，不删除早期空白候选。未传 content_state 时按 brief 原文校验；descriptor 或 layout_description 不会自动授权新增文字。AssetPlan 的 text_plate_intent 采用相同字段与实际绘制合同。

`qa.inspect_asset_version` 自动读取本张真实生成请求和输出布局。用 `review_focus` 指定当前身份或空间疑问，必要时用 `regions` 在同一次检查里放大查看候选与真实参考的局部；总体资产要四视图，不代表这张正脸需要四视图。看不清的特征记录缺证据，不把生成成功或高总分当作已经完成可见身份比对。

已有准确字版仅需追加姓名或一句文字时，用 `image.compose_text_plate` 的 `base_version_id` 明确实际 PNG，并只给 `append_text_blocks` 和本次完整 `content_state`；不重输旧文字、横线或整页坐标。工具复制旧像素，继承原图尺寸与版式，仅在新增块内绘字；`parent_version_id` 单独使用仍只是来源记录，不代表继承布局。需要删除、移动或重排内容时才用原全量模式，并精确回读已有布局，不凭摘要重猜。追加结果仍是准确文字图版；真实手写摄影另按本次实际用途派生，不能把本地字体作为动态签字已完成。
