# workflow

IsaacHuo 个人仓库的自动维护中枢。所有 loop 都跑在本仓库的 GitHub Actions 里，通过一个 GitHub App 跨仓库操作
[maptoposter](https://github.com/IsaacHuo/maptoposter)、[Myleafy](https://github.com/IsaacHuo/Myleafy)、
[familiar](https://github.com/IsaacHuo/familiar)。模型走 OpenCode Go 订阅的 `deepseek-v4.1-flash`。

## Loop 一览

| workflow | 周期 | 做什么 |
|---|---|---|
| `issue-loop.yml` | 每小时 | 分诊新 issue：值得修 / 不修复（附理由，关闭）/ 需补信息 / 咨询 / 中转站广告（固定话术）|
| `pr-loop.yml` | 每小时 | 无凭证沙箱里拉 PR 代码实际跑 → 模型逐行审查 → 关闭垃圾 PR / 要求修改 / 标记待批准 |
| `approval-loop.yml` | 每 10 分钟 | 执行你在 PR / issue 下评论的 `/approve`、`/reject` |
| `fix-loop.yml` | 每 3 小时 | 对已接受的 issue 用 opencode 写修复，开 `bot/issue-N-*` PR（再走审查 + 批准）|
| `cleanup-loop.yml` | 每周一 | 体检最近合并的代码：清理 PR、可疑代码告警、经验教训 PR |
| `digest.yml` | 每天 09:00 | 在本仓库开日报 issue：24 小时动作统计 + 待你处理清单 |
| `smoke.yml` | 手动 | 验证模型 API、opencode CLI、GitHub App 三条链路 |

## 分级

自动执行：评论、打 label、关闭"不修复"和广告 issue、关闭外部的低质量 / 恶意 PR、记违规、记经验、推 `bot/*` 分支并开 PR。

需要你评论 `/approve`（只认 IsaacHuo 本人的 OWNER 评论）：合并 PR、发布 Release、拉黑账号、合并清理 PR 和经验 PR。
`/reject 理由` 拒绝；拒绝合并时理由会被记为经验教训。

发版只打 tag + GitHub Release：合并后自动维护 Release 草稿，并在本仓库开 `[发版]` 追踪 issue，你 `/approve` 后发布，
同时回复所有相关 issue / PR。Myleafy 的版本跟 App Store 走（`release.yml`），`release.mode: none`，机器人不打 tag。

## 安全边界

- 模型只输出固定 Schema 的 JSON 结论，动作由 `wf/policy.py` 按规则决定；issue / PR 里的注入文本最多只能影响一条被清洗过的评论。
- 运行外部 PR 代码的 `sandbox` job：`permissions: {}`、不引用任何 secret / var，匿名 clone。特权 job 不恢复任何 Actions cache。
- 跑 opencode 的 job 只有模型密钥、没有 GitHub 写令牌；opencode 禁用 bash / webfetch / 仓库外文件访问；补丁推送前检查禁改路径和密钥泄露。
- 机器人只信任自己账号发出的 `<!-- wf-bot:... -->` 标记；合并前核对 head SHA，审查后有新提交则作废批准。
- `tests/test_workflows.py` 守住上述 YAML 约束。
- 已知局限：PR 的测试由 PR 自己控制，"测试通过"不能证明没有恶意；恶意判断靠模型审查 + 你的 `/approve`。

## 目录

```
config/     bot.yml（全局）repos.yml（仓库与沙箱检查）policy.yml（分级阈值）replies.yml（固定话术）
            blacklist.yml（黑名单）opencode.json（opencode CLI 配置，无密钥）
prompts/    各 loop 的提示词；_shared.md 是公共规则，会注入 lessons/LESSONS.md
lessons/    经验教训，审查时注入提示词
wf/         Python 实现：github.py llm.py policy.py markers.py state.py release.py patches.py loops/
tests/      pytest
```

跨轮次状态（违规计数、体检游标、事件）在本仓库的孤立分支 `bot-state` 的 `state.json`，首次写入时自动创建。

## 首次配置

1. 创建 GitHub App（Settings → Developer settings → GitHub Apps → New）：
   - 不需要 Webhook（取消勾选 Active）。
   - Repository permissions：Contents **Read & write**，Issues **Read & write**，Pull requests **Read & write**，
     Checks **Read**，Metadata **Read**。不要给 Workflows 和 Administration。
   - 创建后生成私钥（.pem），安装到 `workflow`、`maptoposter`、`Myleafy`、`familiar` 四个仓库。
2. 本仓库 Settings → Secrets and variables → Actions：
   - Secrets：`WF_APP_PRIVATE_KEY`（.pem 全文）、`OPENCODE_API_KEY`（OpenCode Go 的 API key）。
   - Variables：`WF_APP_ID`（App ID）、`BOT_ENABLED=true`、`DRY_RUN=true`。
3. 手动跑 `smoke`，三个 job 都绿再继续。
4. `DRY_RUN=true` 下手动跑各 loop，在 job summary 里看计划动作；确认无误后把 `DRY_RUN` 改成 `false`。

开关：`BOT_ENABLED` 不是 `true` 时所有 loop 跳过；`DRY_RUN` 不是 `false` 时只读不写（告警 issue 除外）。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

## 扩展

新增仓库：在 `config/repos.yml` 加一项（checks 照抄该仓库 CI 的命令），把 App 安装到该仓库，并把仓库名加进
`.github/actions/bot-setup/action.yml` 的 `repositories`。

Discord / 推特：按现有模式各写一个 loop——拉取消息 → 先用便宜的模型调用判断是否与项目相关（不相关直接丢弃，不进入后续提示词）
→ 有价值的转成本仓库或目标仓库的 issue，复用 issue / fix 流程。Discord 回复要用 `<@用户ID>` 才能真正 at 到人。
