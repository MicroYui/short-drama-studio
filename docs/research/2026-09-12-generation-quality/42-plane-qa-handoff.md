# 纸面方向修复的实际结果与可选平面合成交接

日期：2026-09-12，Asia/Shanghai。当前项目仍是前置链路调试，不是新空项目完整验收。本节观察只读数据库及已有文件，没有追加 Provider 调用、改项目或控制运行。

## 三张真实修图及新 QA

项目 `project-5b8286dc63e6`，任务 `studio-job-20f086971171`。以下文件均在 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/assets/asset-4024e0462bfc/`，实际 1440×2560 PNG。

| 用途 | 旧图 → 新图 | 本地完成时间 | 独立查看所得 |
| --- | --- | --- | --- |
| 镜10取笔前 | `v3-asset-version-a8b4587fe910.png` → `v8-asset-version-0df9d98180d4.png` | 07:03:17.515 | 报价标题被移到右侧竖排，表格仍正立；说明文字改为竖列，字形没有随整页转90度，经办与复核沿画面上下分布。右手不再遮签线，但拇指紧贴笔杆，无法清楚证明尚未捏取；不能从静帧断言已经拿起。双空白状态、报价旧红改和确认姓名仍可见。 |
| 镜12转纸前 | `v3-asset-version-08a2aed2206b.png` → `v9-asset-version-5890dc977e57.png` | 07:03:35.743 | 纸形变横向、标题改右侧竖排，正文、表格和签栏仍朝镜头正读，属于重排而非完整纸面同转。左前臂仍从画面左下进入，未改为东侧/画右。经办空白、仅复核赵衡的单签状态保持，右手仍持裸笔，唯一独立帽仍在右下。 |
| 镜1推纸前 | `v3-asset-version-40e289075a43.png` → `v10-asset-version-9b291d582600.png` | 07:03:58.992 | 对坐、桌面纵深、左右手职责和两纸紧叠明显改善；两脸与原参考相容。说明标题仍在画上，未朝西；左手贴北边而非所需西边。放大可见笔端露出尖点，闭帽初态未满足。新图景别变宽、上方隔断及前景空桌较多。 |

镜10/12实际采用 `image.create_edit_guide`，分别使用 QA 同次给出的选区及方向建议，随后 `image.enqueue(operation=EDIT)`。源图排首位，独立 mask 与图示分开，图示职责为 `LAYOUT_GUIDE/GUIDE_ONLY`。镜10采用 `mask_feather_pixels=0`，镜12为4；两张均保存 PNG。对源/成图/实际 mask 逐像素比较，镜10遮罩外 **1,307,101** 像素与镜12遮罩外 **1,476,631** 像素均 **0变化**。这是圈外保留的真实结果，不代表圈内修改正确。

镜1 QA 已指出人物对坐也错，Worker没有机械沿用桌面小遮罩，而是使用房间母图及原身份、纸笔参考重新构图；因此没有圈外逐像素保留的承诺。三份实际 prompt 均明确要求整纸、全部印字和签名统一转向，禁止仅挪标题或镜像；本次不能归结为漏写一句方向提示，也不能把模型未遵循等同于本地遮罩代码失效。

第二次实际 `qa.inspect_asset_version` 均为各 Worker 的 turn13：

- 镜10，`work-order-cde9c3be49f54ba7b2d9baf262f40952`，07:03:44.470→07:07:36.279，约231.81秒，2.0，`recommended=false`。明确报混合重排版式及指杆接触含混，没有误断言笔已拿起。
- 镜12，`work-order-9830d5cadfc34b9ebaa95946d8c03198`，07:04:05.304→07:06:53.219，约167.92秒，2.0，`recommended=false`。明确报标题竖排而正文未同转、左臂仍从左下进入。
- 镜1，`work-order-4bc871f2fb124bfa889aab8f65014161`，07:04:27.547→07:08:07.502，约219.95秒，2.4，`recommended=false`。识别纸向、左掌接触边、露尖笔；还依据实际房间参考识别新增扶手椅，空白画面仅列次要构图问题。

上述 QA 结论与独立查看一致，未把这些修图当成合格结果。07:09:14只读快照：19个图像任务已落盘、`generation_tasks=0`、`provider_runs=0`；三个相关 Worker 已接到暂停。没有从静帧推断未来动作、书写稳定性或声音质量。

## 公开方法与本地采用点

Adobe 的 Vanishing Point 先以四角定义平面，再粘贴栅格化内容并按平面保持透视。这支持“把已有准确内容贴到可见平面”的方法，不能证明自动估计遮挡、光照或纸面一定准确。[Adobe 官方说明](https://helpx.adobe.com/photoshop/using/vanishing-point.html)

Pillow 的 `PerspectiveTransform` 使用从输出坐标到源坐标的透视映射。当前本地 renderer 用对应四角求同类映射，直接变换已有像素，不让图像模型重新排字。具体 renderer、几何与像素验证由 `image_plane_composite.py` 及其测试负责。[Pillow 官方文档](https://pillow.readthedocs.io/en/stable/reference/ImageTransform.html)

## 本次 QA 交接实现

`SemanticAssetReview.plane_composite_suggestion` 为可选的 `AssetImagePlaneCompositeRequest`，可直接交给 `image.composite_plane`。审图输入声明工具能力、实际基图尺寸及每张已发送参考能否作为本地合成源；源的精确版本、实际 EXIF 转正尺寸和整幅确定性字版标记随 `image_map` 提供。

运行时把建议基图固定为本次候选；来源只允许本次实际发送的同项目本地可用参考。DRAFT 与 APPROVED 均可用，不增加批准门。外部 URL 优先观察不能套到可能不同的本地缓存；跨项目参考可以保留为视觉证据，但不能凭当前项目的版本参数直接合成。失效或拒绝素材不成为合成建议来源。

摄影来源须给出可见平面的 `source_quad`，排除灰底、桌面等背景；整幅确定性字版允许省略，其他整幅屏幕/海报内容可显式给全图角点。源和目标角按物理角身份逐点对应，保留90度旋转；不各自按屏幕位置排序。建议与真实工具共用 `validate_plane_composite_geometry` 校验归一化和实际像素上的形状。非法建议、不可用来源或栅格退化只移除可选字段，不丢失原始得分、问题和推荐，也不增加语言调用。

`ASSET_VERIFIER` 方法与审图指令均说明：replace带入源图明暗与质感，multiply只适用于已看清的空白底；保护区域须来自真实前景手笔。目标外留有旧纸/旧墨、角被遮挡无法确定、手姿或机位错误时，不能用贴合伪装修复。建议可以为null，不建立每图必须合成或复审的流程。

Worker的 `asset_review` 当前工作集保留这一字段，后续读取及重开Store后继续交给Worker，精确源与角点不因摘要丢失。正常完整QA仍严格校验；可选字段不能使非法JSON或越界得分变合法。

## 验证与边界

专项19项覆盖实际 Registry→QA→Tool回执→本地 `image.composite_plane` 执行、90度角对应、来源能力/尺寸、DRAFT、本地非Provider尺寸、错误/隐藏/跨项目/远程源、EXIF转正、完整字版省略角点、实际像素退化，以及下一Worker请求与重开Store后的保留。所有语言模型均为本地Fake，没有发新Provider调用。

在 `backend/` 执行以下本地命令：

```sh
.venv/bin/python -m pytest tests/test_asset_plane_composite_suggestion.py tests/test_asset_edit_guide_suggestion.py tests/test_asset_inspection.py tests/test_asset_crop_suggestion.py tests/test_asset_inspection_spatial_context.py -q
.venv/bin/pyright short_drama/asset_inspection.py short_drama/production_manager.py tests/test_asset_plane_composite_suggestion.py tests/test_asset_inspection.py
```

结果：**67 passed / 6.08秒**，四文件 **Pyright 0 errors**。旧审图精确dict断言同步新增可用性字段及默认null，未放松原结论。Boyle已独立只读复核QA资格、坐标和Worker接线，无具体阻断；本代理另只读复核其renderer的四角原序、源/目标遮罩、前景保护、multiply与alpha语义，没有重复跑其像素测试。

收尾将长篇平面方法从每次调用指令合并到既有Verifier与typed字段说明；只有存在可用源时追加最小参数交接，无可用源只要求null且不追加回读。相同本地Fake审图输入的实际 `instructions` 用本地o200k计数：调整前有/无源均3555 tokens；调整后有源3202、无源3075，分别减少353/480。比较范围仅指令文本，schema与输入载荷没有裁剪；不是Provider实测用量或时延收益。

本次没有在真实调试项目执行平面合成，也没有把合成建议当成已合格图片。模型观察四角的准确度、摄影源光照与质感贴合、真实手纸遮挡和后续H3动态表现仍需实际媒体检验。

## 第十轮恢复：8张新增图片、实际检查与暂停交接

`f36dc3a` 加载后，同一调试任务于 **07:23:05** 恢复。以下时间均为2026-09-12北京时间，来自真实 `image_tasks.finished_at` 及各 Worker 的持久化 Tool 请求/结果。观察只用 SQLite `mode=ro`、`query_only` 与实际原图，不调用检查工具或 Provider。

文件根目录是 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/assets/`。本轮8张输出均为1440×2560 PNG；表内“QA结束”是本次 Tool 结果保存时间，不是独立人工结论。

| 实际用途及文件（相对上述目录） | 图片落盘 | 后续实际QA及独立观察 |
| --- | --- | --- |
| 镜14扣帽前：`asset-85d4f6cc7d52/v5-asset-version-08a44419dfc3.png` | 07:25:03.154 | QA 07:25:30.904→07:27:28.524，4.5／推荐。独立观察同意脸肩双手完整、右手裸笔、左指靠帽且帽仍横卧桌面、只有一笔一帽。顶部留白偏多，未阻挡用途。不能据此验证拾帽闭合、原落帽点跨镜连续性或画外纸叠。 |
| 镜10第一张无字底图：`asset-4024e0462bfc/v16-asset-version-3a6ff0ff57d2.png` | 07:26:13.794 | QA 07:26:29.779→07:29:15.990，2.4／不推荐。独立可见右手仍夹围笔尾，笔杆压在近纸内，远纸右下角斜切/不平。QA没有因无字拒绝，而是指出实际接触及平面问题，`plane_composite_suggestion=null`。 |
| 镜1第二次局部修图：`asset-4024e0462bfc/v15-asset-version-15e597f77971.png` | 07:26:37.135 | QA 07:27:07.814→07:30:16.149，3.1／不推荐。整页方向这次已正确：标题画左、双签线画右，字形随页侧转；闭帽笔、去扶手有改善。但左掌仍搭北侧长边，说明正文消失，许岚椅座有补面接缝。独立原像素纸区确认方向改善及正文缺失，未把它与上一轮竖排重写混为一谈。 |
| 镜4第一张无字底图：`asset-5f4d7485e6b8/v9-asset-version-f1536d9856db.png` | 07:31:05.358 | QA 07:31:27.223→07:34:06.037，3.0／不推荐。报价被正确清为空白中间载体；原像素局部仍见拇指与纸缘相切/遮住，没有连续木色间隙。QA准确指出此点及轻微纸角漂移，保留脸、右手、说明和背景可用结论，没有给贴合建议。 |
| 镜13收纸前底图：`asset-4024e0462bfc/v17-asset-version-f93c6507d35e.png` | 07:31:40.566 | QA 07:32:12.230→07:35:08.976，3.7／不推荐。独立可见两张横向空白纸南北分开，赵两手从画左进入，许在画右持裸笔且唯一帽在桌面。北纸西北角被赵指腹遮挡，不能声称已观察完整四角；QA因此不编造贴合参数。空白为明确中间意图，不是双签状态已完成。 |
| 镜10第二张无字底图：`asset-4024e0462bfc/v18-asset-version-bbcb4ca6819b.png` | 07:32:04.069 | QA 07:32:20.491→07:34:56.224，4.7／推荐，范围仅无字合成底图。独立可见右手展开、整笔移到纸外，双手与笔和纸明显分离；远纸角已平，两纸边界可辨。QA给出准确说明与报价源的两层整页合成建议，不声称纸面内容或动态签字完成。 |
| 镜1第三次局部修图：`asset-4024e0462bfc/v21-asset-version-933dd3c823a1.png` | 07:32:57.866 | QA 07:33:52.601→07:35:58.614，2.7／不推荐。坐垫接缝已改善，但左手仍从北侧压纸，标题也被擦淡，正文仍缺。独立查看与这两项新/旧缺陷一致；手姿和遮住的纸角尚不满足仅贴纸面的条件，建议仍为空。 |
| 镜4第二次拇指修图：`asset-5f4d7485e6b8/v12-asset-version-0714451cee50.png` | 07:36:55.765 | 暂停前最后落盘，**尚无本版产品QA**。独立原像素查看发现严重缺损：拇指/掌根成硬断面，仅余细条状皮肤；纸指分开不能代表姿态修复成功。本版不可当作已采用素材或QA结果，定位见下节。 |

镜14实际完成链也已核对：07:27:44.271 `artifact.review_version` 保存补充参考判断；07:28:02.430 将 `wi-yqq-s14-recap-start` 的 candidate/completed 指向该v5；07:28:16.509 保存参考包；07:28:52.692 `shot.save_current` 将精确图绑定到镜14的0–1.7秒，`image_reference_role=COMPOSITION`、`usage=GENERATION`，Shot修订为3。其说明明确不是硬首帧、不替换身份母图、不证明动作完成。这里如实记录 Agent 选择保存的 APPROVED 判断，不将该操作推广为DRAFT使用的前提。

镜4还对原v6做过一次有针对性的旧图QA，07:24:02.022→07:28:18.222。它保留已知身份/空间结论，将本机位的东西方向映射到可见纸边，只对报价及拇指给出编辑建议；部分说明纸角在画外，未要求编造或重做。这份建议真实进入后续 `image.create_edit_guide` 和 `image.enqueue(EDIT)`，说明建议交接已经执行，但生成内容仍须按上表判断。

镜10的 `image.composite_plane` 是 Worker turn25，于 **07:35:13.922** 保存为 `REQUESTED`。基图为v18 `bbcb4ca6819b`，两层源是本次已看过的双空白说明 `a9fb492dce6b` 和准确报价 `d99209b73470`；均显式给摄影纸面 `source_quad`，源TL/TR/BR/BL依次对应目标右上/右下/左下/左上，标题端整页朝东，采用 `multiply`。本次双手和整笔已在纸面外，`protected_regions=[]` 与可见图相符。事件7239明确：请求已持久化、尚未执行，`next_owner=WORKER_TOOL`；暂停接管发生在实际Tool开始之前，不能记成合成失败或合成成功。

07:36:56.579任务为 `paused`，增量8个图片任务均SUCCEEDED，事件末尾7250，`generation_tasks=0`、`provider_runs=0`。三张上一轮失败EDIT（`0df9d98180d4`、`5890dc977e57`、`9b291d582600`）未进入当前Shot精确引用、参考包或当前完成登记；历史候选和某资产current候选指针不等于采用。后续新候选尚未检查的部分，不能沿用旧QA或仅因文件成功落盘宣布可用。

## 镜4拇指缺损：实际mask、生成与拼接边界

失败候选 `0714451cee50` 的真实任务是 `image-task-30685281f69e`，07:34:59.984提交、07:36:55.765落盘。`parent/primary` 同为实际本地v9 `f1536d9856db`，图示为 `99df6a808ded`，独立mask为 `4cfd51730b5a`，`mask_feather_pixels=0`、PNG。真实请求只允许拇指末节及纸边编辑，并同时要求“其余四指、左掌、袖口不动”。

QA turn14的拇指九点polygon被Builder turn15原样采用：归一化外接范围约 `x=0.664–0.731, y=0.576–0.620`，在1440×2560图上约97×113像素。上界从 `(0.692,0.576)` 到 `(0.727,0.584)` 斜切过拇指根部；该处仍需要自然连接，却被分成可改末节和锁定掌根。箭头把指尖从约 `(0.673,0.609)` 移至 `(0.710,0.599)`，目标点在范围内不等于弯折和连接空间充分。

[原图、保存结果及实际可编辑mask的局部对照](../../../output/quality-e2e-2026-09-12/observations/s04-thumb-mask-diagnosis.png)。第三格红色只是本次离线查看叠层，不是新生产图示，未写入项目。成图硬断面与mask上沿重合，圈内原拇指被桌面替代而连接处残留条状皮肤。

本地只读像素比较：mask仅有alpha 0/255；**3,396,508个圈外像素0变化**，289,892个圈内像素中289,885个变化。`asset_workbench._apply_masked_composite` 按实际alpha选取圈内生成像素和圈外源像素，没有反转错语义或替错源。`_land_provider_output` 在staging文件内完成本地合成后搬到正式路径，原始Provider raster没有单独留存。因此可以确认**新圈内内容与锁定根部形成断面，现有选区和固定要求没有留下充分的姿态连接修复空间**；不能进一步断言Provider原图在圈外本来完整、或把全部缺损唯一归因于本地拼接。这里不是“只要扩大便必定成功”的证明。

### 公开方法与本次工程推断

Adobe公开文档说明：选区决定生成编辑作用范围，结果不合适时可以调整选区或提示；Content-Aware Move把选中区域移往目标，并修补原位置，结构与颜色融合需要分别考虑。这支持对移动部位、目标位置和背景恢复范围作联合判断，不支持只确认箭头端点就在mask里就认定姿态可修。[Adobe生成选区说明](https://helpx.adobe.com/photoshop/desktop/make-selections/refine-modify-selections/use-selections-for-generative-editing.html)、[Adobe Content-Aware Patch and Move](https://helpx.adobe.com/photoshop/using/content-aware-patch-move.html)

ByteDance Seedream 5.0 Pro公开介绍点选、套索、方框、涂画等空间编辑指示，并展示移除/添加对象及图层分离后的背景恢复；这是空间控制与结果融合的方法来源，并非本项目当前GPT-Image-2的效果保证。[ByteDance官方说明](https://seed.bytedance.com/en/blog/beyond-generation-it-understands-design-introducing-seedream-5-0-pro)

本次没有找到上述官方资料要求“所有手指移动必须包含整腕”的规则，也不猜测Kling或即梦的私有mask扩张实现。**需要纳入完整移动部位和必要连接关节**是结合本次根部断面、实际mask与姿态要求所得的局部工程判断；依实际结构决定相邻掌面、腕、袖口是否需要，不把固定范围当通用模板。

### 已授权的最小方法修复与验证

只在现有 `asset_verifier.md` 和 `asset_builder.md` 的局部编辑方法里补充：覆盖完整移动部位、必要连接关节、原位置、目标位置、背景恢复及接触阴影；已有小圈造成断面、连接失败或姿态未改变时，不重复同一过小圈，按实际可见结构调整连接区域。范围无法可靠界定则不强用局部mask。明确不固定每次整手/整腕、不自动扩大mask、不自动编辑，不改候选登记流程。`asset_inspection.py` 的实际QA指令只加两句简短提醒，完整方法保留在Verifier正文，没有新schema或额外调用。

在 `backend/` 运行：

```sh
.venv/bin/python -m pytest tests/test_production_skill_loader.py tests/test_production_method_skill_loader.py tests/test_asset_edit_guide_suggestion.py tests/test_asset_inspection.py tests/test_asset_inspection_spatial_context.py -q
.venv/bin/pyright short_drama/asset_inspection.py tests/test_asset_edit_guide_suggestion.py tests/test_production_skill_loader.py
```

结果：**76 passed / 4.70秒**，三文件 **Pyright 0 errors**。现有实际Registry→QA→后续Worker/重开Store测试同时检查方法进入真实语言请求，原始可选建议、单次语言调用及项目不变仍成立；Loader验证Builder/Verifier均实际加载该方法。它们验证接线及原合同没有回退，不能证明新提示已经在真实Provider上解决断指。当前v12仍是未审、不可当作采用结果的新候选；没有发新Provider请求、改运行DB或控制任务。

Arendt独立只读复核了三处实际注入文本：未发现新冲突；必要连接按实际姿态判断，不是固定整手/整腕，不自动扩大mask或增加QA。相关源码保持本次修复范围。

### 镜13的可见纸内平面：不机械要求物理纸角

再看v17的北纸原像素局部及报价源 `d99209b73470`，遮挡位于左上外缘，纸内中央、右侧和下部仍有连续可见的空白区域。当前 `image.composite_plane` 的四点合同和采样实现允许选择平面子区域：可在源图选择包含标题、完整表格、红色改价及确认签名的纸内区域，在基图选择完全可见且避开手的对应区域；不必补猜被挡的物理纸角。按整页朝向保留源角身份即可侧转内容，不能改成竖排重写。

这项只读判断不是合成结果：工具只采样选中的源区域，只修改目标区域，不会把子区域变换自动外推到整纸。必须保留全部必要文字、合理比例与留白；若目标与手相交，仍需按真实可见前景设置保护。摄影源在 `multiply` 下也可能加深原纸灰度，合成后的版式、质感和可读性尚待实际观察。该图没有因一个外角遮挡而必然重生的技术要求，也没有据此宣布本次QA推荐或采用。

[Adobe Vanishing Point](https://helpx.adobe.com/photoshop/using/vanishing-point.html) 区分平面定义和其内选择、粘贴操作，并未要求平面必须等于整张纸的外轮廓。上述纸内区域方案是结合当前图片及本地采样范围的工程推断。

随后按授权修正现有Verifier/Builder平面段、QA短指令和两处quad字段说明，以替换原句为主：可用可见共面子区域，不要求纸的物理四角全露出，必须包含本次完整必要内容且方向、比例适用；只采样source、只改target，不自动补整页，不猜隐角。正确的原纸空白留边可保留，只有目标外**需要移除**的旧字、旧纸或错误纸边才要求先修。渲染、几何校验、资格和状态行为未变，也未执行当前项目的合成或Provider请求。

在上述76项命令中再加入 `tests/test_asset_plane_composite_suggestion.py` 和 `tests/test_image_plane_composite.py`，最终 **106 passed / 7.70秒**。现有子区域像素测试验证源裁区不采入摄影背景、目标外原像素保留、90°角身份与前景保护；实际QA→Worker重启测试验证新说明进入请求且可选建议留存。对 `asset_inspection.py`、`image_plane_composite.py` 及三份改动测试运行Pyright：**0 errors**。这是现有工具使用合同的纠偏，不是s13合成质量已验证。

Arendt最终独立只读复核通过：源alpha先按source quad裁取，变换后覆盖范围由target quad减前景保护决定，与新说明一致；实际来源资格、源角省略限制和像素几何验证均未改变。未发现本切面具体阻断，未重复运行测试或操作项目。

## bde1b8b恢复后的实际合成与姿态结果

同一调试Job于 **2026-09-12 07:56:38.498** 恢复。本节采用SQLite `mode=ro`、`query_only`及本地原图；最终状态快照为 **08:02:20**。只编辑本42文档，未调用生产工具、Provider或控制任务。实际文件名仍是 `42-plane-qa-handoff.md`。

### 镜10：待执行的平面合成已落盘，产品QA仍不推荐

原Worker turn25 `image.composite_plane` 已执行成功：**07:56:46.258** 保存room v22 `asset-version-542bc4d28240`，**07:56:46.714** Tool返回SUCCEEDED。文件是 `asset-4024e0462bfc/v22-asset-version-542bc4d28240-plane.png`，1440×2560 PNG、5,613,544 bytes，`LOCAL_DERIVED / pillow-plane-composite-v1`；真实base为v18 `bbcb4ca6819b`，两层源仍是准确说明 `a9fb492dce6b` 和报价 `d99209b73470`，均为multiply。回执明确 `visual_acceptance=NOT_ASSESSED`，没有把合成成功写成已审；母Asset的current仍为原 `933dd3c823a1`，该操作没有自动替换current。

独立查看整图和两纸原像素裁区，并仅旋转局部预览以核文字：说明的标题、正文“本次报价出现错误，现补充核对。”、经办/复核及两条空白签线完整；报价的表格、原报价红划线、红字调整报价、确认赵衡完整。两页字形与全部版面一起旋转，标题端在画右东侧，不是逐字竖排重写。未见明显重影、双纸边、纸面侵入手笔或新增贴片框。真实base对比显示，两个target quad之外 **2,552,940个像素0变化**，双手、整支裸笔和唯一帽保留。首次独立查看未明确识别近纸比例疑点，此观察不能作为全面合格结论。

随后产品QA turn27于 **07:57:55.971** 开始，**08:01:11.733** 返回 **3.7／不推荐**：确认上述文字与整页方向、手笔分离、双空白状态正确，但指出近处说明纸形及版式比例不协调——标题端落在画右长边，近纸南北偏长；与所选摄影源和同桌远处报价比较，需修近纸实体轮廓，不能重复原四角合成解决。纸色略冷灰，未见旧字或双重纸边。该问题不是新增文字错误，也不能用首次独立“未见明显问题”抵消QA提出的比例疑点。`plane_composite_suggestion=null`，没有声称动态书写已验证。

turn26仅将v22登记为工作项candidate，未完成采用；08:01:53.583请求新EDIT、08:02:11.922创建 `image-task-be3a819abe12`，目标 `asset-version-e8ecd0a6912a`，修近纸形状，`mask_version_id=null`。快照时尚未有新输出；未验证这一新修图能否保留其他内容。

### 镜4：真实QA识别断指，连接区建议已交接

旧v12 `0714451cee50` 的本次QA于 **07:57:06.252** 请求、**07:59:24.236** 完成，**2.5／不推荐**。它明确指出：纸指间隙虽成立，拇指仍为斜切断面和薄片，不能等同自然收指；没有因空白报价中间态缺文字否决。脸、右手、说明、空间及平整无字纸面可保留，`plane_composite_suggestion=null`。

这次QA实际返回更宽的拇指/掌侧连接区域 `x≈0.675–0.765, y≈0.539–0.620`，包含断面、薄片、必要掌侧连接、目标及旧位置恢复。Builder turn20原样采用该polygon，于 **08:00:44.314** 保存guide `4c26e7817a35` 与mask `d63934bc6db7`。独立打开真实guide局部，框已向掌侧上移，包含上次断面周边连接，不再只有末节；图示本身不是修复成图。

turn21新EDIT以旧v12为parent/primary，使用上述mask、PNG、`mask_feather_pixels=5`，任务 `image-task-b6ce14c1b4e2`，目标 `asset-version-df45b22a7839`。截至08:02:20正在执行，**未有新图可供本轮判断**；不据新选区或旧QA宣布已修复。

### 镜1：接触方向改善，但新指尖与袖片缺陷落在实际遮罩边界

`image-task-91469cb1bce1` 于 **08:01:07.591** 保存room v25 `asset-version-1388b6ef4aa3`，文件 `asset-4024e0462bfc/v25-asset-version-1388b6ef4aa3.png`。独立查看整图及手袖局部：左掌确已移到画左短边附近，旧北侧接触区域腾开；但数个指尖被直线白纸面截断，左袖前臂出现尖条状布片残留。两脸、右手闭帽笔、房间及已修坐垫保留；本次明确不恢复文字，正文缺项仍待处理，不能因此把新手部破损忽略。turn32只登记candidate，turn33 QA于 **08:02:14.335** 开始，快照时仍IN_PROGRESS，尚无该版产品QA结论。

实际参数来源已逐点核对：Builder turn28的14点polygon与旧turn27 QA（07:33:52.601→07:35:58.614，检查v21）的建议完全相同，箭头start/end也相同，仅标签和描述改写。Builder使用了覆盖完整前臂、腕掌、旧位置和目标接触的文字要求，但没有因新方法加载而重新调整这一份旧几何。这不是新QA重新观察并选出的区域。

本次EDIT的parent/primary均为v21 `933dd3c823a1`，guide为 `d4730829f10d`，mask为 `0eb6852ff8a6`，PNG、`mask_feather_pixels=3`，尺寸均1440×2560。实际mask只有alpha 0/255，可编辑区域共54,839像素，外接像素范围 `(389,1180)–(721,1575)`。源、输出及真实边界叠看可见：

- 指尖白纸断口沿mask下边 `(0.5,0.597)→(0.407,0.600)`，新指体越过可编辑边缘后接回了原纸像素。
- 尖袖片沿mask右侧折线；圈内前臂移位，圈外旧袖像素仍被保留，形成不连续轮廓。
- mask外 **3,631,561个像素0变化**；圈内54,756像素发生变化。羽化只柔化圈内边缘，不能恢复区域外的旧袖背景或容纳越界的新指尖。

这是可证实的源/选区/保存输出不连续，不能据此断言Provider原图的完整手袖本来正确。其原始返回在staging内被 `_apply_masked_composite` 覆盖后移动到最终路径，当前staging副本不存在，本轮未取得合成前raster。最小判断是应按实际新旧轮廓重新界定本处必要连接、目标及背景恢复边界，不能原样复用已产生断口的几何；不推广成所有姿态都固定扩大到整手、整腕。本轮没有修改产品代码、实际mask或任务。

## 08:04:51暂停请求附近的三份新结果

以下观察仍是同一调试项目的真实静帧，不是新空项目完整E2E；只读检查本地整图、必要原像素局部及对应Worker结果，不发Provider或控制暂停。最后读取的QA于 **08:07:23.690** 落盘。

| 候选与实际文件 | 独立图像观察 | 实际产品QA与采用边界 |
| --- | --- | --- |
| 镜4赵衡v15 `df45b22a7839`，08:03:27.332落盘；`asset-5f4d7485e6b8/v15-asset-version-df45b22a7839.png` | 这次拇指有完整厚度、侧向指甲及关节褶皱，掌根过渡连续；上次直切口和尖薄残片消失。纸指之间仍有木色间隙。可见脸、其余四指、空着的右手、说明、房间保留；报价仍为本次有意保留的无字载体。 | turn25于08:06:23.732返回4.6／推荐，范围明确为“按纸前、待恢复报价内容”的中间底图，与独立观察一致。给出来自准确报价源的整套内容180°合成建议，尚未执行；不能记为完整镜头参考或动态按纸已实现。 |
| 镜10room v27 `e8ecd0a6912a`，08:04:35.423落盘；`asset-4024e0462bfc/v27-asset-version-e8ecd0a6912a.png` | 近纸改为横向空白纸，四角及单层边界可辨；原先较大的近纸区域恢复为桌面。双手、裸笔、帽、远报价目视保留，无明显新手部缺损或双重纸边。不能仅凭横向改善就宣布最终物理比例已验证；此图还没有说明文字。 | 暂停接管前turn31保存candidate仍为REQUESTED，尚无本版QA。新纸形与后续准确内容、版式比例仍待实际检查和合成；不沿用v22已否决的QA或将无字中间图算成完成。 |
| 镜13room v26 `71b7a63db253`，08:00:54.547本地合成落盘；`asset-4024e0462bfc/v26-asset-version-71b7a63db253-plane.png` | 南纸说明标题向西，双签与正文存在；北纸报价标题向东，表格、红改及确认存在。手纸关系与两纸分开状态保留。原像素局部清楚显示两纸内偏灰的四边形贴入区，白纸留边与内区有色阶接缝；表格细线断续成虚线，边缘还有点状亮线。 | turn33于08:03:32.522返回3.5／不推荐，具体报告纸内色阶接缝及细线采样损失，与独立观察一致。它没有把灰贴片误判为新增纸张，也未因正确相反纸向返工，修复范围限定纸面合成。 |

镜13随后只对原空白基图 `f93c6507d35e` 与两份实际白底字版补查：turn34于08:07:23.690返回4.4，确认可用纯白字版 `5464dab394be` 和 `974420be0033` 在空白底上multiply，避免摄影源灰白再次形成replace贴片。推荐范围仍是中间载体与合成路线，**没有新合成结果**。QA同时明确印刷姓名与摄影手写姓名的笔形、字号、留白不完全一致，不能登记成精读签名外观连续性已经通过。此限制应随来源交接保留。

### 最小制作方法建议：区别坐标复用、范围调整和边缘融合

本节仅提出方法，不修改Skill或产品源码。s01的实际14点坐标及箭头位置完全复用旧QA，即使新prompt写了“完整前臂、腕掌、旧位置和目标”，也不构成重新观察或重新划定几何。建议在现有局部编辑方法中补一个明确判断：**出现沿边断口、旧姿态残片或目标越界时，对照本次真实base、mask与输出检查新旧轮廓、必要连接和背景恢复范围；单改说明不算改选区。**如果旧几何仍适用可继续复用，不要求每次新增一次QA；需要改动时据可见结构更新相关坐标，不固定整手/整腕或统一扩张像素。

这一建议直接对应两组实际像素：s04旧小圈输出有根部断面，新连接区结果恢复连续；s01旧选区外3,631,561像素均保持原样，袖部旧轮廓与越界指尖无法被仅圈内生成纠正。羽化是融合边缘，不是给实际可编辑范围增加空间。本地当前实现将羽化结果再次乘原mask，因此圈外仍不变。

[Adobe生成选区说明](https://helpx.adobe.com/photoshop/desktop/make-selections/refine-modify-selections/use-selections-for-generative-editing.html) 把选区作为编辑位置约束，并要求看实际结果后按需要调整选区或prompt；这不表示改prompt就会改变持久化mask。[ComfyUI ImageCompositeMasked](https://docs.comfy.org/built-in-nodes/ImageCompositeMasked) 将destination、source、mask分开，体现已有图像与合成范围可独立处理；[GrowMask](https://docs.comfy.org/zh/built-in-nodes/GrowMask) 则显式扩展或收缩mask，并不等同羽化。两份节点文档标注AI生成，本次另核对[官方nodes_mask.py源码](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/master/comfy_extras/nodes_mask.py) 的mask合成、独立GrowMask和FeatherMask实现，不把文档描述当私有产品能力证明。

如果之后保存了实际Provider原图，且所需新区域在原图里真实可用，可先用它和明确调整的本地mask重合成，检查结果后再判断是否还需要生成；不能用不存在的原图承诺免调用恢复。当前已覆盖并移走的历史staging并无合成前副本，仍须标为未知。原图保存与重合成是另项能力工作，实现在doc45记录；本42不提前声明它已启用，也没有自行改现有运行素材。
