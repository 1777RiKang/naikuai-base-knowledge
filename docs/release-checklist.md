# 发布检查清单

本清单用于首次及后续公开 Release 的离线证据核对。它检查**本机构建产物**，不下载 Release、不执行下载内容、不读取凭据，也不判断授权范围。

## 何时运行

- 本地构建公开 r3 之后、创建 Release 之前。
- CI 的 `release` 工作流会自动运行；非零退出会阻断 `gh release create`。
- 只想复核已有产物时，也可单独运行，不触碰仓库数据。

## 命令

```bash
python build/build_public_r3.py --out dist/public-r3
python build/evaluate_public_r3.py dist/public-r3/naikuai-kb-public.sqlite \
  --output dist/retrieval-evaluation.json
python build/release_preflight.py \
  --directory dist/public-r3 \
  --evaluation dist/retrieval-evaluation.json \
  --output dist/release-preflight.json
```

可选参数：`--version-file` 指定版本文件，`--cases` 指定固定题集，默认分别为仓库 `VERSION` 与 `eval/retrieval_cases.json`。

## 检查项

| 检查 | 通过条件 | 失败含义 |
| --- | --- | --- |
| `version` | `VERSION` 非空 | 版本文件缺失或为空 |
| `bundle-layout` | 产物目录的直接成员恰好为 `manifest.json` 与 `release_cache.ASSETS` 中的 4 个资产，且全部是普通文件，没有额外文件、子目录或符号链接 | 目录含会被通配上传的多余内容，或缺少必需资产 |
| `manifest-read` | 产物目录存在 `manifest.json` | 未构建或目录错误 |
| `manifest-contract` | schema、仓库、版本格式、4 个资产白名单、大小与摘要格式符合下载器契约 | 产物不是本项目公开包，或清单被改写 |
| `version-match` | manifest 的 `release_version` 等于 `VERSION` | 代码与产物版本不一致 |
| `assets` | 每个资产存在、非符号链接、实际大小与 SHA-256 与清单一致 | 资产被替换、截断或清单过期 |
| `public-database` | 复用下载器校验（大小、摘要、SQLite 完整性、公开 schema、`facts=0`），并额外核对 `kb_version`、块与别名全部 `publishable`、无 `sensitive` 块 | 混入私用事实、不可公开内容或元数据不符 |
| `evaluation` | 固定题集全部实际运行，结果 ID 与题集一一对应，逐条通过，报告计数完整；报告的 `database_sha256`/`cases_sha256` 与当前产物数据库及题集的实际字节一致 | 评测缺失、少跑、ID 不匹配、存在失败用例，或报告来自旧数据库/旧题集 |

资产白名单只来自 `public/scripts/release_cache.py` 的 `ASSETS`，预检不维护第二份名单；发布附件范围仍与下载器一致。

`evaluation` 检查的是**受信流水线评测报告**的完整性与输入绑定，不是独立重跑，也不把读取报告说成独立证明模型效果或 QQ 实际回答质量。

输出为机器可读 JSON，包含版本、manifest 与数据库摘要、实际资产大小与 SHA-256、块/别名/事实计数、评测通过数、逐项检查与失败原因。

## 通过之后

预检通过只证明本地产物自洽，仍须依次完成：

1. 在 `main` 上手动触发 `release`，保持默认草稿，核对资产范围未变。
2. 用 Release 说明中的 manifest SHA-256 运行 `build/stage_public_release.py`，完成一次真实下载验收。
3. 由维护者按既有流程确认内容与授权记录；预检不改变授权状态。

## 限制

- 不验证 GitHub 网络、Actions 权限、产物保留期或草稿/正式发布行为。
- 不比较 CI（Ubuntu + Python 3.12）与本地构建的字节一致性；以实际发布产物的 SHA-256 为准。
- 不验证机器人接入、VPS、手机端或最终回答质量。
- 不判断《数据摘要》与别名的授权充分性；授权原文与来源补证仍是独立的记录完整性待办。
- 公开库的 `active` 只表示检索可用，不等于根词条 `confirmed`，预检也不会改写审核状态。
