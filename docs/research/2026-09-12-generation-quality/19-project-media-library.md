# 项目资产的具体文件与剧情状态

日期：2026-09-12（Asia/Shanghai）。这是前置 debug 中实际桌面观察发现的信息丢失修复；不是新空项目端到端验收。

## 实际问题与产品边界

根代理在项目资产打开“情况说明”时只能看到当前 1 份素材；真实可用的双空白、单签字版与摄影状态仍存在于同一个 Asset 的文件记录，却因未被 current/approved/正文/参考包引用而被资产页隐藏。`mediaLabel` 又按 `｜` 拆开后只取第一段，并截断句子与 64 字，单签文件变成“情况说明”，详情也复用了这个短标签。

[产品页面方案 8.1](../../product-interface-spec.md#81-一份项目资产清单看清已有内容与缺项) 与 [PRD 4.7](../../product-prd-and-roadmap.md#47-项目资产与配套入口) 明确要求同处的核心资产与所有媒体两种查看方式：人物、场景、道具是对象；不同视图、剧情状态、声音与源视频是具体文件。这里补齐既定入口，不提供历史版本、恢复、审批或新增采用流程。

## 采用的公开方法

Adobe Premiere 的 Project 面板用 [bins 组织项目文件](https://helpx.adobe.com/premiere/desktop/organize-media/file-organization/add-and-delete-bins.html)，与成片里的使用关系分开。Adobe Photoshop [Layer Comps](https://helpx.adobe.com/photoshop/using/layer-comps.html) 用明确名称区分同一作品的不同外观/布局，并支持导出独立文件。本地采用点是“对象关系不丢、具体状态有完整名称、每个实际文件能预览”，没有复制这两个产品的版本或编辑机制，也不声称知道 MiniMax Design 的私有媒体库实现。

## 实际实现

- `StudioAssets` 同页提供“核心资产 / 所有媒体”，媒体可按图片、视频、音频和完整说明筛选。对象封面仍优先当前素材；详情展开同对象全部具体文件，包括没有当前绑定的 DRAFT。
- 清单从 `ProjectDocument.assets[].versions`、实际 `Take` 和已经保存的 editorial 媒体投影生成。文字设定不进入文件统计；没有地址或已知为空的媒体记录显示待补。生成组源视频只展示一份文件，并显示覆盖镜头与实际源时窗。
- 所有媒体按媒体类型与本地路径（无本地路径时按外部地址）合并同一物理文件卡片，保留首条明确操作目标与全部关联对象说明；核心资产内的具体引用身份不被合并。已有 AssetVersion 与 Take 指向同一文件时补上实际视频来源与覆盖关系。同一导入视频产生多条 Take 时，保留首条预览对应的精确 Take/镜头，其他 Take 保留为来源与覆盖关联，使用状态合并而不被后来的未采用记录清空。不同剧情状态的不同文件不合并。
- 完整素材说明保留标点、换行和 `｜` 后的剧情状态。仅列表标签移除旧 `wi-...` 前缀，详情显示原说明，文字图版还显示已声明 `content_state.name`。
- 候选、已拒绝、来源失效等记录状态与读取状态分别显示。没有实际加载成功前标“尚未验证读取”，加载失败标“无法读取文件”并从正常文件计数排除。成功预览不表示内容 QA 通过；DRAFT 也不形成等待批准的门槛。
- 浏览、切换预览及关闭详情只改 React 本地选择；不会调用采用或内容保存接口。明确的“导入替换这份素材”继续把所选 Asset/AssetVersion 传给原导入接口，原有引用更新语义不变。
- `StudioScope` 增加可选 `asset_version_id` / `take_id`。给 Agent 的可见文案仅包含名称与具体状态，精确文件 ID 通过范围字段传递。现有 Runner 已把完整 scope 附入首次目标和后续消息，未新增路由或自动提交动作。

## 本地检查与剩余边界

`node scripts/test-studio-media-library.mjs`：8 项通过，覆盖完整状态文案、旧选择器兼容、三种 DRAFT 状态、缺失/空文件/拒绝/失效、生成组源文件与 AssetVersion 合并、真实 takeIndex 的剪辑使用关系、共享路径去重和无内容修改、自然文案与精确 scope，以及同一路径两条 Take 的标题/精确交接/采用状态一致性。

`cd backend && .venv/bin/python -m pytest tests/test_studio_media_scope.py tests/test_studio_editor_selection.py -q --tb=short`：7 项通过 / 11.48 秒。验证首次实际 Runner 语言输入、同资产不同文件的后续消息、重新读取已保存 Job/重启续接，以及项目内容完全不变；使用已有 Fake language client，不调用 Provider。修改的 Python 模型与新测试 Pyright 为 0 errors，前端 TypeScript 检查通过。

根任务在实际 Electron 中已看到 6 个对象 / 10 份媒体，查看空白与单签摄影的完整详情、尺寸、切换预览与自然修改文案；前后所有 assets 逐值不变，情况说明 current 仍指向单签摄影。这里只是素材浏览交互验收，未提交 Agent 修改或付费生成。

本次 UI 的读取状态依赖浏览器/Electron 对真实文件的加载，未在前端进行全库磁盘扫描；保存的尺寸/时长不是新 ffprobe 结果。当前集之外未加载的独立 episode 数据仍由现有项目 API 的范围决定。最终重载后的 Electron 复核及构建由根任务合并检查，未主动操作正在暂停的 debug Job。
