# 参与贡献

词条新增、纠错、文档与脚本改进都欢迎。请 Fork 仓库，新建分支后向 main 提交 PR。
一个 PR 尽量只处理一个词条、一组同义变体或一个工程问题。
维护者负责审查与手动合并，CI 必须通过；维护者自己的 PR 可以自行合并。

## 本地检查

```bash
pip install -r requirements.txt
python build/validate.py
python build/privacy.py
python -m unittest discover -s tests -v
python build/build_sqlite.py dist
```

不要提交 `dist/`、`__pycache__/`、环境文件或任何 token。

## 新增词条

1. 在 `terms/<kind>/<slug>.yaml` 新建文件，id 与路径匹配且长期稳定。
2. 填写说法、规范名、语境、释义、已脱敏证据与自选公开贡献名。
3. 不充分的内容先标 `draft`。维护者确认后改为 `confirmed`；有争议标 `disputed`。
4. 检查后提交 PR；维护者可以在你的 PR 分支补充审核字段。

```yaml
id: item.example
term: 示例简称
canonical: 示例全称
aliases: [示例别名]
kind: item
senses:
  - context: 交易/装备
    definition: 一句话说明它是什么、用来做什么。
    notes: 可选补充，写清歧义和版本差异。
provenance:
  source: community
  evidence:
    - text: 已去掉个人信息的短句证据
      channel: 公屏
      captured_at: '2026-10-22'
      redacted: true
contributors:
  - name: 自选公开贡献名
    role: annotator
review:
  status: draft
  reviewer: null
  reviewed_at: null
version: 1
updated_at: '2026-10-22'
```

日期须加引号，避免 YAML 将其解析成日期对象。
自选贡献名限 1–32 字；填写即同意在词条与贡献榜公开展示，不要求与 GitHub 名一致。
不要冒用他人名义，不要填真实身份、联系方式或凭据。首次 annotator 名作为默认贡献名。

## 规则

- 一个独立词条可通过 senses 表达不同语境义项；同物异写优先放 aliases，避免重复计数。
- 别名全局唯一。规范全称可以相同，但词条必须有独立含义，例如附魔版本与原物品。
- 不收录价格、汇率、账号交易、广告，不把证据里的报价作为行情数据。
- 证据只保留说明用法的短句，去掉玩家昵称、QQ 号、服务器编号等个人信息。
- 旧知识过时后标 deprecated；不要删除或复用旧 id。
- 维护者负责全部内容审核。争议未解决时可以按 disputed 合并，AI 默认不引用。
- 首次引入词条的 PR 作者获得该条后续 confirmed 时的 1 分；代合并和修订不转移归属。

详细计分与改名方式见 [贡献排行榜规则](docs/leaderboard.md)。

## 许可

提交即同意以 CC BY-NC-SA 4.0 发布词条数据、以 MIT 发布代码。
只提交你有权提供的内容，不复制第三方攻略正文。
