# 按类型检索公开正文

`examples/public_text_reader/` 提供离线、确定性的公开正文读取示例，供机器人区分职业玩法、精灵技能和副本资料。它读取独立的公开 SQLite，不读取私用客户端数据库、聊天记录或模型凭据，也不调用模型。

## 使用

先按 [Release 与独立缓存](releases.md) 下载并验证公开快照，或从本仓库可信源代码本地构建：

```bash
python build/build_public_r3.py --out dist/public-r3
python examples/public_text_reader/public_text_cli.py \
  --db dist/public-r3/naikuai-kb-public.sqlite \
  --sha256 <已核对的数据库SHA-256> --question "精灵有哪些技能" --json
```

问题未指定精灵时应先追问具体对象。Python 接口是 `PublicTextReader(db_path, expected_sha256)`，提供 `lookup(question)` 和 `close()`，也可使用 `with` 关闭连接。完整参数和分页方式见 [示例说明](../examples/public_text_reader/README.md)。

## 证据与边界

- 公开社区摘要正文只进入 `conditional`，答复保留来源、版本和适用条件；不把它当作当前配置或游戏实测。
- 同名职业、精灵和副本 NPC 按类型区分；人数、难度或层数未确定时先澄清。
- 历史、未上线、未完成及删除线内容不能作为当前攻略；截取不完整须明示。
- 明确数值求值、未解码字段和配置冲突由调用方的结构化检索处理，不能用摘要补猜。
- 文件摘要、公开范围、`facts=0` 和只读连接是输入检查；它们不证明所有知识正确，也不证明 QQ 最终回答通过。

此示例不修改现有 r3 公开快照、词条审核状态、计分规则或机器人配置。合并代码后仍须由机器人项目独立验证接入效果。
