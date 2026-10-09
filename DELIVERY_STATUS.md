# 交付状态

更新日期：2026-10-09，北京时间。原 ChatGPT 对话及原 ZIP 不可访问，本项目已在当前工作区重新构建。用户已授权公开部署，GitHub 授权已完成。

仓库：https://github.com/fande139988604-de/a-share-etf-assistant

公开 JSON：https://fande139988604-de.github.io/a-share-etf-assistant/latest.json

OpenAPI：https://fande139988604-de.github.io/a-share-etf-assistant/openapi.json

| 项目 | 状态 | 证据/限制 |
|---|---|---|
| Python + AKShare 实现 | 已完成 | AKShare 1.19.1、Python 3.12.14；源码、固定依赖和运行脚本均已交付 |
| 身份、交易日、来源时间及新鲜度校验 | 已验证 | 本机和 Linux 云端各 45 项离线测试通过；reports/offline-tests.xml、reports/linux-ci/offline-ci.xml |
| 真实交易日历 | 已验证 | 2026-10-09 为交易日，日历覆盖至 2026-12-31 |
| 两个真实指数分钟接口 | 本机及云端已验证 | 000510、399673 同一响应代码及名称核验通过；本机各取得 1205 条分钟记录；云端证据见 reports/cloud-live-report.json |
| 当天 14:30 记录 | 已取得历史记录 | 2026-10-09 14:30：中证 A500 为 5302.96 点，创业板 50 为 3146.50 点；本机测试于 16:50 进行，不能称为当前实时价格 |
| 14:30 现场、五分钟内采集 | 未验证 | 所有真实网络实测均发生在收盘后；需以未来交易日现场运行证明，fresh_1430_snapshot_verified 仍为 false |
| 收盘后拒绝过期数据 | 本机及云端已验证 | public/latest.json 为 unavailable，价格为 null，两个指数均为 stale_data |
| GitHub 仓库及 Actions | 已部署并启用 | main 分支；工作流 state=active；北京时间周一至周五 14:23 启动预热，等待 14:30，实际日历排除休市 |
| 云端实际执行及发布 | 已验证 | reports/deployment-run.json；真实采集、JSON 提交、匿名读取和 Pages 发布成功；因收盘后行情过期，采集任务按设计标记失败，整个运行显示 failure |
| 公开 HTTPS URL | 已验证 | reports/public-pages-url.json：HTTP 200、application/json、JSON Schema 通过、与发布文件完全一致，读取时行情不可用 |
| 首次按日程自动运行 | 未验证 | 已验证手动触发的云端执行；尚未取得未来交易日定时触发且新鲜度通过的证据 |
| ChatGPT 浏览工具读取 | 未验证 | 对实际 Pages JSON 的本次请求返回“not accessible via this tool”；普通匿名 HTTPS 成功不能替代 ChatGPT 工具实测 |
| ChatGPT OpenAPI | 已生成，实际 Action 未测试 | public/openapi.json 使用真实 Pages 地址；须在实际账号中导入并测试 getIndexSnapshot |
| ChatGPT 云端定时任务 | 未创建 | 当前可调用的 Codex 自动化只支持本机执行，不能满足电脑关机后的手机提醒；浏览器控制工具未能连接 |
| 手机通知 | 未验证 | 详见 PHONE_SETUP.md；必须单独核验手机实际收到通知 |
| 本机 Windows 定时任务 | 备用脚本已提供，未激活 | 不依赖此方案完成电脑关机后的云端采集 |

Linux CI：https://github.com/fande139988604-de/a-share-etf-assistant/actions/runs/37911393497

云端采集与 Pages 部署：https://github.com/fande139988604-de/a-share-etf-assistant/actions/runs/37911389310

GitHub 定时工作流可能延迟或被跳过，免费行情源也可能失败。因此本项目会明确发布失败状态，无法保证每天恰好 14:30 成功。读取时必须重新核验日期、身份和 300 秒新鲜度。JSON 是定时快照，不是全天实时 API；本项目没有买点策略，signal 为 null。

项目没有包含密码、个人 Token 或 Cookies。云端提交使用 GitHub 内置 Token，Pages 使用平台部署权限。公开行情原始证据保存在本地 ZIP；公开仓库仅保留必要报告。

下一步只剩 ChatGPT 账号中的实际读取及通知测试，以及未来交易日 14:30 现场验证。最短操作路径及可复制任务文本见 PHONE_SETUP.md。
