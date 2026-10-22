# 日常维护说明

## 收到别人的 PR

1. 核对贡献内容、证据脱敏、知识点是否重复、自选公开贡献名是否由本人提供。
2. 未核实的词条可用 draft 合并；有争议用 disputed。只有你确认后的 confirmed 进入默认 AI 检索和贡献积分。
3. 通过 GitHub 的 Files changed 审查。需要对方修改时选择 Request changes；满意后 Approve。
4. 等待 validate 通过，解决全部 review conversation 后，使用 Squash and merge 手动合并。
5. 合并后 Actions 会生成 SQLite 产物并更新榜单。榜单取最初引入该词条的 PR 作者，不取合并者。

所有文件由仓库所有者作为 CODEOWNER 审核。新提交会使旧批准失效，需要重新查看。
外部贡献者通过 Fork 和 PR 参与，不因此获得仓库写权限。

## 自己的修改

新建分支、提交并打开 PR。你不能批准自己的 PR，因此仓库仅为所有者配置了 PR 审批例外；
validate 仍然必须成功。检查无误后可以使用 GitHub 的规则绕过入口合并自己的 PR。
规则不允许直接推送 main，不允许强推或删除 main；CI 规则没有绕过名单。
首次创建仓库的 bootstrap 提交用于建立 main，此后使用以上 PR 流程。

## 词条审核字段

- confirmed：reviewer 填维护者标识（例如 maintainer），reviewed_at 填加引号的日期。
- draft：可以暂不填写 reviewer 和 reviewed_at，供继续完善。
- disputed：必须记录提出异议的审核标识，保留争议依据，AI 默认不引用。
- deprecated：保留旧 id 和历史，不再用于默认回答与当前贡献积分。

不要把 reviewed_at、贡献者署名或审核标识写成聊天中的玩家昵称、QQ 号或联系方式。
贡献者自愿公开的贡献名是例外，仅用于署名和排行榜。

## 榜单与贡献名

一个非种子 confirmed 词条计 1 分，不按字数或 PR 数计分。
每次 main 合并后自动更新 leaderboard 分支；README 展示前 15 名，完整榜单与 JSON 保留全部。
如果贡献者提出改名，核对申请人 GitHub 数字 ID 后，接受其 display-names.json 配置 PR。

榜单图片有缓存延迟时先查看完整榜单和 Actions；工作流失败时保留上次成功版本。
可在 Actions → leaderboard → Run workflow 手动重建，不需要提供个人 token。

## 下载与接入

Actions → validate → 成功的运行 → Artifacts → glossary-sqlite。
下载需要 GitHub 登录；产物按保留期限过期，可以通过新的合并或重新运行 validate 再次生成。
按 manifest.json 验证 SHA-256，AI 查询默认只读取 confirmed，并保留来源与许可说明。
