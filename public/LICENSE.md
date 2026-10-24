# 开源许可证与组件分许可说明 (License Information)

本项目采用多许可证分层管理架构（Multi-licensed Architecture），针对不同性质的代码、词表与知识正文资产分别赋予明确的法律许可规范：

---

## 一、脚本与工程基础设施 (Scripts & Infrastructure)

**适用范围**：
- `scripts/build_public.py`
- `scripts/kb_query_public.py`
- 相关的数据库架构 DDL 定义与构建工具代码

**许可协议**：[MIT License](https://opensource.org/licenses/MIT)

```text
MIT License

Copyright (c) 2026 Naikuai Base Knowledge Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 二、术语词表、别名与消歧元数据 (Glossary & Terms Data)

**适用范围**：
- `glossary/aliases.tsv`
- `glossary/alias_groups.tsv`
- `glossary/alias_senses.tsv`
- `sources.csv`
- 社区共同维护的公开术语词库

**许可协议**：[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

**主要权利与义务**：
1. **署名 (Attribution)**：使用或分发本词表数据时，必须保留项目来源声明与贡献者署名。
2. **非商业性使用 (NonCommercial)**：严禁将本词表及由其衍生的数据用于商业营利目的或有偿数据服务。
3. **相同方式共享 (ShareAlike)**：若对本数据进行修改、转换或二次创作，必须采用与本协议相同或兼容的许可协议进行公开发布。

---

## 三、《数据摘要》正文

适用范围：`sources/chunks.jsonl`，以及生成数据库中相应正文和全文索引。

原作者：乱走丶沙城。来源版本：8.6.26.10。许可标识：`LicenseRef-Original-Source`。

沿用上游仓库既有的[作者授权记录与原作使用说明](https://github.com/1777RiKang/naikuai-base-knowledge/blob/main/docs/data-summary.md)。该记录基于维护者确认，授权原文尚未收录；本文件不编造原作免责声明，也不增加其授权范围。

保留原作者署名、出处和原作使用说明。联系方式脱敏属于本次导出处理，相关内容不因此转为 MIT 或 CC BY-NC-SA 再许可。不推定允许商用或其他未记录用途。事实审核与公开授权分别处理，来源版本不代表官方认证。

本包代码、词表与原作正文的许可范围相互独立；生成一个 SQLite 文件不会改变各部分的归属。完整约束同时参见上游仓库 DATA-LICENSE.md。
