# QQ 机器人 r3 接入与测试

可选的 [QQ 接入示例](../examples/qq_bot_r3/README.md) 提供只读检索、本人私聊资料注入、歧义阻断、授权群影子对比及合成回归测试。它不包含真实数据库、API 凭据或 QQ 会话，也不会改变本仓库现有 glossary/knowledge 的 confirmed-only 默认规则。

r3 使用独立 schema，不能把本仓库构建出的 glossary.sqlite 直接改名后交给它，也不能把 r3 active 状态解释为本仓库 confirmed。未来若增加两种 schema 的适配，应单独验证审核状态、来源许可、义项和版本映射。

```bash
python examples/qq_bot_r3/run_tests.py
```

公开 CI 在临时目录构造纯合成数据；真实资料必须由部署者在私有环境另行准备。部署观察、合成测试和手机实测各有不同范围，不能将“收到回复”写成“游戏数值正确”或“长期在线已验收”。

重点评测问题登记于 [evaluation_cases.json](../examples/qq_bot_r3/evaluation_cases.json)，已知未解决的问题与来源边界见示例 README。本提交不新增词条、不改变审核状态，不产生贡献积分。
