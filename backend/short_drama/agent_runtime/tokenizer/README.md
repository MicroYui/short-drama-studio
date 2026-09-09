# 本地上下文计数

`o200k_base.json.gz` 是 OpenAI 公开 o200k_base 词表转换为本地已有 Hugging Face `tokenizers` 可读取的格式。它用于发送前估算纯文本 token，不是视频或语言模型权重。运行时不下载数据或包；实际计费 token 以 Provider usage 为准。

- 词表：[OpenAI public encoding](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken)
- 分词规则：[OpenAI tiktoken](https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py)，MIT。
- 转换方式参考本机 Transformers `TikTokenConverter`，Apache-2.0。
- 重建：使用现有 Python 环境运行 `scripts/build-studio-tokenizer.py <已下载词表> backend/short_drama/agent_runtime/tokenizer/o200k_base.json.gz`。
- 计数包括文本中的特殊标记的字面内容，不将用户文本当作控制 token。

License 文本随本目录保留。模型别名对应的封装和隐藏计数可能不同，因此另外预留封装空间，并记录估算与实际的差异。
