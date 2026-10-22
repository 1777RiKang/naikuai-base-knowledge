# 《数据摘要》接入说明

原始资料署名：**作者**。此页是接入说明和目录入口，不是《数据摘要》正文。

《数据摘要》是玩家整理的游戏资料，涵盖基础数据、PVE、PVP、副本、跟宠、精灵、玩家机制、坐骑、传说装备、活动、附魔与占卜。
已检查的本地正文版本为 8.6.26.10。作者说明资料有效期为一个月，仍需按最新游戏版本核对。

## 公开与本地范围

按维护者确认的范围，公开仓库只提交标题、分类、来源定位、内容哈希和审核状态。
正文、免责声明原文及联系方式留在原始本地文件中，不提交 GitHub。
本地检索副本对联系方式及常见凭据格式脱敏，不改写原始文件，不把原作重新授权为本仓库的 CC 数据。

本批次从本地 Markdown 的 487 个标题中提取出 387 个含正文的独立章节块；
父章节只保留自身文字，不重复包含所有子章节。章节路径生成稳定 ID，内容变化更新哈希并重新待审。
分类按主题组织，查看 [知识目录](../knowledge/README.md)。

## 来源归属

直接来自原始正文的条目标 `source: author`、`attribution: 作者`、`acquired_via: local-file`。
nkwiki 上转载或拆分的章节仍标原始归属“作者”，同时记 `acquired_via: nkwiki` 和原文章定位。
章节归属判断规则保存在 origin_rule 中，可人工检查；来源不确定的记录仍是 draft。

## 本地接入

```powershell
python build/import_sources.py --nkwiki-dir 'D:\qq_bot\.review\nkwiki' --summary 'D:\qq_bot\.review\gamekb\v10\full.md'
python build/catalog_docs.py
python build/build_sqlite.py dist
python build/build_knowledge.py --out .local --local-bodies .local/bodies.jsonl
```

输出 .local/bodies.jsonl、.local/knowledge.sqlite 和本地回执；这些路径被 Git 忽略。
查询示例见 [AI 接入说明](ai-usage.md)。新导入记录均为 draft，AI 默认不引用。

## 更新与审核

重新运行离线导入前更新本地来源快照。导入先核对原始文件哈希，内容哈希未变时保留已有审核；
内容变化时退回 draft，来源移除的记录保留并标 deprecated。
通过 PR 审查公开目录变化，确认来源、版本、知识内容及使用范围后再更新审核状态。
本文不表示已取得公开分发第三方全文的授权。
