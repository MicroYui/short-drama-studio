# Phase 11：可选角色声线输入

## 目标与范围

Phase 11 只解决一件事：用户可以为角色上传或用麦克风录制一段参考人声，系统在适合的对白镜头中把它作为 MiniMax-H3 `reference_audio` 输入。用户不提供时，整条 `AUTO_DRAFT` 仍使用 H3 原生声音并正常继续。

本阶段**不新增声音 QA**。Phase 9 已有的 `AudioInspection` 保持不变；Phase 10 暴露的“设备播放被画成角色本人说话”仍是已知声音来源语义缺口，但按用户决定不在本阶段实现音视频联合判定，也不增加录音笔、画内/画外或设备播放专用领域字段。

## H3 契约

官方 H3 Reference 模式支持图片、视频与音频参考；音频支持 WAV/MP3，单段 2–15 秒、单文件不超过 15 MB，同一请求最多 3 段且合计不超过 15 秒。Reference 与首帧/首尾帧模式互斥，因此系统不能在一个请求里同时塞入关键帧控制和角色声线参考。

实现采用以下普通产品规则：

- 上传接受 WAV、MP3、M4A、AAC、FLAC、OGG 与 WEBM；
- 后端统一转为 48 kHz、单声道 PCM WAV，再检查 2–15 秒与 15 MB 上限；
- 录音入口最长录制 14 秒，给容器收尾留出余量；
- 参考音频只用于角色音色、声线和说话方式，不复制样本中的原台词、环境声、音乐或节拍；
- 一个镜头最多绑定 3 名实际有台词的角色；未命中角色绑定时保留 H3 原生声音。

参考：[MiniMax Video Generation API](https://platform.minimax.io/docs/guides/video-generation)、[MiniMax-H3 Full-reference Prompt Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)。

## 领域与接口

角色声线不写入不可变的 `ProjectBibleVersion`，而是项目级轻量绑定：

- `VoiceReferenceBinding.character_id` 指向当前 ProjectBible 角色；
- `VoiceReferenceBinding.asset_id` 指向一个 `REFERENCE` 资产；
- 资产的正式版本必须是可用的 `AUDIO`；
- `ProjectRecord.voice_reference_revision` 在绑定、替换或解绑时递增。

产品 API：

- `POST /projects/{project_id}/characters/{character_id}/voice-reference/upload`：上传或录音文件，自动规范化、批准并绑定；
- `PUT /projects/{project_id}/characters/{character_id}/voice-reference`：绑定已有已批准音频资产，或传 `null` 解绑。

声线版本变化会使旧 H3 Route Plan 失效；旧任务产物也不会再作为当前版本 Take 复用。固定前期制片包的独立重复会复制仍可用的角色声线资产与绑定。

## 自动路由

Route Planner 根据当前镜头的锁定台词解析实际说话角色。若该角色有声线绑定，则建立带 `reference_audio` 的 Reference 候选，并把角色与 `<Audio N>` 的用途写入 H3 prompt。确定性排序和 GPT-5.6-sol Route Agent 都会优先考虑该候选，但这不是硬锁：当已应用的 Director 修复明确需要首帧/首尾帧控制时，系统可以为画面质量选择互斥的 Frame 路线，本镜头不使用声线输入。

这保持了用户要求的自动化与可忽略建议：提供声线后无需逐镜操作；不提供、解绑或某镜头没有该角色台词时，也不会阻断生成。

## 桌面端

资产工作台新增“角色声线参考（可选）”：

- 为当前 ProjectBible 的每个角色显示绑定状态；
- 上传本地音频；
- 直接请求麦克风权限并录制；
- 试听已绑定版本；
- 复用已有已批准音频资产；
- 一键解绑并恢复 H3 原生声音。

macOS 打包配置包含麦克风用途说明与音频输入 entitlement；应用只在用户点击录制时请求系统麦克风权限。

普通音频和视频 AssetVersion 也可以在资产预览区直接播放。

## 验证边界

离线测试覆盖上传规范化、自动批准与绑定、过短音频拒绝、解绑、对白镜头自动选择 `reference_audio`、无对白镜头原生声音回退，以及声线更新导致旧 Route Plan 失效。未运行新的付费 H3 质量探针，因此“参考音频能否在不同角色、语速和跨项目条件下稳定迁移声线”仍是 `UNKNOWN`。

工程验证：

- 后端全量 `677 passed in 110.27s`；
- Pyright 与 TypeScript 均为 0 错误；
- OpenAPI JSON 与 TypeScript 类型重新生成；
- Vite 前端、Electron main 与 preload 生产构建通过；
- macOS entitlement plist 与 Electron Builder YAML 解析通过。
