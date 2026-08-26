# 本地长篇测试语料

`scripts/download_public_domain.py` 从 Project Gutenberg 固定作品页对应的官方缓存地址下载《西遊記》《紅樓夢》《水滸傳》《三國志演義》和《聊齋志異》。这些作品分别覆盖长途行程、复杂家族关系、群像势力与地点网络、历史多线战争和短篇组合作品，用于验证普通散文体长篇，而非只测试人工结构化样例。Project Gutenberg 将这些版本标为在美国属于公版。其他地区运行或再分发前，需要按当地版权期限重新核验。

下载文件被 `.gitignore` 排除，只用于本地导入、分片和基准测试。项目不附带现代版权小说，也不绕过电子书数字版权管理。

来源页：

- https://www.gutenberg.org/ebooks/23962
- https://www.gutenberg.org/ebooks/24264
- https://www.gutenberg.org/ebooks/23863
- https://www.gutenberg.org/ebooks/23950
- https://www.gutenberg.org/ebooks/51828
