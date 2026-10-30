# 按类型检索公开正文

`examples/public_text_reader/` 提供离线、确定性的公开正文读取示例，供机器人区分职业玩法、精灵技能、精灵系统章节、副本资料和宝石子章节。它读取独立的公开 SQLite，不读取私用客户端数据库、聊天记录或模型凭据，也不调用模型。

## 使用

先按 [Release 与独立缓存](releases.md) 下载并验证公开快照，或从本仓库可信源代码本地构建：

```bash
python build/build_public_r3.py --out dist/public-r3
python examples/public_text_reader/public_text_cli.py \
  --db dist/public-r3/naikuai-kb-public.sqlite \
  --sha256 <已核对的数据库SHA-256> --question "精灵有哪些技能" --json
```

`--sha256` 可省略；省略时从同目录 `manifest.json` 读取并核对。问题未指定精灵时应先追问具体对象。Python 接口是 `PublicTextReader(db_path, expected_sha256)`，提供 `lookup(question)` 和 `close()`，也可使用 `with` 关闭连接。完整参数、分页方式与输出契约见 [示例说明](../examples/public_text_reader/README.md)。

## 证据与边界

- 公开社区摘要正文只进入 `conditional`，答复保留来源、版本和适用条件；不把它当作当前配置或游戏实测。
- 同名职业、精灵和副本 NPC 按类型区分；人数、难度或层数未确定时先澄清。
- 精灵系统章节（如 `6.1` 精灵装备）使用独立 `elf_system` 路由；概述只从真实子章节标题、表头字段和套装行生成短结论，不推断“给精灵/给玩家”等源文未说明的作用关系。
- 宝石按 `2.6.x` 实际子章节区分效果、升级、切割和镶嵌；具名宝石只摘对应表行及所属分组，不把同表其他职业宝石当答案，表内没有该行时明确来源边界。
- 具名副本问“有什么技能”时走攻略/机制段落，返回阶段、普通攻击、召唤、点名等正文，不把入口人数/属性面板当作技能答案。
- 历史、未上线、未完成及删除线内容不能作为当前攻略；截取不完整须明示。
- `unknown` 范围证据绑定本次打开的 SHA/manifest、`kb_version`、快照日期和实际检查过的文档 locator，不在无法定位时伪造来源。
- 明确数值求值、未解码字段和配置冲突由调用方的结构化检索处理，不能用摘要补猜。
- 文件摘要、公开范围、`facts=0` 和只读连接是输入检查；它们不证明所有知识正确，也不证明 QQ 最终回答通过。

真实库测试默认读取构建产物 `dist/public-r3/naikuai-kb-public.sqlite`，也可用 `PUBLIC_TEXT_TEST_DB` 指向已核对的公开缓存。CI 在构建公开 SQLite 后单独运行 `tests/test_public_text_reader.py`，真实库用例会实际执行。

此示例不修改现有 r3 公开快照、词条审核状态、计分规则或机器人配置。合并代码后仍须由机器人项目独立验证接入效果；本示例不声称已完成完整 QQ 部署或覆盖全部知识。
