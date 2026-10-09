# 交付状态

更新日期：2026-10-09，北京时间。原 ZIP 不可取得，项目已重新构建。GitHub 公开仓库及云端采集已经部署；用户本次选择先分析两个指数的风险信号，并授权把提醒设置请求发送到原 ChatGPT 对话。

仓库：https://github.com/fande139988604-de/a-share-etf-assistant

数据：https://fande139988604-de.github.io/a-share-etf-assistant/latest.json

完整 JSON 网页视图：https://fande139988604-de.github.io/a-share-etf-assistant/snapshot.html

OpenAPI：https://fande139988604-de.github.io/a-share-etf-assistant/openapi.json

| 项目 | 状态 | 证据与实际限制 |
|---|---|---|
| Python + AKShare、严格行情校验 | 已完成 | AKShare 1.19.1；交易日、同响应身份、来源时间、五分钟新鲜度、午盘拒绝规则保持生效 |
| 本机与 Linux 测试 | 各 55 项通过 | reports/offline-tests.xml、reports/linux-ci/offline-ci.xml；新增风险历史完整性、风险时效及 HTML 完整 JSON 转义测试 |
| 真实两个指数分钟接口 | 本机与云端已验证 | 000510 中证A500、399673 创业板50；reports/cloud-live-report.json，原始公开行情证据保存在本地 ZIP |
| 指数风险观察 | 已部署及离线验证 | 回撤 1.5%、20 分钟下跌 0.8%、跌破上午最低分钟收盘点 0.3%；初始阈值未经回测，无持仓卖出指令，详见 RISK_RULES.md |
| 收盘后校验 | 最新云端已验证 | 真实采集发生在收盘后，行情 stale_data、quote=null；风险 unavailable、metrics=null，无风险结论 |
| GitHub Actions 与 Pages | 已部署并启用 | 周一至周五北京时间 14:23 预热、等待 14:30，真实日历排除休市；电脑关机不影响云端执行 |
| 最新云端实际执行 | 已验证 | reports/reader-deployment-run.json；采集和发布执行成功，因过期行情主动标失败，整体 conclusion=failure；Pages job=success |
| 公开 JSON、HTML 与 OpenAPI | 已验证 | 匿名 HTTP200，JSON Schema 通过，与发布文件匹配；HTML 中的完整 JSON 与同次快照一致，OpenAPI 包含风险结构 |
| 首次交易日按日程 14:30 新鲜采集 | 未验证 | 实际测试均在收盘后，fresh_1430_snapshot_verified=false；需下一真实交易日现场核验 |
| ChatGPT 云端提醒保存 | 用户在手机确认 | 名称“14:30指数行情与风险提醒”；周一至周五 14:33、Asia/Shanghai；截图显示更新原任务并启用，下次报告为 10 月 12 日。Codex 未直接读取任务对象 |
| 手机 App 与系统通知权限 | 用户确认已开启 | 用户明确回复已开启两处通知；该确认不能替代实际任务推送到达 |
| ChatGPT 实际 JSON/HTML 读取 | 未通过 | 手机截图报告 DisabledError；本次 Codex 浏览工具也无法读取两个地址。普通匿名 HTTPS 成功不能声称 ChatGPT 已接入 |
| 手机任务推送到达 | 未验证 | 已向原对话请求在工具支持时立即运行现有任务测试，不重复创建；尚无实际送达证据 |
| 自定义 GPT Action | 未配置/测试 | 提供真实 OpenAPI，可选手动接入，不等于已经能被云端定时任务调用 |
| 本机定时采集 | 未启用 | 保留备用脚本，不依赖此方案实现电脑关机后的采集 |

最新 Linux CI：https://github.com/fande139988604-de/a-share-etf-assistant/actions/runs/37936314539

最新云端采集与发布：https://github.com/fande139988604-de/a-share-etf-assistant/actions/runs/37936319187

两个风险观察结果均需先通过读取时的新鲜度校验。no_trigger 只表示本项目三个初始阈值未触发，不代表安全；数据不足或过期只报告无法分析。signal 与 sell_order 为 null，不执行交易。

定时工作流及免费数据源不能保证每个交易日恰好 14:30 成功。延迟或失败会明确输出不可用，不能用午盘或旧数据补成有效行情。JSON 与 HTML 均可能被缓存，消费者必须核验时间，不以 HTTP200 代替新鲜度。

当前剩余事项：解决 ChatGPT 任务的实际读取能力错误；验证手机实际收到任务通知；在未来交易日完成 14:30 新鲜采集实测。请求已保存在 CLOUD_REMINDER_PROMPT.txt；手机状态见 reports/cloud-reminder-status.json。

Computer Use 和浏览器控制均因本机工具运行错误未能连接。没有索取或公开密码、个人 Token、Cookies、持仓信息或手机截图；仓库提交使用 GitHub 平台授权。
