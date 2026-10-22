# 字段规范

## 文件与 id

- 路径：`terms/<kind>/<slug>.yaml`，`slug` 使用小写 ASCII 与连字符。
- `id`：`<kind>.<slug>`，与路径一一对应；id 一经合并不得修改或复用。
- 一个文件表示一个独立说法/知识点；不同语境义项放在 `senses` 中。同物异写优先用别名，避免重复文件。

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定标识，格式 `<kind>.<slug>` |
| `term` | 是 | 词条名，保留玩家最常用的写法 |
| `canonical` | 是 | 规范写法/全称；可以是「A / B」这类并列 |
| `aliases` | 否 | 简称、错别字、语序变体；全局唯一 |
| `kind` | 是 | item / material / consumable / mount / service / jargon / composite / system |
| `senses` | 是 | 至少一条；每条含 `context`、`definition`，可选 `notes` |
| `relations.composes` | 否 | 仅 composite 使用，指向拆分出的成分 id |
| `relations.variant_of` | 否 | 指向同物的另一写法/版本 |
| `relations.related` | 否 | 相关词条，不做强约束 |
| `provenance.source` | 是 | community / in-game |
| `provenance.evidence` | 是 | 至少一条；含 `text`、`channel`、`captured_at`、`redacted` |
| `provenance.url` | 否 | 可核对的公开来源链接 |
| `contributors` | 是 | 自选公开贡献名（1–32 字），可与 GitHub 名不同；不填玩家隐私或 QQ 号 |
| `review.status` | 是 | draft / confirmed / disputed / deprecated |
| `review.reviewer` | 条件 | confirmed、disputed 必填 |
| `version` | 是 | 从 1 开始，实质修改 +1 |
| `updated_at` | 是 | 最后修改日期 |

## 状态机

```
draft ──审核──> confirmed ──过期/被推翻──> deprecated
  │                 │
  └──证据不足──> disputed <──异议──┘
```

- `deprecated` 不删除文件，保留检索与历史；回答时不再主动引用。
- 同一说法出现新义项时，补充 `senses` 并注明语境，不覆盖旧义项。

## 来源分层

| source | 用途 | 回答时的标注 |
| --- | --- | --- |
| `community` | 玩家标注的简称、黑话、组合缩写 | 「社区标注」 |
| 知识章节的 `author` | 已授权《数据摘要》正文 | 「乱走丶沙城《数据摘要》」 |
| `in-game` | 实测截图/录屏 | 「游戏内实测」 |

一个回答如果同时用了两层，必须分别标注，不能把玩家标注写成攻略站来源。

## 证据与隐私

- 公屏证据只保留能说明用法的短句，删除昵称、QQ 号、服务器编号。
- 词条文本禁止出现 6 位以上连续数字（校验脚本会拦截），所有来源的证据均须标记 redacted。
- 价格、汇率、账号交易信息不进入词库；证据中偶然出现的报价数字只作为原句
  上下文，不构成价格数据，也不得被机器人当作行情引用。
- 证据时间精确到日即可。

## 审核

- 所有外部贡献由仓库维护者审核并批准，CI 通过后手动合并。
- 维护者自己的修改也开 PR，CI 通过后可自行合并。
- 证据不足可按 draft 收录；争议可按 disputed 收录，AI 默认均不引用。
- 贡献者自填的公开贡献名用于署名和排名，不适用于聊天证据中的玩家昵称。
- 日期使用加引号的 YYYY-MM-DD 字符串。
