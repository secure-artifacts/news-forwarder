# 国际新闻自动收集与转发器

1.2.0 增加独立的“社交平台动态”板块，可定期监测公开的 WhatsApp、Facebook、Instagram、抖音/TikTok 及其他平台政策和资料更新。社交动态支持中文重点摘要、相似内容去重、单条/全部复制，并写入独立的 Google Sheets 工作表。

1.4.1 是安全修订版：删除未使用且存在多项已知漏洞的 `python-multipart` 依赖，并在 Windows 打包配置中明确排除其模块。1.4.0 将社交平台动态升级为近期研究资料监测：默认回溯 90 天并按最新发布日期排序，自动覆盖平台政策、算法、广告、Reels、爆款内容和账号增长等主题；Windows 版新增任务栏通知区域图标，可随时打开管理页面或彻底退出后台程序。

每天自动收集指定国家的国际新闻，过滤中国域名和指定来源，翻译成简体中文，然后：

- 发送到 Microsoft Teams 频道或聊天；
- 追加到 Google Sheets；
- 在本地管理页面查看状态、历史记录和失败重试；
- 用 SQLite 自动去重，避免重复转发。

默认已配置安哥拉和莫桑比克。国家、关键词、翻译服务、Teams、Google
Sheets 和运行时间都可以在网页设置中修改，无需手工编辑配置文件。

## 1. 快速启动（Windows）

最简单的方法是直接双击项目目录中的 `一键启动.bat`。它会自动检查运行环境、
后台启动服务，并打开管理网页；如果服务已经运行，则只会打开网页，不会重复启动。

1. 安装 Python 3.11 或更高版本。
2. 复制 `.env.example` 为 `.env`，填入需要的密钥。
3. 在 PowerShell 中运行：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\start.ps1
   ```

4. 浏览器打开 <http://127.0.0.1:8000>。

首页用于抓取新闻和选择发送目标；设置页面是
<http://127.0.0.1:8000/settings>。设置保存后立即生效。

首页的“运行日志”会每 2 秒自动刷新，并详细显示每个国家的优先网站搜索结果、
全网补充、正文解析、同事件去重、中文整理以及 Google Sheets/Teams 发送进度。
最近 2000 条活动保存在数据库中，任务结束或刷新页面后仍可查看。

第一次启动会创建 `.venv` 并安装依赖。也可以只执行一次抓取：

```powershell
.\.venv\Scripts\python.exe main.py run-once
```

## 2. Microsoft Teams 配置

推荐使用 Teams 的 **Workflows**，不要新建即将淘汰的旧 Microsoft 365 Connector。

1. 打开目标 Teams 频道，点击频道旁的 `…` → **Workflows**。
2. 选择类似 **Send webhook alerts to a channel** 的模板，或创建带有 **When a Teams webhook request is received** 触发器的流程。
3. 选择要接收消息的团队和频道，保存并复制 Webhook URL。
4. 打开网页右上角的“设置”，在 Microsoft Teams 区域粘贴 Webhook URL，
   启用 Teams 后保存。

如果流程模板只接受简单文本，把 `config.yaml` 中的 `payload_mode` 改成 `text`；默认的 `adaptive_card` 显示效果更好。

## 3. Google Sheets 配置

1. 在 Google Cloud 创建项目，启用 **Google Sheets API**。
2. 创建服务账号并下载 JSON 密钥。
3. 新建或选择一个 Google 表格，把表格共享给服务账号 JSON 中的 `client_email`，权限设为编辑者。
4. 从表格网址 `/d/` 和 `/edit` 之间复制 Spreadsheet ID。
5. 在网页“设置”的 Google Sheets 区域填写 Spreadsheet ID、工作表名称和
   服务账号 JSON 文件路径，然后保存。

程序会自动创建 `News` 工作表，并写入采集时间、发布时间、国家、中文标题、中文摘要、原文标题、来源和原文链接。

## 4. 中文翻译

当前运行实例使用本地 `TranslateGemma 4B`，不需要 API Key。模型文件位于
`data/models/translategemma-4b-q4_k_m.gguf`，程序启动时会自动启动本地翻译服务。
模型约 3.3 GB，不包含在源码 ZIP 中。

网页设置支持本地翻译、Gemini、Groq 和 OpenAI。选择云端服务时，在同一区域
填写模型名和 API Key；密钥只保存在本机 `.env`，读取设置时不会回传到浏览器。
留空表示保留原密钥，也可勾选“清除密钥”。

翻译流程会先从原始摘要中截取最多三句重点，再生成中文标题和不超过 180 个汉字
的重点摘要，不会翻译新闻全文。

## 5. 国家与关键词

在网页设置的“国家与地区”区域点击“添加国家或地区”。每个国家可以分别设置
Google News 地区参数、原文语言、必须包含的关键词和排除词。

新增国家时只需要重点填写：

- 显示名称：例如“佛得角”；
- 国家基础搜索词：例如 `Cabo Verde OR Cape Verde`；
- 新闻关键词：可选，支持逗号或换行分隔，例如 `economy, government, investment`；
- 优先新闻网站：可选，填写域名或完整网址，例如 `reuters.com`、`bbc.com`。

内部 ID 由系统自动生成，页面不再要求手工填写。语言、地区代码等不常用字段已放进
“高级语言与地区设置”，一般保持默认即可。

采集时系统会先查询指定网站；数量不足或原文正文无法读取时，再从其他符合国际来源
安全规则的网站补充。Google News 跳转链接会尽量还原成媒体原文链接。

大量关键词会自动分组为 OR 搜索。例如 140 个关键词会拆成多个长度安全的搜索组，
所有关键词都会覆盖，同时避免单个 Google News 查询网址过长。

程序会读取可访问的新闻正文，选取包含人物、机构、地点、数字、日期和事件结果的关键
事实，再生成简短中文摘要。无法读取正文时才使用 RSS 内容作为后备。不同媒体对同一
事件的相似标题会被合并，只保留一条；不同事件仍分别保留。

- `query`：Google News 搜索表达式；
- `keywords`：至少命中一个才保留，空列表表示不限制；
- `exclude_keywords`：命中任何一个就排除；

## 6. 手动发送方式

首页的“抓取并整理新闻”只负责采集、去重和翻译。完成后勾选“Google Sheets”或
“Microsoft Teams”，再点击“发送到所选目标”。两个目标可以单选，也可以同时选择。
如果确实希望抓取完成后自动发送，可在设置中启用“抓取完成后自动发送”。

需要定时写入表格时，启用 Google Sheets，并勾选“按下方时间抓取并自动写入已启用
渠道”，然后在“定时更新”中设置时区、小时和分钟。保存后立即生效，无需重启。

## 7. 国际来源安全策略

`source_policy` 默认执行以下规则：

- 只允许 HTTPS；
- 屏蔽所有 `.cn` 域名；
- 屏蔽配置中的中国媒体域名和来源名称；
- 来源网址存在时，优先检查真实媒体来源域名，而不是 Google News 跳转域名。

如果需要更严格的白名单，只允许 Reuters、BBC、DW 等指定网站，可以设置：

```yaml
source_policy:
  require_https: true
  allowed_domains: [reuters.com, bbc.com, dw.com, rfi.fr]
  blocked_domain_suffixes: [.cn]
  blocked_source_words: [Xinhua, 新华]
```

安全过滤指的是来源协议与域名规则，不代表程序对每篇新闻的真实性作保证。重要信息仍应打开原文交叉核验。

## 8. 定时与部署

默认按 `Europe/Lisbon` 时区每天 08:00 运行：

```yaml
app:
  timezone: Europe/Lisbon
  schedule_hour: 8
  schedule_minute: 0
```

服务器上可使用 Docker：

```bash
cp .env.example .env
docker compose up -d --build
```

公开部署管理页面时，请在 `.env` 设置强随机 `ADMIN_TOKEN`；点击“立即抓取”时页面会要求输入。建议同时用防火墙或反向代理限制管理页面访问。

## 9. 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

数据库位于 `data/news.db`。发送失败的记录不会标记为完成，下次运行会自动重试。

## 10. 安全发布

仓库只保存示例配置。首次从源码运行时，请复制 `config.example.yaml` 为 `config.yaml`，并复制 `.env.example` 为 `.env`。真实的 Google 服务账号、Webhook、API 密钥、数据库、日志、本地模型和运行时文件不会进入 Git 或发布包。

推送 `v*` 标签会运行测试、构建 Python 包和可直接运行的源码 ZIP，然后由 GitHub Actions 创建带构建来源证明（Attestation）的 Release。CodeQL 与 Dependabot 配置也已包含在仓库中。

## 11. Windows 离线安装包

离线安装版内置 Python、应用依赖、本地翻译运行组件和 TranslateGemma 模型。安装完成后会创建桌面和开始菜单快捷方式，用户无需安装 Python 或下载模型。安装包不会包含开发者自己的 `.env`、Google 服务账号、新闻数据库或日志。

构建命令：

```powershell
.\build-installer.ps1
```
