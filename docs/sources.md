# 来源与源码快照

快照日期：2026-08-17。本清单优先记录官方资料和固定源码 commit。产品能力、模型接口、价格与仓库代码都会变化，进入实现前应重新核验。

## 1. 模型与 API 官方资料

### OpenAI

- [GPT-5.6-sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [GPT Image 2 model](https://developers.openai.com/api/docs/models/gpt-image-2)
- [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)

### Azure OpenAI Image

- [Azure OpenAI 图像生成模型](https://learn.microsoft.com/zh-cn/azure/ai-foundry/openai/dall-e-quickstart?view=foundry-classic)

### MiniMax

- [Video Generation Guide](https://platform.minimax.io/docs/guides/video-generation)
- [Create Video Generation Task V2](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)
- [Query Video Generation Task V2](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)
- [List Video Generation Tasks V2](https://platform.minimax.io/docs/api-reference/video-generation-v2-list)
- [Cancel or Delete Video Generation Task V2](https://platform.minimax.io/docs/api-reference/video-generation-v2-delete)
- [Upload File](https://platform.minimax.io/docs/api-reference/file-management-upload)
- [Pay-as-you-go pricing](https://platform.minimax.io/docs/guides/pricing-paygo#video)
- [H3 Context-IR API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)
- [MiniMax-H3 Base Prompt Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)
- [MiniMax-H3 Reference Prompt Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)

### AceData Cloud（当前 MiniMax-H3 传输网关）

- [MiniMax H3 Video Generation API Integration Guide](https://platform.acedata.cloud/documents/minimax-videos-integration)
- [MiniMax H3 Task Query API Integration Guide](https://platform.acedata.cloud/documents/minimax-tasks-integration)
- [AceDataCloud HailuoMCP source](https://github.com/AceDataCloud/HailuoMCP)

### Azure Speech

- [Text to speech](https://learn.microsoft.com/en-us/azure/ai-services/Speech-Service/text-to-speech)
- [SSML pronunciation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation)
- [Viseme events](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-speech-synthesis-viseme)
- [Batch transcription and word timestamps](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-create)

### 抖音交付兼容

- [抖音开放平台：上传视频](https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/upload/)
- [抖音内容发布接入方案](https://open.douyin.com/platform/resource/docs/ability/content-management/douyin-publish-solution)

官方资料只提供 MP4/WebM、720p 以上、大小和时长等基础边界，没有给出统一字幕安全区、固定码率或响度值；本项目的安全区、编码和响度是内部可调整交付预设，不冒充平台强制规范。

这些页面只证明接口和声明的能力，不证明固定剧本的实际质量。GPT Image 2、H3 和 Azure 都是可选能力，不因列入来源清单而成为必选项。

## 2. 闭源平台官方资料

仅用于研究产品机制；最终系统不调用或依赖这些产品。

- 即梦：[官方主页](https://www.jimeng.com/)
- 可灵：[VIDEO 3.0 用户指南](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide)
- LTX Studio：[产品主页](https://website.ltx.studio/)、[完整工作流教程](https://ltx.io/blog/ltx-studio-tutorial)、[TV/Media workflow](https://ltx.io/studio/solution/tv-media-networks)、[Retake 教程](https://ltx.io/blog/retake-ai-directing-tool-ltx-studio)、[Elements 教程](https://ltx.io/blog/getting-started-with-elements)、[Retake API](https://docs.ltx.io/api-documentation/api-reference/async-video-generation/submit-retake)
- LTX Studio 制作方法：[一致角色指南](https://ltx.io/blog/how-to-create-a-consistent-character)、[完整预视化流水线](https://ltx.io/blog/how-to-build-a-complete-pre-visualization-pipeline)
- Google Flow：[Ingredients 与 Frames 帮助](https://support.google.com/flow/answer/16353334?hl=en)
- Runway：[Gen-4 Image References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[Image-to-Video Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)
- 可灵：[多图参考公开说明](https://kuaishou.gcs-web.com/zh-hant/news-releases/news-release-details/kuaishoukelingaituichuduotucankao-gongneng)
- 纳逗 Pro：[业务页](https://www.iqiyi.com/common/aqynatoopro.html)、[发布说明](https://pages.iqiyi.com/m/scpr/63x0au29.html?qyc-pr=1)、[新功能说明](https://pages.iqiyi.com/m/scpr/63x0avkx.html?qyc-pr=1)

公开产品资料中的效果描述标为 `PRODUCT-CLAIM`。由于这些产品不可用于本项目，本轮不安排登录、积分或平台实测。

## 3. 开源源码固定快照

| 项目 | 仓库 | 本轮审查 commit | License |
| --- | --- | --- | --- |
| Toonflow | [HBAI-Ltd/Toonflow-app](https://github.com/HBAI-Ltd/Toonflow-app) | [`bc61ec7a1b5df31293b286981a5f4ad4635464ee`](https://github.com/HBAI-Ltd/Toonflow-app/commit/bc61ec7a1b5df31293b286981a5f4ad4635464ee) | Apache-2.0 |
| ComfyUI | [comfy-org/ComfyUI](https://github.com/comfy-org/ComfyUI) | [`7fe8a6138504f90ff7be82f3babf416da32876b1`](https://github.com/comfy-org/ComfyUI/commit/7fe8a6138504f90ff7be82f3babf416da32876b1) | GPL-3.0 |
| ViMax | [HKUDS/ViMax](https://github.com/HKUDS/ViMax) | [`05a48943878312d88fe5a016c12a9654940ecc43`](https://github.com/HKUDS/ViMax/commit/05a48943878312d88fe5a016c12a9654940ecc43) | MIT |
| Jellyfish | [Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish) | [`a9678194ddf2d9be3ccbe78d4287d87d5089e123`](https://github.com/Forget-C/Jellyfish/commit/a9678194ddf2d9be3ccbe78d4287d87d5089e123) | Apache-2.0 |
| Wind Comic | [ChrisChen667788/wind-comic](https://github.com/ChrisChen667788/wind-comic) | [`c83e1cf5e9b88fa8ac62bb737c79985a95243b8d`](https://github.com/ChrisChen667788/wind-comic/commit/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d) | MIT |
| LumenX | [alibaba/lumenx](https://github.com/alibaba/lumenx) | [`f2a02e23171447c939e7d8e1386b24d17049bbf1`](https://github.com/alibaba/lumenx/commit/f2a02e23171447c939e7d8e1386b24d17049bbf1) | MIT |
| LTX Desktop | [Lightricks/LTX-Desktop](https://github.com/Lightricks/LTX-Desktop) | [`2ac57301af814f4fbdde2380c23e64e673860770`](https://github.com/Lightricks/LTX-Desktop/commit/2ac57301af814f4fbdde2380c23e64e673860770)（2026-08-14 上游快照） | Apache-2.0（应用代码） |
| LocalMiniDrama | [xuanyustudio/LocalMiniDrama](https://github.com/xuanyustudio/LocalMiniDrama) | [`7b6c1a748e9e3013b88a902cfbfd31ec283da0d1`](https://github.com/xuanyustudio/LocalMiniDrama/commit/7b6c1a748e9e3013b88a902cfbfd31ec283da0d1) | MIT |
| dramai | [hyyyyyyz/dramai](https://github.com/hyyyyyyz/dramai) | [`2ec38104380823aff711c96ed852d5f713b8ac5a`](https://github.com/hyyyyyyz/dramai/commit/2ec38104380823aff711c96ed852d5f713b8ac5a) | Apache-2.0 |
| director_ai | [freestylefly/director_ai](https://github.com/freestylefly/director_ai) | [`dd812c756f0ee0533cd7d36042a16144ab1b1202`](https://github.com/freestylefly/director_ai/commit/dd812c756f0ee0533cd7d36042a16144ab1b1202) | 未发现 License 文件，不复用代码 |

本轮为静态源码审查，没有安装全部依赖、运行全套测试或调用仓库配置的真实 Provider。License 取自对应快照的仓库声明；进入发布或 SaaS 集成前仍需按最终使用方式复核。

LTX Desktop 的应用代码与模型权重必须分开判断：[应用 License](https://github.com/Lightricks/LTX-Desktop/blob/2ac57301af814f4fbdde2380c23e64e673860770/LICENSE.txt) 是 Apache-2.0，但 [NOTICES](https://github.com/Lightricks/LTX-Desktop/blob/2ac57301af814f4fbdde2380c23e64e673860770/NOTICES.md) 明确指出模型权重另受模型条款约束。这里仅记录会影响底座选择的事实，不作法律结论；当前决定只复用 Apache 应用代码，LTX 权重不进入产品或本轮实验。

LTX 模型本身的版本与能力另见 [Lightricks/LTX-2](https://github.com/Lightricks/LTX-2)。Desktop 代码中的生成、Retake、Extend 和 LoRA 路径仍带有具体 LTX 模型版本假设，因此不能把桌面应用壳等同于某个模型版本的通用能力；当前项目只复用应用与编辑代码。

## 4. ComfyUI 官方开发资料

- [Submit a workflow](https://docs.comfy.org/api-reference/cloud/workflow/submit-a-workflow-for-execution)
- [Custom nodes overview](https://docs.comfy.org/custom-nodes/overview)
- [Server-side custom node overview](https://docs.comfy.org/custom-nodes/backend/server_overview)
- [Registry overview](https://docs.comfy.org/registry/overview)

## 5. 证据使用规则

- 官方文档用于确认接口、字段和产品公开功能；
- 固定源码用于确认代码里实际存在的数据模型和执行路径；
- README 宣传若没有对应源码路径，不升级为 `SOURCE-CONFIRMED`；
- 宣传中的“连贯、精准口型、一键成片”等效果词，未经固定输入生成仍为 `PRODUCT-CLAIM` 或 `UNKNOWN`；
- 能接受某种参考输入不等于能稳定遵循它；
- 最终路线只采用解决了具体质量问题的资源。
