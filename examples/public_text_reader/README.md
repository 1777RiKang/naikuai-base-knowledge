# 公开正文检索组件（public-text-adapter）

离线、确定性的 `PublicTextReader`：从已验证的公开知识库 SQLite 中检索“职业玩法 / 精灵技能 / 副本定义与攻略”正文。不联网、不调用模型、不读取题集或 stage2 数据，也不修改数据库。

## 可用范围

- 职业：`2.3对各职业的深刻理解` 下的职业正文与玩法/机制说明。
- 精灵：`6.2各精灵介绍`、`6.3.6/6.3.7` 精灵面板正文；技能问题优先精灵章节。
- 副本：`4.1秘境`、`4.2轮回塔` 下的副本入口、开启人数、世界、层数、难度与攻略正文。
- 宝石：只处理公开快照实际存在的宝石效果表；镶嵌/升级/切割缺失时明确 `abstain`，不用效果表冒充规则。

下列内容不回答：普通聊天、配置数值、配方、未收录道具、历史/删除线/未完成段落、需要跨类型猜测的同名条目。

## 使用

```python
from examples.public_text_reader.public_text import PublicTextReader

DB = "path/to/naikuai-kb-public.sqlite"
SHA = "d80d95a6de1ed68ab784828522b700920c93c0b94144692d37073c50972c46c3"

with PublicTextReader(DB, SHA) as reader:
    result = reader.lookup("尼伯龙根十人怎么打")
    print(result["action"], result["text"])
```

分页与限制：

```python
result = reader.lookup("无尽之海有什么副本", limit=2)
if result["pagination"]["has_more"]:
    more = reader.lookup("无尽之海有什么副本", limit=2, cursor=result["pagination"]["next_cursor"])
```

CLI：

```pwsh
python examples/public_text_reader/public_text_cli.py --db path/to/naikuai-kb-public.sqlite --info
python examples/public_text_reader/public_text_cli.py --db path/to/naikuai-kb-public.sqlite -q "希尔芙技能" --json
```

`--sha256` 可省略；省略时从同目录 `manifest.json` 读取该文件的 SHA-256，仍会与数据库实际哈希比对。路径由调用方配置，模块内不含本机绝对路径。

## 安全与完整性

- 打开前对整文件做 SHA-256 校验，连接使用 `mode=ro&immutable=1` 并启用 `PRAGMA query_only=ON`。
- 校验 `meta.schema_version=unified-r3-public`、`meta.content_scope=public_clean_only`；要求 `facts=0`，否则抛错。
- 若存在同目录 `manifest.json`，会校验其中的 SHA-256 与文件大小；不匹配则拒绝打开。
- 只索引 `publishable=1`、`sensitive=0`、`review_status=active` 且属于上述业务章节的段落。`database_info()["chunks_count"]` 是过滤后的可索引段落数，可能小于 `meta.stats.chunks_count`。
- 所有 SQL 均参数化；问题文本不参与拼接。
- 不执行下载脚本或任何外来代码。

## 输出契约

`lookup()` 返回与结构化 reader 兼容的字典，主要字段：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 本组件版本 `public-text-reader/v1` |
| `question` | 原始问题；`query` 保留为兼容别名 |
| `scope` | `game`（业务范围）或 `non_game`（普通聊天） |
| `entity_type` | `profession` / `fairy` / `dungeon` / `gem`，未识别为 `null` |
| `entity` | 命中的规范实体名，未识别为 `null` |
| `conditions` | 已解析的世界/人数/层数/难度条件 |
| `requested_intent` | `list` / `guide` / `intro` / `skills` / `growth` / `overview` / `general` / `none` |
| `matched` | 是否识别为本组件业务范围 |
| `action` | `answer_with_caveat` / `clarify` / `abstain` |
| `reason` | 机器可读原因，如 `ambiguous_branch`、`ambiguous_world_abbrev`、`no_strategy_in_public_text`、`only_incomplete_or_unreleased` |
| `route` | `{handler, intent_family, scope, subject, entity_type, conditions}`；意图取 `profession_guide` / `elf_skills` / `dungeon_intro` / `dungeon_guide` / `gem_overview` 或 `null`，外部精灵类型为 `elf` |
| `text` | 中文说明或条件性摘录 |
| `clarify` | `{prompt, options}` 或 `null` |
| `buckets` | `confirmed/conditional/conflict/unknown` 四桶 |
| `evidence` | 每段的真实定位与摘录 |
| `database` | 版本、哈希、快照日期、过滤后段落数 |
| `pagination` | limit/cursor/是否截断/字符预算 |
| `intent` | 领域、实体、条件、分支 |
| `limits` | 声明无网络/无模型、未回答配置数值等 |

证据政策：

- 公开摘要正文只进入 `conditional`；`confirmed` 与 `conflict` 始终为空。
- `unknown` 记录缺字段、条件不匹配、分页、未完成/删除线、未收录等限制。
- 每条证据带 `source`、`doc_id`、`chunk_id`、`section`、`version` 等真实字段，可直接回查 SQLite。
- 权重只用于排序，不转换成概率。

## 语义规则

- 先识别领域（职业/精灵/副本/宝石），同名条目不会因字面相同跨类型作答。
- 职业机制优先职业章节，并从“附魔属性”长列表中保留核心机制、关键附魔组与主要限制；不把Ⅰ/Ⅱ/Ⅲ重复数值全部倾倒出来。
- 精灵技能优先精灵章节，并跳过面板、礼物与定位段，从“技能数值/技能介绍”等真实技能段开始摘录。
- 副本按世界、人数、层数、难度过滤；条件不足时只澄清具体分支，不泛问等级。
- 世界简称按唯一最长前缀解析；命中多个世界时返回 `ambiguous_world_abbrev` 并列出真实世界名，不自行挑选。
- “有什么副本/有哪些”按入口概览列出世界内真实入口；多个分支不能合并成一份攻略。
- 问“怎么打/攻略/机制”时只取阶段、机制、仇恨、分摊等攻略正文；正文只有入口/属性面板时返回 `no_strategy_in_public_text`，不把入口表当打法。
- 无符合条件的分支时返回 `abstain` 并说明已应用条件。
- 已识别业务范围但公开正文缺失：`matched=true` + `abstain`，不使用相似 NPC 替代。
- 普通聊天：`matched=false`，`scope=non_game`，`route.handler=none`。
- 历史、删除线、猜测、未上线、未完成内容不作为当前攻略；删除线在摘录前已清洗。
- `clarify` 的 `text` 同时包含提示和真实可选分支，接入方不必只显示泛化提示。

## 测试

```pwsh
python build/build_public_r3.py --out dist/public-r3
python -m unittest discover -s tests -p test_public_text_reader.py -v
```

测试分两层：

- 合成 SQLite：类型隔离、同名条目、多人分支、世界简称、历史/未完成、缺人数、长文截断、分页游标、宝石澄清、普通聊天、敏感段落、错误 SHA、`facts>0`、只读/关闭状态、SQL 注入式输入、技能/职业核心抽取、无攻略 abstain 与真实分支澄清文本。
- 本地构建的公开库：公开源示例问句，并逐条校验证据的 `source/doc_id/section/version` 与数据库一致；额外覆盖弓箭手多附魔组、世界入口概览与简称解析。测试默认读取 `dist/public-r3/naikuai-kb-public.sqlite`，也可用 `PUBLIC_TEXT_TEST_DB` 指定已验证公开库；模块不内置本机路径。CI 在构建后运行该测试文件，实际覆盖真实库用例。

## 限制

- 只覆盖公开社区摘要，不证明当前游戏配置；输出必须保留“条件性摘录”提示。
- 正文摘要可能滞后或缺失；组件不会推断、补写或跨条目拼凑答案。
- 分页按候选段落计数，单段过长时按字符预算截断，`truncated` 与 `has_more` 分别表示两种截断。
- 这是检索组件，不是最终回复生成器；接入时应与主结构化 reader 组合，并遵守上层证据政策。
