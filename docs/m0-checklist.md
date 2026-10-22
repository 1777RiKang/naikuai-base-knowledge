# M0 验收清单

## 仓库

- [x] 目录结构、README、CONTRIBUTING、许可证
- [x] `schema/term.schema.json`（Draft 2020-12）
- [x] 校验脚本：schema、id/文件名、别名冲突、关系完整性、证据脱敏
- [x] 构建脚本：YAML → SQLite + manifest（sha256、条目数）
- [x] 单元测试：校验、构建、别名解析、组合拆分、bigram 索引
- [x] GitHub Actions：PR 校验 + 产物上传
- [x] PR / Issue 模板、维护者 CODEOWNERS

## 数据

- [x] 51 条人工标注迁移为 50 个词条文件
- [x] 3 组同物异写合并为别名（黑仗 / 碎魂 / 飞行精灵石）
- [x] 组合词「精灵飞行」拆出精灵石、飞行契约两个 draft 条目
- [x] 来源全部标为 `community`，与 nkwiki 正文分离
- [x] 证据去昵称、去长数字，标记 `redacted: true`

## 待办（M1）

- [x] 由仓库所有者负责审核，维护者自己的 PR 可自行合并；所有 PR 必须通过 CI
- [ ] 补齐「铁块」「切割器」「精灵石」「飞行契约」释义（现为 draft）
- [ ] 黑奶2 / 黑杖 与 nkwiki「黑暗信使法杖」条目建立跨库引用
- [ ] 机器人改为读取 Release 产物，按 provenance 标注来源
- [ ] 影子测试接入 CI，固定问句集防止召回回退

## 公开协作与贡献榜

- [x] 未核实内容可以 draft/disputed 合并，AI 默认只用 confirmed
- [x] 贡献榜按非种子 confirmed 词条计分，归原始 PR 作者，自选公开贡献名
- [x] 榜单在独立产物分支更新，README 展示，不向 main 自动写入
- [x] SQLite 完整溯源、释义检索与默认状态过滤
- [ ] GitHub 首次远端 CI、产物下载和分支规则验收
