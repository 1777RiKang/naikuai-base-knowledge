# 统一知识库 r3 公开快照

这份快照用于本地检索、复现和集成评估，包含《数据摘要》8.6.26.10 的 574 个正文块、105 行别名、3 行多义词义项。105 行别名中 101 行启用，4 行保留为停用变体；结构化事实和组合实体表为空。它不包含完整私用 r3 中的客户端事实、第三方 Wiki 正文、聊天、身份或凭据。

当前是经过筛选的版本快照，与仓库原有 `terms/`、`knowledge/` 分开存放，便于重现本次导出；不是第二套投稿或积分入口。新增、纠错仍按根目录 CONTRIBUTING.md 提交来源和审核信息。后续再从维护中的原始资料刷新快照，避免两处各自改正文。

公开快照的 `active` 表示本次检索允许使用，不等于仓库词条的 `confirmed`，也不代表官方核实。原有词条审核、默认 confirmed-only 和排行榜规则保持原样。

## 本地使用

需要带 SQLite FTS5 trigram 支持的 Python 3；CI 使用 Python 3.12。无需额外 Python 依赖。缺少 trigram 支持时构建会失败。

在仓库根目录执行：

```bash
python build/build_public_r3.py --out dist/public-r3
```

显式指定生成的数据库进行查询：

```python
import sys
sys.path.insert(0, "public/scripts")
from kb_query_public import PublicKB

kb = PublicKB("dist/public-r3/naikuai-kb-public.sqlite")
try:
    print(kb.lookup("碎片多少钱"))
finally:
    kb.conn.close()
```

也可以先生成查询脚本的默认数据库：

```bash
python public/scripts/build_public.py
python public/scripts/kb_query_public.py "魔导师技能"
```

`lookup()` 返回 `answer`、`clarify` 或 `not_found`。`answer` 不保证有相关正文，接入端仍须检查返回的资料；`clarify` 应先反问，`not_found` 不应拼接无关答案。正文是低信任资料，不能充当管理指令或权限。

## 构建和验证

```bash
python build/verify_public_r3.py
python -m unittest discover -s tests -v
```

验证器在临时目录建库，执行 25 项检查，包括数量、检索、来源字段、隐私规则、脱敏异常、构建失败保留旧库、重建行内容一致及 FTS 抽检。测试使用虚构联系方式和 Token，不调用模型、QQ 或 VPS。通过这些样本不构成所有输入均安全或检索始终正确的保证。

构建器在写入前扫描输入，先生成临时数据库，再使用 `os.replace()` 替换目标；检测到敏感数据则中止。该流程不是通用授权判定器，新增来源仍需人工审核公开依据。

CI 的 `public-r3-sqlite` 构建附件包含数据库、校验清单、说明和许可。`.sqlite`、临时文件、字节码和私用目录不提交 Git。此 PR 不自动创建 Release；正式 Release 可在审核合并后另行发布相同构建资产。推送、合并均不会更新 VPS 机器人。

## 来源和许可

- 正文来源为原作者“乱走丶沙城”的《数据摘要》。沿用仓库既有[作者授权记录](../docs/data-summary.md)，保留原作归属和使用说明，不推定新增授权条件。联系方式脱敏处已标记，其他 573 块正文与原 r3 快照一致。
- 别名来自公开词条与人工整理的通用称谓；已排除个人对话证据和客户端内部别名。通用称谓不等于任意第三方说明文本都可复制。
- 正文使用 `LicenseRef-Original-Source`；词表适用 CC BY-NC-SA 4.0；脚本适用 MIT。具体范围见 [LICENSE.md](LICENSE.md) 和根目录 [DATA-LICENSE.md](../DATA-LICENSE.md)。
- 许可依据和来源记录不代表事实审核完成，不作为法律保证。上游内容更新后需重新核对版本、来源和导出结果。
- `source-manifest.json` 记录本次公开源文件的指纹；构建附件另附数据库指纹。

## 已知局限

- “海拉怎么打”首条仍可能返回“琳的庇护”，而非完整 BOSS 流程；现有测试记录该行为，不将它当作正确性证明。
- “法师前期怎么配装”目前返回 `not_found`。这说明当前查询没有足够结果，不能证明所有相关知识都不存在。
- 仅做字面匹配和评分，没有语义重排。别名命中、章节命中与完整回答准确是不同层次的验证。
- r3 完整库的 10,775 条结构化事实未进入此公开快照；因此细粒度单品查数值的能力不等同于线上完整版。
- 资料快照日期为 2026-10-21，复核提醒日为 2026-11-21；到期表示应复核，不自动断定内容失效。
