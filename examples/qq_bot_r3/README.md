# QQ 机器人 r3 接入示例

这是 AstrBot / OneBot 试运行链路的源码参考：本人私聊使用 r3 资料回答，授权群保留旧库回答并异步运行新库影子检索。代码不包含运行配置、QQ 身份、模型凭据、聊天记录或真实数据库，也不会自动连接 QQ 或部署服务器。

本示例与仓库根目录 `build/query.py`、`glossary.sqlite`、`knowledge.sqlite` 独立。公开词库的默认 confirmed-only 规则不变；r3 的 active/candidate/legacy 与本仓库的审核状态不是同一套 schema，不能直接互换或把 active 当作 confirmed。

## 文件

- `cloud/napcat_trial_plugin/main.py`：已有权限门禁之后的 AstrBot 接入，包括私聊资料注入和歧义时停止事件。
- `cloud/unified_private.py`：只读检索、资料格式化与私聊提示规则。
- `cloud/unified_shadow.py`：候选库校验、只读查询、SQL 进度预算、有界后台队列和私有轮转日志。
- `cloud/naiwa_store.py`、`napcat_guard.py`、`napcat_trial_bootstrap.py`：对应的记忆、权限和隔离启动源码参考；`build_game_kb.py`、`v2_lookup.py` 是旧库兼容依赖。
- `unified_r3/kb_query.py`：r3 参考检索器，强制以 SQLite 只读 URI 打开库。
- `unified_r3/build_unified.py`、`refine.py`、`compare_builds.py`：构建、状态修正和逻辑重建比对脚本；需要操作者另行准备有权使用的完整输入包。
- `unified_r3/schema.sql`：只有结构，没有生产行数据。
- `make_fixture.py`、`run_tests.py`：独立生成合成测试库并运行测试，不读取任何生产文件。
- `evaluation_cases.json`：自然问法、人工评估标准与尚未修复的问题，不是已确认的游戏知识。

## 无凭据、无真实数据库的验证

需要 Python 3.12+，SQLite 支持 FTS5 trigram。仓库 CI 使用 Python 3.12，集成测试本身只用标准库，不需要 AstrBot、QQ 账号或 API Key：

```bash
python examples/qq_bot_r3/run_tests.py
```

测试库在系统临时目录生成并在结束后清理。合成库中的名称仅为测试输入，属性和正文明确是人为占位，不代表游戏数值、攻略或授权资料。测试覆盖只读、过滤、歧义阻断、超长事实预算、权限、队列过载、日志隐私、轮转和请求隔离。公开 CI 使用合成库；它不复现私有生产库全部内容，也不能衡量真实群问法召回率。

需要单独观察输出时，可在仓库忽略目录内生成样例：

```bash
python examples/qq_bot_r3/make_fixture.py .local/r3-fixture/unified-r3
python .local/r3-fixture/unified-r3/kb_query.py "宝石镶嵌规则"
```

## 接入已有机器人

这些文件是源码快照，不是“一键上线”安装器。AstrBot 钩子与原 OneBot 门禁适配绑定；示例中的固定实例根路径是隔离检查的一部分。迁移到其他实例前，应明确根路径、私有 policy 文件、插件安装位置、进程属主及已有回复所有权，不直接删除路径检查。

1. 保留前置门禁，只让已授权的本人私聊进入私聊查库分支。群聊继续由 chat_plus 管理，不能再由另一个处理器重复回复。
2. 操作者在私有目录放入经审核的 r3 数据库、查询代码和 SHA-256 receipt；仓库不提供真实库或完整输入包的下载地址。
3. 资料结果保留类型、版本和未核验限定；资料中的文字不授予任何工具或管理权限。
4. `action=clarify` 直接反问并停止 AstrBot 事件，不进入生成模型调用；不可读库也停止并给出固定提示。
5. 私聊保留原记忆、图片和模型配置。影子结果不进入正式群聊提示词，不新增逐人额度限制。
6. 部署前建立回滚点，只重启目标 AstrBot 实例；本示例没有开机自启或修改服务器的脚本。

模型凭据只放操作者自己的私有配置，不写入仓库。真实数据、聊天和部署记录也不放在示例目录。影子队列最多 8 个任务，日志最多 3 份、每份 2 MiB；原问题只短暂留在内存，日志只存加盐摘要和知识库检索结果。采样期限与留存期限由私有配置明确指定，清理只在进程运行或下次启动时执行。

## 构建与许可

完整构建命令为：

```bash
python examples/qq_bot_r3/unified_r3/build_unified.py /path/to/authorized-input /path/to/private-output
```

脚本末尾调用 refine.py。输出可能包含 Wiki、客户端派生内容和人工标注证据，必须由操作者另行管理。`publishable`、`license_basis` 和脚本内来源分级是工程元数据，不构成再分发授权或游戏事实认证；不要因为标记可公开就上传输出。代码按仓库 MIT 许可；外部资料权利不随代码许可转移。

逻辑重建比对：

```bash
python examples/qq_bot_r3/unified_r3/compare_builds.py /path/to/build-a /path/to/build-b
```

比对依据是稳定 ID、表内容、状态、固定问法及导出文件，而不是要求 SQLite 文件 SHA 相同。完整输入包不在仓库，CI 只验证合成样例，不宣称重建了真实生产库。

## 已验证与待改进

记录日期：2026-10-24。部署时本地相关回归 111 项通过，服务器 Python 3.12 的私聊、群聊及门禁测试 64 项通过；安装版 AstrBot 4.29.1 钩子合成测试验证了歧义时停止生成边界。操作者另已确认“宝石镶嵌规则”和“碎片多少钱”两条真实私聊均正常收到回复；未抓取上游 HTTP 请求计数。

以下自然问法仍有问题：宝石口语问法的意图澄清不充分；无尽三人本混入其他世界副本；猎魔人职业与同名 NPC 未区分。检索裁剪也可能遗漏关键动作或留下仅标题的块。本提交将这些问题登记为评测用例，没有把它们标为已修复，没有加入多轮工具查库，也没有修改任何词条审核状态。

正式切换群聊、真实问法准确率、长时间稳定性、服务器重启后的恢复与回滚实跑不在本提交的验证范围。
