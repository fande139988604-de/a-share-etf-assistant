# 本机读取与手机通知

GitHub 不是必需。它只是电脑关机时继续采集的一种云端方案。

本机方案：电脑开机、联网；本机 Python 采集；当前 Codex/桌面任务读取文件。Windows 定时任务只负责采集，不会自行发 ChatGPT 或手机通知。要手机提醒，还需在 ChatGPT/Codex 中保存定时任务，并在手机同一账号中开启相应通知；需要真实测试通知到达，不能仅凭脚本成功就声称推送成功。当前交付没有激活任何定时任务或通知。

官方说明：需要本机文件的桌面定时任务要求电脑与应用保持运行；网页版任务不能直接读取电脑文件。计划、工具和通知入口可能因账号而异。

- 定时任务：https://learn.chatgpt.com/docs/automations
- 通知：https://learn.chatgpt.com/docs/notifications

## 可复制给当前 Codex 的定时任务要求（尚未创建）

在本对话中每周一至周五北京时间14:28启动本机项目，等待14:30采集中证A500（000510）和创业板50（399673）。必须使用实际交易日历排除休市日。在项目目录执行 scripts/run-local.ps1 -WaitFor1430，随后读取 public/latest.json 并执行 python -m etf_assistant.cli read-local。读取时重新核验指数身份、来源日期、分钟时间戳与300秒新鲜度。交易日采集成功时报告两个指数点位、各自来源时间戳及数据年龄；失败时明确报告失败原因。休市且无异常时保持安静。不能用午盘、昨日、过期或模拟数据生成买点。任务不应发送邮件、调用外部通知服务或修改 GitHub。请确认任务已经保存，并单独验证手机任务通知是否送达。

说明：该文本只是待使用说明，不代表任务已建立。即使使用同一账号，原 ChatGPT 网页对话也不会自动获得当前本机文件，需要已连接的本机任务、上传文件或公共接口。

## 公共 HTTPS + GPT Action（可选，当前未部署）

若以后希望手机端直接向一个自定义 GPT 请求行情，可为项目部署公开 JSON 接口，再按官方 GPT Actions 流程导入 OpenAPI。此方案不需要 OpenAI API Key；GPT Actions 是否可用取决于账号。给出普通 URL 也不等于可靠地接入了每次查询，必须在 Action 测试按钮中看到实际返回。

1. 获得真实的 `https://.../public/latest.json`；先运行 `python -m etf_assistant.cli verify-url <真实URL>`。
2. 运行 `python scripts/make_openapi.py <真实URL>` 生成 `public/openapi.json`。
3. 在自定义 GPT 的 Actions 中导入生成的 schema，认证选 None；测试 `getIndexSnapshot`。
4. 对返回体核对两个代码、交易日期、source_timestamp、expires_at；保存实际测试结果。离线 schema 测试不能替代该步骤。

https://developers.openai.com/api/docs/actions/introduction

## GPT 必须遵守的读取规则

只有 `data_kind=live`、交易日状态 verified 且当日确为交易日、两个指数身份一致，才能继续检查价格。`status=ok` 表示采集时有效，不能代替读取时检查。读取时间减 source_timestamp 必须在0至300秒之间，source_timestamp 必须是当日北京时间14:25至14:35，读取本身也必须在14:30至14:35之内，且未超过 expires_at。任何字段缺失、未来时间、过期或状态失败都只输出“当前无可验证的新鲜14:30行情”，说明原因，不能给买点。

若用户晚上查看当天14:30档案，可以明确标为历史快照；不把它称为实时行情。本项目没有定义交易策略，`signal` 始终为 null。指数点位不等于 ETF 价格，也不意味着可按该点位交易。
