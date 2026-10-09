# A股ETF实时行情助手

获取两个目标指数在北京时间14:30附近的分钟数据，并严格验证是否可用。程序使用 Python + AKShare，不要求行情 API 密钥，也不要求 OpenAI API Key。它获取指数点位，不把 ETF 价格、创业板指399006或中证500000905代替目标指数。

## 当前结果

- 已完成代码；新增风险分析后本机 54 项离线测试通过，最新 Linux 云端结果见 DELIVERY_STATUS.md。
- 2026-10-09真实AKShare测试：交易日历覆盖1990-12-19至2026-12-31；中证A500000510和创业板50399673代码、名称均核验成功；均返回当天14:30分钟记录。详见 reports/live-2026-10-09-final/report.json。
- 实测在收盘后进行，14:30记录只能证明真实分钟数据可取得，不能证明14:30采集时五分钟内新鲜。fresh_1430_snapshot_verified=false是正确结果。
- GitHub Actions 已部署并启用，云端实际采集、JSON 发布和 Pages 部署已验证。公开数据：https://fande139988604-de.github.io/a-share-etf-assistant/latest.json 。ChatGPT 实际调用、手机通知及首次定时触发的 14:30 现场验证尚未完成；详见 DELIVERY_STATUS.md 和 PHONE_SETUP.md。

## 电脑关机也能采集

使用公开GitHub仓库的免费GitHub Actions。北京时间每周一至周五14:23启动，安装环境后等到14:30采集。运行时用真实交易日历跳过节假日，不能以工作日代替交易日。任务延迟到14:35以后会输出不可用，不伪造时间。GitHub官方不保证定时任务准点；若要严格的分钟级服务保证，需要其他常开服务器及调度服务。

程序会把结构化JSON提交到仓库 public/latest.json，提供匿名可读HTTPS地址，并发布 public/verification.json 和可导入的 public/openapi.json。使用 GitHub 内置 GITHUB_TOKEN 提交数据，采集仅授予 contents:write；Pages 发布单独授予 contents:read、pages:write 和 id-token:write。不需要保存个人 Token。公开JSON是定时快照，并非全天实时API。免费托管额度及服务条款以GitHub当前账号为准。

手机消息另由ChatGPT云端定时任务负责读取该URL和发通知。GitHub工作流本身不会发送每日行情推送；部署成功不等于手机通知成功。CHATGPT_INSTRUCTIONS.md提供读取规则，需在实际账号中测试。电脑关机时不能依赖“本机桌面任务”。

## 本机最短操作

在当前项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe -m etf_assistant.cli live-test
.\.venv\Scripts\python.exe -m etf_assistant.cli collect
```

setup.ps1优先使用Codex已安装的内置Python；也可传入-PythonExecutable指定Python3.11以上路径。依赖安装在本项目.venv内。setup只安装及测试，不创建定时任务、不登录、不部署、不推送。

若14:28开始，采集命令可加 --wait --retry-window；如果现在已经收盘，返回unavailable和退出码2是正常的严格校验结果。交易日历无法获取或覆盖不足则拒绝采集，状态calendar_unverified。

读取并重新核验本地JSON：

```powershell
.\.venv\Scripts\python.exe -m etf_assistant.cli read-local public/latest.json
```

本机定时采集是可选方案。只有用户明确决定使用本机方案后，才运行 scripts/register-local-task.ps1。该脚本不会创建手机通知。

## 校验规则

1. AKShare新浪交易日历必须覆盖当天；无法确认则关闭有效输出。周末及官方休市日不采集。
2. 中证A500：规范代码000510；明确市场候选1.000510、2.000510。创业板50：399673，0.399673。每次从同一分钟响应取得code、name核验；不能只用请求参数或配置名称作为身份证据。
3. 行情时间必须来自分钟数据时间列；不以抓取时间补造数据时间。保存来源、分钟周期、收取时间、响应摘要及原始证据。
4. 来源日期必须是当天，时间不得在未来；允许14:25至14:35附近记录。采集及读取必须在14:30至14:35之内，年龄须为0至300秒。午盘、昨日、过期和错指数一律不出价格及买点。
5. 优先1分钟接口，窗口内可回退5分钟接口；两者都核验原始响应身份和真实时间戳。网络调用有连接/读取超时及独立子进程总时限。
6. 免费源有时拒绝默认TLS客户端，使用curl_cffi兼容传输重试，仍保持HTTPS证书验证；没有使用verify=False。
7. 成功/失败都会原子覆盖latest.json；离线测试数据永远标记offline_fixture且不可作为实时数据。读取端必须重新计算新鲜度，不能只看status=ok。

## JSON内容

schema定义在schemas/snapshot.schema.json。主要字段：

| 字段 | 含义 |
|---|---|
| generated_at | 完成采集及校验的实际时间 |
| target_at | 当天北京时间14:30 |
| live_usable_at_collection | 仅表示采集时通过全部校验 |
| expires_at | 该快照最迟有效时间 |
| trading_day | 日历源、覆盖范围及交易日状态 |
| indices[].source | 同一响应中的代码、名称、市场、接口和分钟周期 |
| indices[].source_timestamp | 数据源分钟时间，非请求时间 |
| indices[].age_seconds | 采集时数据年龄，读取时必须重算 |
| indices[].quote | 校验通过的指数点位，失败为null |
| signal | null；尚未定义任何买点策略 |
| risk_analysis | 两个指数的日内回撤、20分钟走势、上午低点跌破观察；数据不全或过期时 unavailable |

## 风险提醒

用户选择先分析两个指数的风险信号。默认观察阈值是回撤至少1.5%、20分钟下跌至少0.8%、跌破上午最低分钟收盘点至少0.3%。一个触发项为watch，至少两个为elevated，无触发为no_trigger；无触发不代表安全。规则尚未回测，不能直接当作卖出指令，sell_order为null。完整定义、数据覆盖要求与局限见RISK_RULES.md。

云端提醒设置请求见CLOUD_REMINDER_PROMPT.txt。提醒保存、实际URL读取和手机推送是三个单独的验证步骤，状态见DELIVERY_STATUS.md。

## 部署结果与复用路径

本项目已经部署；以下步骤仅供以后在其他仓库复用。当前仓库为 https://github.com/fande139988604-de/a-share-etf-assistant 。主要数据地址为 https://fande139988604-de.github.io/a-share-etf-assistant/latest.json 。

1. 在本人GitHub账号创建公开仓库，把本目录作为仓库根目录上传（包含.github/workflows）。不要上传.venv、work、代理设置、账号资料或密钥。
2. 在仓库Actions中启用工作流。collect.yml需要内置Token的contents:write权限；如组织政策阻止，需要仓库管理员授权，不能添加未获授权的个人Token绕过。
3. 在仓库 Settings → Pages 中选择 GitHub Actions 作为 Source，Pages 发布使用 pages:write 与 id-token:write。点击“Collect 14:30 index snapshot”→“Run workflow”，验证云端运行、失败状态发布以及公开URL。在窗口外手动测试会采集真实数据但标记不可用，工作流可能以失败状态结束；这是预期。
4. 匿名测试真实URL：

```powershell
.\.venv\Scripts\python.exe -m etf_assistant.cli verify-url "https://raw.githubusercontent.com/OWNER/REPO/main/public/latest.json"
```

OWNER/REPO及main必须改为真实账号、仓库及默认分支；该示例不是已部署的URL。raw.githubusercontent.com可能返回text/plain，正文仍为JSON；导出的OpenAPI支持两种类型，GPT Action真实读取仍需测试。校验HTTP匿名读取、JSON结构、发布内容匹配与实时新鲜度是分开的结果。

工作流会自动生成实际仓库地址的OpenAPI。需要导入自定义GPT时，参考CHATGPT_INSTRUCTIONS.md。手机推送需在ChatGPT云端配置定时任务与通知，不能仅依赖自定义GPT创建或Actions成功。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=reports/offline-tests.xml
.\.venv\Scripts\python.exe -m etf_assistant.cli live-test --output-dir reports/live-current
```

requirements.lock.txt固定本次验证环境。离线测试包括时间边界、午盘、跨日、错指数、日历超范围、未来时间、无时间、坏价格、重复冲突、缓存过期、TLS兼容传输、子进程超时、实际AKShare解析器、JSON Schema及工作流配置。Linux 云端 45 项测试已通过，证据为 reports/linux-ci/offline-ci.xml 和 reports/linux-ci-run.json。

## 官方资料

- AKShare分钟接口：https://akshare.akfamily.xyz/data/index/index.html
- 中证A500官方资料：https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000510factsheet.pdf
- 创业板50官方资料：https://www.cnindex.com.cn/html2pdf/preview/jj_399673.pdf
- GitHub定时任务延迟说明：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- OpenAI定时任务：https://learn.chatgpt.com/docs/automations
- GPT Actions：https://developers.openai.com/api/docs/actions/introduction
