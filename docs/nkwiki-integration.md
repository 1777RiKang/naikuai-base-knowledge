# nkwiki 接入说明

站点资料署名：**nkwiki**。接入使用维护者提供的本地快照，不访问或更改试运行实例。
本版不提供联网爬虫，不假定已确认站点 API 授权或文章深层链接规则。

## 已核对的输入

快照时间：2026-10-22。raw/ 有 7 个数据 JSON 和 1 个 manifest.json。
text/ 有 1,005 篇 Markdown：articles 432、equipment 455、gem-attributes 70、gems 30、fairies 12、encyclopedia 6。

原始 wiki-kb.sqlite 有 376 个实体、403 个别名、573 条记录和 1,677 个文档块。
这些是旧索引的统计口径，与当前分类目录的记录数量不同。
补丁库加入了社区术语，不能被统一当作纯 nkwiki 来源；接入脚本不读取补丁正文，也不更新部署。
程序只从原始库读取名称别名，从 raw/ 读取独立资料，并用《数据摘要》原文判断原始归属。

## 分类与来源

- 装备、宝石、宝石属性、精灵、百科按其主题目录组织。
- articles 按基础数据、PVE、PVP、副本、任务、制作、机制、坐骑、活动等主题分类。
- 站点记录用 source=nkwiki、attribution=nkwiki；转载《数据摘要》的原始来源是 author/乱走丶沙城。
- URL 保留已确认的站点首页，locator 保留原始类型与 ID，例如 equipment:481。
  未验证页面路由前不编造文章深层链接，可在站点按标题定位。
- 内容哈希和 raw 文件 SHA-256 用于核对快照；不以文件修改时间代替来源更新时间。

[分类目录](../knowledge/README.md) · [来源统计](source-overview.md) · [导入文件校验清单](../sources/import-manifest.json)

## 生成公开产物

```bash
python build/validate.py
python build/privacy.py
python -m unittest discover -s tests -v
python build/build_sqlite.py dist
python build/build_knowledge.py --out dist
```

CI 的 glossary-sqlite 包含术语 glossary.sqlite 和仅元数据的 knowledge.sqlite，各自有 manifest。
公开 knowledge.sqlite 的 content_scope 必须是 metadata-only，正文块数量必须为 0。
知识目录标 LicenseRef-Original-Source；不能把站点或原作者正文视为 MIT/CC 的仓库自有数据。

## 本地全文与检索

按 [数据摘要接入说明](data-summary.md) 的离线命令导入，全文只输出到被忽略的 .local/。
构建器会拒绝把全文数据库输出到 dist/ 或公开产物目录。检索按来源返回“乱走丶沙城”或“nkwiki”，
并带原始定位、更新时间、审核状态。默认仅 confirmed，显式调试参数才允许查看待审内容。

更新过程为“本地快照 → 校验与分类 → 生成目录差异 → PR 审核 → 合并”，不自动确认游戏事实。
完全相同的正文用 duplicate_of 关联并在检索时去重；按主题编号或标题路径保持稳定 ID。
自动导入及重复目录都不计贡献积分，不能靠转载和拆分正文刷分。
