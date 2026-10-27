# Release 与机器人公开资料缓存

## 创建版本

在本 PR 审核合并到 main 后，从 Actions 手动运行 `release` 工作流，version 必须等于仓库 VERSION（本轮 0.4.0）。默认创建草稿；只有手动勾选 publish 才直接公开。工作流拒绝从其他分支发布，不自动在 PR 合并时执行。

流程会运行原校验、隐私检查、全部单元测试和 30 道固定检索题，再构建数据库、来源指纹及 SHA-256 清单。发布附件只有公开 r3；不读取 VPS，也不读取私用资料目录。首次发布前仍需审阅内容和既有作者授权记录。

发布前还会运行离线预检：

```bash
python build/release_preflight.py \
  --directory dist/public-r3 \
  --evaluation dist/retrieval-evaluation.json \
  --output dist/release-preflight.json
```

工作流已在内置构建与评测之后调用它，非零退出会阻止创建 Release。预检只读本地产物：要求产物目录的直接成员恰好为 manifest 与 4 个白名单资产（无额外文件、目录或符号链接），核对版本、manifest 契约、资产大小与 SHA-256、公开元数据、`facts=0`、可公开标记，并验证受信流水线评测报告的完整性及其与当前数据库、题集的实际字节绑定。预检不独立重跑评测，也不据此证明模型或 QQ 效果。检查项与阶段证据见 [发布检查清单](release-checklist.md)。

Release 说明提供 `manifest.json` 的 SHA-256。它用于固定本次下载内容；不要从未经核对的聊天或第三方链接复制一个摘要作为信任依据。

## 下载到独立缓存

仓库根目录执行（把 MANIFEST_SHA256 替换为已核对的 Release 说明中的值）：

```bash
python build/stage_public_release.py \
  https://github.com/1777RiKang/naikuai-base-knowledge/releases/download/v0.4.0/manifest.json \
  --manifest-sha256 MANIFEST_SHA256
```

默认缓存为 `.local/public-r3-releases/0.4.0/`。脚本只接受固定仓库的 HTTPS Release 路径，并核对版本、文件白名单、大小、摘要、SQLite 完整性和公开范围。下载到临时目录，全部验证后才成为一个新版本目录；同版本内容不同会拒绝覆盖。

这条命令不会修改 `.env`、机器人配置、已有完整 r3 或服务状态。缓存成功不表示机器人已经切换。没有发布 Release 时，远程下载无法验收；本轮只以本机资产和损坏下载夹具验证更新逻辑。

## 接入完整私用库

`public/scripts/release_cache.py` 提供可选 `ReleaseLayer`，应在现有本人/群权限校验之后调用。它只返回检索结果，不调用模型或 QQ。

```python
import sys
sys.path.insert(0, "public/scripts")
from release_cache import ReleaseLayer

reader = ReleaseLayer(".local/public-r3-releases/0.4.0", expected_manifest_sha256)
try:
    result = reader.lookup(question, private_lookup=existing_private_lookup)
finally:
    reader.close()
```

`existing_private_lookup` 是现有完整库的只读回调，返回 r3 原始结构（action、facts、chunks 等），不是只返回 material 的提示词格式化包装。公开消歧直接返回，不调用私用回调；其他情况保留私用事实优先权，公开库缺失的镶嵌模型等由私用回调补充。职业玩法优先使用公开职业章节，避免同名 NPC 数值混入。

上层仍须做到 clarify 不调模型、空资料不编答案、资料不是指令、会话权限及记忆隔离保持原样。不得用公开版替换完整私用数据库。VPS 适配和手机测试是后续独立验收，当前不将 M1 的“机器人已读 Release”标为完成。

SQLite 读取器应在同一线程创建、使用和关闭。异步框架可把完整的一次读取操作放进工作线程；不要在主线程构造后跨线程复用连接。

回退时停用可选 ReleaseLayer，恢复原私用回调；不要删除私用库。旧的已验证公开缓存也保留，便于人工指定版本回退。
