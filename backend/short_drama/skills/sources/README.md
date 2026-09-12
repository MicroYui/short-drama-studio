# 方法来源与参考

这里是来源资料层，当前可选方法统一位于 `../methods/`。来源目录中的 `MiniMax` 表示实际出处，不是产品方法名称。

| 目录 | 内容 |
| --- | --- |
| `minimax_design/3.0.11/workflows/` | 来自本机 MiniMax Design 的短剧、广告和 MV Workflow 原文及相关参考，保留原有相对引用 |
| `minimax_h3/original/` | 实际采用的上游中文 Skill、作者/来源元数据和必要的英文参考 |
| `minimax_h3/translations/` | 当前方法读取的 4 份参考译文；相应英文原文保留在 `original/` |

各方法 manifest 的 `role_source_sections` / `stage_role_source_sections` 声明进入请求的章节；`upstream_sources` 记录出处和作者。参考资料不会作为第二份方法目录展示，也不会整包注入模型。

未使用的英文 SKILL 副本、模型选择/固定回退/旧镜头表资料已删除。保留的原文没有改写成项目原创内容；具体来源和再分发边界见项目根目录 [NOTICES.md](../../../../NOTICES.md)。

本地图片制作方法还参考公开技术说明：[Adobe Levels/Curves 白点](https://helpx.adobe.com/photoshop/using/adjust-color-tone-levels-curves.html)、[Pillow 滤镜与缩放](https://pillow.readthedocs.io/en/stable/handbook/concepts.html#filters)。现有 Builder/Verifier 中可选摄影纸色处理为本地整理，局部透视采样为本地实现；没有复制产品私有代码或整篇文档。实际缺陷、采用范围与验证见[细线采样和摄影纸色研究](../../../../docs/research/2026-09-12-generation-quality/47-plane-resampling-and-paper-tone.md)，来源说明不另行注入每次模型请求。

2026-09-12的正文精确引用与观察复用方法参考[W3C文本引用选择器](https://www.w3.org/TR/annotation-model/#text-quote-selector)、[Anthropic唯一原文编辑](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool)及[上下文工程说明](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)；布局控制参考[OpenAI图像生成限制](https://developers.openai.com/api/docs/guides/image-generation)与[草图转实景方法](https://developers.openai.com/api/docs/guides/image-prompting#turn-a-drawing-into-a-realistic-image)。本地角色说明结合真实失败整理，未复制这些文档的完整提示词或闭源实现；新图像文档以2.5为例，不表示本地gpt-image-2已验证相同能力。具体合同差异、采用范围及验收见[第三轮集中修复](../../../../docs/research/2026-09-12-generation-quality/61-third-pre-video-consolidation.md)。这些来源说明不额外注入运行角色。
