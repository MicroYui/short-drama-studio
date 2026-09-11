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
