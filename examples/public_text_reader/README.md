# 公开正文检索示例（public_text_reader）

离线、确定性的 `PublicTextReader`：从已验证的公开知识库 SQLite 中检索“职业玩法 / 精灵技能与精灵系统章节 / 副本定义与攻略 / 宝石实际收录章节”正文。不联网、不调用模型、不读取题集或 stage2 数据，也不修改数据库。

## 可用范围

- 职业：`2.3对各职业的深刻理解` 下的职业正文与玩法/机制说明。
- 精灵：`6.2各精灵介绍`、`6.3.6/6.3.7` 精灵面板正文；技能问题优先精灵章节。另检索 `6.1精灵装备` 等不依赖具名实体的系统/机制子章节（加成与部位、数值对比、套装、分解、对应关系），按真实子章节和问法选择，系统用途不要求先给精灵等级。
- 副本：`4.1秘境`、`4.2轮回塔` 下的副本入口、开启人数、世界、层数、难度与攻略正文。
- 宝石：按公开快照实际存在的 `2.6.x` 子章节回答，不再把全库说成只有效果表。`2.6.1` 效果与 `2.6.3` 升级材料可条件性回答（升级保留三角/四角/五角与升级档次）；`2.6.4` 切割当前内容全部为删除线历史，只返回历史边界；本快照未收录 `2.6` 镶嵌模型时明确 `abstain`，并说明“仅限本公开快照”，不外推为游戏没有该机制。

下列内容不回答：普通聊天、配置数值、配方、未收录道具、历史/删除线/未完成段落、需要跨类型猜测的同名条目。缺失只在检查本次公开快照的实际候选章节后返回；不预设“整库只有某类表”。

## 使用

```python
from examples.public_text_reader.public_text import PublicTextReader

DB = "path/to/naikuai-kb-public.sqlite"
SHA = "<与数据库实际内容一致的 SHA-256>"

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
| `entity_type` | `profession` / `elf` / `dungeon` / `gem` / `elf_system`，未识别为 `null`（内部精灵领域名为 `fairy`，对外实体类型为 `elf`） |
| `entity` | 命中的规范实体名或系统章节名，未识别为 `null` |
| `conditions` | 已解析的世界/人数/层数/难度条件；宝石升级另含 `gem_shape` / `gem_stage` |
| `requested_intent` | `list` / `guide` / `intro` / `skills` / `growth` / `system` / `overview` / `upgrade` / `cut` / `inlay` / `effect` / `source` / `general` / `none` |
| `matched` | 是否识别为本组件业务范围 |
| `action` | `answer_with_caveat` / `clarify` / `abstain` |
| `reason` | 机器可读原因，如 `ambiguous_branch`、`ambiguous_world_abbrev`、`no_strategy_in_public_text`、`only_incomplete_or_unreleased` |
| `route` | `{handler, intent_family, scope, subject, entity_type, conditions}`；意图取 `profession_guide` / `elf_skills` / `elf_system` / `dungeon_intro` / `dungeon_guide` / `gem_overview` / `gem_upgrade` / `gem_cut` / `gem_inlay` / `gem_effect` / `gem_source` 或 `null`。具名精灵仍映射外部类型 `elf`；`6.1` 等无具名实体的系统章节用 `elf_system`，不会贴 `elf_skills` |
| `text` | 中文说明或条件性摘录；系统/宝石概述先给短结论，具体等级表只在等级/数值问句展开 |
| `clarify` | `{prompt, options}` 或 `null` |
| `buckets` | `confirmed/conditional/conflict/unknown` 四桶 |
| `evidence` | 每段的真实定位与摘录；系统/宝石表格回答另带 `quote`，保留该子段原始表格文本以便审计 |
| `database` | 版本、哈希、快照日期、过滤后段落数 |
| `pagination` | limit/cursor/是否截断/字符预算；未展示段落数在 `remaining_candidates`，说明在 `note` |
| `intent` | 领域、实体、条件、分支 |
| `limits` | 无网络/无模型、未回答配置数值；`confirmation_scope` 保存“当前配置未核对”的 release 边界，`excerpt_scope` 标明来源片段，`source_processing` 保存 `execution_note` 类删除线清洗等处理说明 |

### 新系统接口建议

新系统/机制章节不能复用 `elf_skills`，否则主调度会把“精灵装备用途”误当成某个精灵的技能。建议在主调度允许列表中加入下列值：

- `intent_family=elf_system`、`entity_type=elf_system`：用于 `6.1` 等无具名实体的精灵系统子章节；`subject` 取真实章节名（如 `精灵装备`），条件仍走现有 `conditions`。样例：`{"handler":"public_text.fairy","intent_family":"elf_system","entity_type":"elf_system","subject":"精灵装备","conditions":{}}`。理由：系统章节有独立语义，不能要求精灵等级，也不该贴到某个精灵的技能上。
- `intent_family=gem_upgrade` / `gem_cut` / `gem_inlay` / `gem_effect` / `gem_source`：用于宝石不同子章节。升级样例：`{"handler":"public_text.gem","intent_family":"gem_upgrade","entity_type":"gem","subject":"宝石升级","conditions":{"gem_shape":"四角"}}`；切割/镶嵌样例分别返回 `gem_cut` / `gem_inlay`，保留来源边界与历史边界。理由：主调度可据此区分“有表可答”“只有历史删除线”“本快照未收录”，不会把效果表或升级表当成其他模式。

证据政策：

- 公开摘要正文只进入 `conditional`；`confirmed` 与 `conflict` 始终为空，范围记录不会把摘要升级为 confirmed。
- `unknown` 只放两类可审计记录，均有 `record_type`、`kind`、`detail` 和 `scope`：
  - `knowledge_gap`：在本次查询的已验证公开快照与候选文档范围内，缺少字段/攻略/条目或条件不匹配（如 `missing_field`、`missing_strategy`、`condition_mismatch`、`not_in_public_snapshot`）。`scope` 只声明检查范围，不是该缺失事实的正面证据。
  - `source_boundary`：因历史/未上线/未完成/同名不同类型而不代答（如 `historical_source`、`incomplete_source`、`type_mismatch`）。
- `unknown.scope.release` 绑定本次实际打开的 SHA-256、manifest 校验、`kb_version`、快照日期和段落数；`scope.documents` 是实际检查过的文档 locator（`source/doc_id/chunk_id/section/version` 等）；`scope.checked` 说明检查的字段或标记。不会给无法定位的断言伪造 locator。
- 分页未展示说明只在 `pagination.note` / `remaining_candidates`；删除线清洗等处理说明只在 `limits.source_processing`（`record_type=execution_note`）；当前配置未确认边界只在 `limits.confirmation_scope` 和正文提示中，不伪装成知识条目。
- 没有知识缺失时 `unknown` 可以为空；未核对/来源片段边界仍保留在 `limits` 与正文提示中，不为了填桶制造记录。
- 每条 conditional 证据带 `source`、`doc_id`、`chunk_id`、`section`、`version` 等真实字段，可直接回查 SQLite。
- 权重只用于排序，不转换成概率。

## 语义规则

- 先识别领域（职业/精灵/副本/宝石），同名条目不会因字面相同跨类型作答。
- 职业机制优先职业章节，并从“附魔属性”长列表中保留核心机制、关键附魔组与主要限制；不把Ⅰ/Ⅱ/Ⅲ重复数值全部倾倒出来。
- 职业回答保留条件性来源片段提示，不补写成完整当前攻略，也不做无来源的职业强弱/优劣比较。
- 精灵技能优先精灵章节，并跳过面板、礼物与定位段，从“技能数值/技能介绍”等真实技能段开始摘录。
- 精灵系统/机制章节按真实子章节路由：用途/部位取 `6.1.1`，对比取 `6.1.2`，套装取 `6.1.3`，分解取 `6.1.4`，对应关系取 `6.1.5`；系统用途不要求精灵等级，具体数值仍标为条件性摘录。
- 系统章节区分概述与详情：问“用途/有啥用/哪些部位/套装/概述”时，`text` 只从真实子章节标题、表头属性标签和 `6.1.3` 套装行生成短概述，以中性陈述说明公开正文只按部位列出属性、未说明具体作用对象或结算方式，保留 `6.1.1` 的“仅与精灵属性发挥百分比有关”限制，并明确表内档位是装备等级、不是精灵等级；不展开 0–7 级逐行数值。只有问具体等级/数值时才输出等级表，并按问句中的部位、属性、等级过滤。
- 名称、属性名、套装名和数值都从源表读取，不在代码里写死某一题的完整答案；源表存在“无”列时只说明该栏没有对应加成，不补猜未提供的加成对象或机制。
- 同一段含多个子章节时按原文标题切分，只摘当前子章节；`6.1.4` 分解若全为删除线，返回 `only_historical_or_unreleased`，不把 `6.1.5` 对应表当作分解答案。
- 宝石先按问法定位镶嵌/升级/切割/效果，再检查实际子章节：升级用 `2.6.3` 材料表并保留形状/档次条件；切割 `2.6.4` 只有删除线历史时明确历史边界；镶嵌仅在本快照确无对应章节时返回来源边界，且不用效果表或升级表冒充。问具名宝石效果时只取 `2.6.1` 中该宝石所在行及所属职业分组标题，不把同表其他职业/其他宝石当答案；表内找不到该行时返回来源边界，不用其他行“提到过该词”代答。
- `2.6.3` 的合并首列按行继承：首列出现在分组首行时更新形状，后续只含“升级档次+材料”的行继承该形状；渲染为“形状｜升级档次：材料”。同一升级档次在不同形状下保留各自材料，不能合并成一套通用材料。
- 副本按世界、人数、层数、难度过滤；条件不足时只澄清具体分支，不泛问等级。
- 世界简称按唯一最长前缀解析；命中多个世界时返回 `ambiguous_world_abbrev` 并列出真实世界名，不自行挑选。
- “有什么副本/有哪些”按入口概览列出世界内真实入口；多个分支不能合并成一份攻略。具名副本问“有什么技能”时不套用“有什么”的入口概览，而是走攻略/机制子段，返回阶段、普通攻击、召唤、点名等真实技能正文。
- 问“怎么打/攻略/机制/技能”时只取阶段、机制、仇恨、分摊等攻略正文；正文只有入口/属性面板时返回 `no_strategy_in_public_text`，不把入口表当打法。
- 无符合条件的分支时返回 `abstain` 并说明已应用条件。
- 已识别业务范围但公开正文缺失：`matched=true` + `abstain`，不使用相似 NPC 替代。
- 普通聊天：`matched=false`，`scope=non_game`，`route.handler=none`。
- 历史、删除线、猜测、未上线、未完成内容不作为当前攻略；删除线在摘录前已清洗。
- `clarify` 的 `text` 同时包含提示和真实可选分支，接入方不必只显示泛化提示。

## 覆盖矩阵

四项分开记录，避免把“库里有”直接当成“能回答”。

| 公开子章节 | 库内存在 | 进入候选 | 能路由 | 能按条件回答 |
| --- | --- | --- | --- | --- |
| `6.1.1` 精灵装备加成/部位 | 是（3 段） | 是 | 是 | 是（`conditional`） |
| `6.1.2` 与玩家装备及 BUFF 对比 | 是（3 段） | 是 | 是 | 是（`conditional`） |
| `6.1.3` 精灵装备套装属性 | 是（2 段） | 是 | 是 | 是（`conditional`） |
| `6.1.4` 精灵装备分解 | 是（仅删除线历史） | 是 | 是 | 否（仅历史边界，不输出数值） |
| `6.1.5` 精灵对应精灵装备 | 是（与 6.1.4 同段） | 是 | 是 | 是（`conditional`，只取当前子段） |
| `2.6.1` 宝石效果 | 是 | 是 | 是 | 是（`conditional`；具名宝石只取对应行） |
| `2.6.3` 宝石升级材料 | 是 | 是 | 是 | 是（保留形状/档次行分组） |
| `2.6.4` 宝石切割 | 是（仅删除线历史） | 是 | 是 | 否（仅历史边界，不输出概率） |
| `2.6` 宝石镶嵌模型 | 否（本公开快照） | 否 | 是（识别意图） | 否（来源边界；不代表游戏无此机制） |
| `4.1.10.1` 海雾水母首领技能 | 是（入口表同段） | 是 | 是（技能→`dungeon_guide`） | 是（`conditional`，只取机制行） |

## 测试

```pwsh
python build/build_public_r3.py --out dist/public-r3
python -m unittest discover -s tests -p test_public_text_reader.py -v
```

测试分两层：

- 合成 SQLite：类型隔离、同名条目、多人分支、世界简称、历史/未完成、缺人数、长文截断、分页游标、宝石澄清、普通聊天、敏感段落、错误 SHA、`facts>0`、只读/关闭状态、SQL 注入式输入、技能/职业核心抽取、无攻略 abstain 与真实分支澄清文本。
- 精灵系统/宝石覆盖：不同问法下的 6.1 用途/部位/套装/对应、概述不倾倒等级表、概述不推断作用对象、具体等级仍取源值、多列表头与属性列不串位、6.1.4 删除线历史、无来源反例；宝石升级材料表、形状/档次合并行分组、同档次跨形状材料不同、具名宝石只取对应行、表内无该行时不串用其他行、切割历史与镶嵌来源边界。
- 范围记录测试：逐条校验 `unknown.record_type`、`scope.release` 的 SHA/manifest/版本、`scope.documents` 的 chunk locator 与数据库一致；分页和删除线说明不得出现在 `unknown`。
- 真实已验证公开库：默认读取 `dist/public-r3/naikuai-kb-public.sqlite`，也可用 `PUBLIC_TEXT_TEST_DB` 指定已核对的公开缓存。测试逐条校验证据的 `source/doc_id/section/version` 与数据库一致；覆盖弓箭手多附魔组、世界入口概览、简称解析、精灵装备 6.1 子章节、具名副本首领技能走机制行、具名宝石单行摘取与其他实体不串、宝石 2.6.3 升级与 2.6.4 历史边界，以及职业回答的 confirmation/excerpt 边界。CI 在构建公开 SQLite 后单独运行该测试文件，真实库用例会实际执行；模块与测试均不写死本机绝对路径。

## 限制

- 只覆盖公开社区摘要，不证明当前游戏配置；输出必须保留“条件性摘录”提示。
- 正文摘要可能滞后或缺失；组件不会推断、补写或跨条目拼凑答案。
- 分页按候选段落计数，单段过长时按字符预算截断，`truncated` 与 `has_more` 分别表示两种截断。
- 这是检索组件，不是最终回复生成器；接入时应与主结构化 reader 组合，并遵守上层证据政策。
- 本示例只描述公开正文检索能力与输出契约，不声称已完成 QQ 机器人部署，也不声称覆盖全部游戏知识或当前配置。
