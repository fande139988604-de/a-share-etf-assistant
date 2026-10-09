# ChatGPT 读取与手机通知

云端采集已部署在 GitHub Actions，电脑关机也可运行。GitHub 只是本次采用的免费云端方案，并非手机通知必需的平台。

公开数据：https://fande139988604-de.github.io/a-share-etf-assistant/latest.json

OpenAPI：https://fande139988604-de.github.io/a-share-etf-assistant/openapi.json

备用数据：https://raw.githubusercontent.com/fande139988604-de/a-share-etf-assistant/main/public/latest.json

匿名 HTTPS、JSON 结构和发布内容一致性已验证。当前 Codex 浏览工具未能读取 Pages JSON，因此不能声称 ChatGPT 搜索、GPT Action 或手机任务已成功接入。

## 手机云端任务

按 PHONE_SETUP.md 在手机 ChatGPT 的支持任务功能中创建云端任务并启用推送。先做一次实际 URL 读取测试，再核验手机通知到达；若账号的任务工具无法访问该 URL，必须报告无法读取，不能用网页搜索摘要或本地缓存替代行情。

当前没有创建 ChatGPT 云端任务，也没有验证手机通知。Codex 本机自动化需要电脑及应用运行，不能用于这项关机要求。

任务入口、可用工具和时间精度取决于账号及应用版本；手机推送需要在支持的手机应用创建任务并允许通知。

官方说明：https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt

## 自定义 GPT Action（可选）

如果实际账号支持 GPT Actions，可用真实 OpenAPI 建立只读接口，不需要 OpenAI API Key。

1. 在 GPT 编辑器的 Actions 中导入上述 OpenAPI URL，或粘贴 public/openapi.json，认证选择 None。
2. 测试 getIndexSnapshot，确认返回两个规范代码、来源名称、source_timestamp 和完整状态字段。
3. 收盘后应返回 unavailable 及 quote=null。这只能验证接口可读，不能证明 14:30 新鲜采集成功。
4. 另在真实交易日 14:30 至 14:35 测试读取时新鲜度。自定义 GPT Action 的成功不会自动建立手机定时任务或推送。

https://developers.openai.com/api/docs/actions/introduction

## GPT 必须遵守的读取规则

只有 data_kind=live、status=ok、交易日状态 verified 且当日确为交易日、两个指数身份一致，才能继续检查价格。status=ok 只表示采集时有效，不能代替读取时检查。

- 中证 A500 必须是 000510，来源名称为中证A500或中证A500指数，市场标识为 1.000510 或 2.000510；创业板 50 必须是 399673，来源名称为创业板50或创业板50指数，市场标识为 0.399673。身份必须来自同一分钟响应。
- 来源日期、target_at、generated_at 和交易日必须为当日北京时间；行情时间必须来自 source_timestamp，不能用抓取时间替代。
- 读取时间必须在北京时间 14:30 至 14:35；source_timestamp 必须在当日 14:25 至 14:35；读取时间减 source_timestamp 必须为 0 至 300 秒，且没有超过 expires_at。
- 两个指数都必须通过上述校验，价格必须是正数且非空。任何字段缺失、身份错误、未来时间、午盘、过期、昨日或失败状态都只报告“当前无可验证的新鲜14:30行情”及原因。
- 日历确认休市时可以保持安静；日历未知、下载失败、任务延迟、数据过期时必须报告失败，不能猜测是否开市。

晚间查看当天档案时必须明确标为历史快照。指数点位不等于 ETF 价格；本项目 signal 始终为 null，不生成买入建议。
