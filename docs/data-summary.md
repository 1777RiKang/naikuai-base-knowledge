# 《数据摘要》全文与接入说明

原始资料署名：**乱走丶沙城**。当前版本 **8.6.26.10**。

[完整正文](../sources/data-summary/full.md) · [原图](../sources/data-summary/images/) · [分类章节](../knowledge/README.md)

## 作者授权与联系

维护者于 2026-10-22 确认已取得作者授权，并于同日要求公开《数据摘要》全部正文。
本记录依据维护者确认；授权原文尚未收录。不据此推定可商用或按 CC BY-NC-SA 重新授权。

作者公开联系 QQ：2413493480（维护者于 2026-10-22 指定公开）。

## 公开范围与原作声明

公开完整 Markdown、372 张表格、41 处原始图片引用及分类章节 JSON 正文。
来源为维护者已有的 Word 文件及其全文提取版本，不依赖任何第三方站点转载。
原有《系列文件使用说明》保留在全文开头，包含非官方性质、数据准确性限制、一个月有效期及禁止商业牟利等说明。
原文引用的独立《数据摘要-免责声明》文件未随本地资料提供；本仓库不编造其内容。
作者指定公开的联系方式保留；其他联系方式脱敏。原始本地 Word 与 Markdown 不修改。

完整 Markdown 和图片为阅读版；knowledge/<category>/*.json 的 content 为独立章节正文。
从 487 个标题中提取 387 个含正文的章节，父章节只保留自身文字，避免重复包含子章节。
原文图片从 Word 恢复；章节 JSON 使用可直接访问的原图 URL。
提取记录见 [extraction-manifest.json](../sources/data-summary/extraction-manifest.json)，保留原始文件哈希、正文与图片校验信息。

## 构建与查询

```bash
python build/import_sources.py
python build/catalog_docs.py
python build/build_sqlite.py dist
python build/build_knowledge.py --out dist
python build/query.py dist/glossary.sqlite "锋利" --knowledge-db dist/knowledge.sqlite --include-unconfirmed
```

仓库已有公开正文，克隆后即可构建，不需要维护者的 .local 文件或原始 Word。
CI 的 glossary-sqlite 产物包含全文 knowledge.sqlite、清单及 data-summary/ 阅读版。
保留署名、来源使用说明与许可文件；不得把原作重新授权为本仓库的 CC 数据。

## 更新与审核

授权发布不等于完成事实核验。387 个导入章节保持 draft，不自动计个人贡献积分。
默认 AI 仅引用 confirmed；--include-unconfirmed 用于预览待审正文，不能当作已经确认的结论。
内容更新时需复核适用版本；内容哈希变化退回 draft，未变的章节保留原审核状态。
已移除站点抓取记录与转载副本，只以此原作生成章节和全文检索库。
