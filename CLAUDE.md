# CLAUDE.md — Onsite Sign

## 项目概述

在飞书多维表格中实现一键发起 Zoho Sign 现场服务工单签署。工程师在飞书表格中触发按钮 → 服务器创建 Zoho 嵌入式签署请求 → 将签署链接通过飞书机器人发给现场人员 → 客户在 Zoho 嵌入式页面完成签名 → Zoho Webhook 回调 → 服务器下载已签 PDF 并回写到飞书表格附件字段。

部署平台：Render（付费 Starter 实例，无冷启动），单文件 Python 服务，零外部依赖。线上服务名 `Onsite_sign`（`srv-d7gelfs71suc7383unl0`），**实际区域为 oregon**。

## 架构

```
飞书多维表格 (Onsite Service)
    │
    ├─ 按钮触发 POST /sign/start ──→ sign_server.py (Render)
    │                                       │
    │                   ① 读取飞书记录+按方向选模板│
    │                   ② 字段映射 + 校验    │
    │                   ③ 创建 Zoho 嵌入式签署│
    │                   ④ 获取签署 URL       │
    │                   ⑤ 飞书机器人发链接    │
    │                                       │
    │  ←── 签署链接（飞书消息）──────────────┘
    │
    │  客户在 Zoho 嵌入式页面完成签名
    │
    └── Zoho Webhook ──→ POST /webhooks/zoho-sign
                                          │
                             ⑥ 验证 HMAC 签名
                             ⑦ 下载已签 PDF
                             ⑧ 上传到飞书附件字段
```

## 文件结构

| 文件 | 角色 |
|------|------|
| `sign_server.py` | **生产服务器**，包含全部业务逻辑 |
| `render.yaml` | Render 部署配置 |
| `.env.example` | 本地开发环境变量模板 |
| `Onsite_sign.env` | 实际环境变量（**勿提交**，含密钥） |
| `requirements.txt` | 空，仅标准库 |
| `request_map.json` | 运行时生成的 `request_id → record_id` 映射 |

## 本地运行

```bash
# 加载环境变量（Windows 需手动设置或用 IDE）
cp .env.example .env
# 填入真实值

python sign_server.py
# 默认监听 0.0.0.0:8080，可通过 PORT 环境变量修改
```

测试：
```bash
curl -X POST http://localhost:8080/sign/start \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TRIGGER_AUTH_TOKEN>" \
  -d '{"record_id": "recXXXX", "notify_open_id": "ou_XXXX"}'
```

## 关键环境变量

### Zoho Sign
- `ZOHO_BASE_URL` — `https://sign.zoho.eu`
- `ZOHO_ACCOUNTS_BASE_URL` — `https://accounts.zoho.eu`
- `ZOHO_TEMPLATE_ID` — 当前生产模板 `VorortServiceProtokoll2.1` (ID: `109352000000062071`)
- `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` — OAuth 刷新令牌方式获取 access token（优先级高于 `ZOHO_SIGN_TOKEN`）
- `ZOHO_SIGN_TOKEN` — 静态 token（OAuth 不可用时的 fallback）
- `ZOHO_EMBED_LOCALE` — 德国区的签署页语言和邮件语言，默认 `de`（BENELUX 固定：签署页 `nl`、邮件 `en`）
- `ZOHO_TEMPLATE_ID_BENELUX` — BENELUX 英文模板 `VorortServiceProtokoll-EN-Benelux`（ID: `109352000000292004`）；未配置时 BENELUX 工单直接报错，不会退回德语模板
- `ZOHO_WEBHOOK_SECRET` — Webhook HMAC 签名密钥

### 飞书
- `FEISHU_BASE_URL` — `https://open.feishu.cn`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` — 获取 tenant_access_token
- `FEISHU_APP_TOKEN` — 多维表格 app token
- `FEISHU_TABLE_ID` — 表格 ID
- `DEFAULT_NOTIFY_OPEN_ID` — 默认通知目标（未指定 `notify_open_id` 时的 fallback）

### Zoho 额度周报
- `ZOHO_CREDIT_REFRESH_TOKEN` — 只读令牌（scope `ZohoSign.account.READ`，同一个 Self Client 于 2026-11-13 生成），只用于查额度，和签署用的 `ZOHO_REFRESH_TOKEN` 分开
- `CREDIT_REPORT_OPEN_ID` — 接收周报的飞书 open_id（Marco：`ou_59c3ace2665789eeea82ffda54b21bc6`）
- `CREDIT_ALERT_THRESHOLD` — 低于该值时消息里加提醒，默认 200

两者都设置时，`main()` 启动后台线程 `run_weekly_credit_report`：每周一 09:00 柏林时间（`next_credit_report_time`，自动处理冬/夏令时）调 `GET /api/v1/currentuser` 读 `zs_credits_left`，无论多少都发一条飞书消息；查询失败也会发失败消息。服务重启后重新计算下一次时间，启动日志有 `credit_report.scheduled` 或 `credit_report.disabled`。2026-11-13 剩余 1895 credits。

### 服务
- `TRIGGER_AUTH_TOKEN` — 保护 `/sign/start` 端点
- `REQUEST_MAP_FILE` — 映射持久化文件路径，默认 `request_map.json`
- `PORT` — 监听端口，默认 `8080`

## API 端点

### `GET /` 或 `GET /health`
健康检查，返回 `{"status": "ok"}`。Render 用此端点判断服务是否存活。

### `POST /sign/start`
飞书表格自动化触发。鉴权方式：
- Header `Authorization: Bearer <TRIGGER_AUTH_TOKEN>`
- 或 Header `X-Trigger-Token: <TRIGGER_AUTH_TOKEN>`

请求体：
```json
{
  "record_id": "飞书记录 ID（必填）",
  "notify_open_id": "通知目标 open_id（必填，也用于 Zoho 签署邮件）",
  "trigger_open_id": "触发者 open_id（可选，未指定 notify_open_id 时使用）",
  "wo": "工单号（可选，优先使用飞书记录中的上门单号字段）"
}
```

成功返回 `200`，校验失败返回 `400`（含 `validation_errors` 详情数组），鉴权失败返回 `401`。

### `POST /webhooks/zoho-sign`
Zoho Sign 事件回调。鉴权方式：验证 `X-ZS-WEBHOOK-SIGNATURE` Header 的 HMAC-SHA256 签名。

仅在签署完成事件时触发 PDF 下载和回写。其他事件返回 `{"ignored": true}`。

## 核心业务逻辑

### 字段映射
飞书表格字段 → Zoho 模板字段，定义在 `REQUIRED_FIELD_MAPPING` 和 `OPTIONAL_FIELD_MAPPING` 字典中（`sign_server.py`）。

注意：`field_text_data` 的 key 必须和 Zoho 模板的 `field_label` **大小写完全一致**（如 `Techniker` 不能写成 `techniker`）。

必填字段：`service_date`, `service_KW`, `kunden_name`, `kunden_addr`, `kunden_contact`, `system_modell`, `system_sn`, `system_bat_modell`, `system_bat_anzahl`, `vorort_problem`, `vorort_arbeiten`, 以及 9 个 `zustand_*` 状态字段，`Techniker`（从 `人员` 字段自动提取英文名）。

可选字段：`austasuch_sn_alte`, `austasuch_sn_neue`, `service_anmerkungen`, `Abfahrt`, `Ankunft`, `Von`, `Bis`, `Entfernung`。

### 校验规则（`validate_mapped_fields`）
1. 所有必填字段不能为空
2. 任何字段值不能包含中文字符
3. 如果 `zustand_austausch = Ja`，则 `austasuch_sn_alte` 和 `austasuch_sn_neue` 也必填
4. **PDF 溢出预检**：任何文本字段在 PDF 里放不下就拦截，不创建签署，飞书提示精简（SN 字段提示把多余 SN 写到备注）。见下方「PDF 字段溢出」

### 特殊值转换
- `日期`：飞书存的是毫秒时间戳 → 转为 `DD.MM.YYYY` 格式（柏林时区）
- `周数 KW`：`KW07` → `7`，只保留整数
- 嵌入签署链接：强制设置 `locale` 参数（德国区 `de`，BENELUX `nl`）
- `SN(被取回)` / `SN(被使用)`：统一用 `; ` 连成一行，由 Zoho 按宽度自动折行（不再强制分两行，强制换行会浪费格子空间）
- `Techniker`：从飞书 `人员` 字段自动提取英文名（email 推断 → en_name → name），最多取前 4 人（`MAX_TECHNIKER_NAMES`）：`Marco`、`Marco & Tri`、`A, B, C & D`。德国区和 BENELUX 相同。模板里的 Techniker 框宽约 154pt、一行 8pt；按表中 31 位工程师实测，3 人任意组合都放得下，4 人组合只有极少数（最长名字凑在一起，约 0.04%）会超出并被溢出预检拦截

### 区域（BENELUX）
按飞书记录的 `方向` 字段选择区域配置（`SignRegion`，`get_sign_region`）：

| | 德国区（默认） | BENELUX（`方向 = BENELUX`） |
|---|---|---|
| Zoho 模板 | `ZOHO_TEMPLATE_ID`（德语） | `ZOHO_TEMPLATE_ID_BENELUX`（英文，页脚 Alpha ESS Benelux B.V.） |
| 签署页语言（`signing_page_locale`） | `de` | `nl`（不能用 `en`，见下文） |
| Zoho 邮件语言（`language`，含签完后的 PDF 邮件） | `de` | `en`（客户生效；抄送工程师仍收到德语，原因未明，见下文） |
| 兜底收件邮箱 | `service@alpha-ess.de` | `servicebenelux@alphaess.nl` |
| 状态字段值 | `Ja` / `Nein` / `Nicht vorhanden` 原样 | 翻译为 `Yes` / `No` / `Not available` |

英文模板与德语模板字段一一对应、坐标完全相同；区别是 9 个 `zustand_*` 在英文模板里是**普通文本框**（Zoho API 不能创建下拉框）。**不要把预填文本框设为只读**：Zoho 不会把只读文本框的预填值渲染进 PDF（签署前和签完都是空的）。

**邮件语言**：代码在 createdocument 时给每个收件人传 `language`（`build_embedded_actions`）。Zoho 后台 Branding → Email templates 里**不要勾选 “Set as common language”**，勾上后所有收件人都被强制成该语言、`language` 参数失效（2026-11-12 就是因为勾了德语才一直收到德语）。注意 API（包括网页后台的内部接口）读回请求时 `language` 常为空或不准，**不能用回读判断是否生效，只能看实际收到的邮件**。实测（2026-11-13）：客户（嵌入式签署人）收到英文；抄送工程师（VIEW）收到德语，而前一天英文邮件模板未自定义时工程师收到的是英文，原因未确认。

**英文邮件模板**（Zoho 后台 Branding → Email templates → English，共 26 个，只有 BENELUX 会用到）：已改成灰色顶栏 `#9FA0A0`、黄色按钮 `#FABE0F`（与德语模板和 logo 一致）；正文和标题里的 `$SENDER_EMAIL$` / `$ORG_NAME$` 已写死为 `servicebenelux@alphaess.nl` / `Alpha ESS Benelux B.V.`。`$FOOTER$` 是 Zoho 必填变量，删掉就无法保存，所以原页脚被包在隐藏的 div 里（Zoho 保存时会删掉 `display:none` 和 `mso-hide:all`，保留 `hidden` 属性和 `visibility:hidden` 等），下面另写一份同内容、邮箱换成 Benelux 的页脚。Gmail 已验证只显示我们的页脚，Outlook 未测。发件人地址和回复地址仍是 `service@alpha-ess.de`（发起请求的 Zoho 账户），要彻底分开只能给 BENELUX 单独开 Zoho 用户和 refresh token。

**签署页语言**：由 embed 链接的 `locale` 控制，Zoho 生成的链接本身带 `locale=en`。但 Zoho 签署页对 `locale=en` 不生效：它把 `en` 当成默认值，不带 locale 去取界面文字，结果按设备浏览器语言显示（中文手机就是中文）。`de`、`nl` 等非英语值能强制生效；Zoho 支持的英语代码只有 `en`（`en_GB` 会让页面卡在加载中）。所以 BENELUX 签署页用 `nl`，PDF 和邮件仍是英文。

模板源文件见 Downloads 下 `ServiceProtocol-EN.docx`（Word 无订阅，只能用“打印 → 存储为 PDF”导出）。

### 签署请求命名
Zoho 请求名格式：`VorortProtocol_{工单号}`

### Zoho 签署收件人
- **签署人（客户，嵌入式）**：飞书 `Email Adresse` 里的第一个合法邮箱；没有就用区域兜底邮箱（德国区 `service@alpha-ess.de`，BENELUX `servicebenelux@alphaess.nl`）。
- **抄送（VIEW，工程师副本）**：**点击按钮的人**的飞书邮箱，不是 `人员` 字段。飞书自动化把点击人以**名字**（如 `杨赞琦`）传成 `notify_open_id`，服务器用 `list_feishu_users` 在通讯录里按名字/open_id 找到这个人，拿 open_id（发签署链接）和邮箱（抄送）。与客户邮箱相同时不加抄送。
- `人员` 只用于 PDF 上的 On-site technician 名字。

客户签完后 Zoho 自动给签署人和抄送人发已签文档副本（`send_completed_document: True`）。

### 飞书应用权限（每个点按钮的人都需要）
1. 应用**可用范围**：否则机器人发不了签署链接给他。
2. **通讯录权限范围**：否则 `list_feishu_users` 查不到他，拿不到 open_id 和邮箱。
3. 多维表格编辑权限：否则按钮/自动化不触发（这种失败不会到服务器，日志里看不到）。

点击人不在通讯录范围内时的现状：Zoho 请求照样创建（没有抄送），随后发飞书消息失败，链接丢失，请求挂在 Zoho 里。2026-11-13 核对时通讯录范围只有 9 个德国区用户（离职的 jianfei.yu、mark.ma 已移出）；已申请扩大到整个部门，但当时读回仍是 9 人，需发布新版本/管理员审批后才生效，BENELUX 同事首次使用前要再确认。可用 `GET /open-apis/contact/v3/scopes` 和 `list_feishu_users` 核对。历史核对：5 月加抄送以来 312 单都有抄送人，没有发现解析失败的单子。

### kunden_contact 字段优先级
1. 优先取 `Email Adresse` 字段的值
2. 为空时取 `联系方式` 字段 → 多行号码时只取第一行

飞书会把被清空的字段写成占位文字 `（空）`（签完后 `Email Adresse` 被改成 `（空）` 就是这样），`normalize_value` 把 `（空）` / `(空)` 当空值处理，所以不会再被优先选中、触发“含中文”的误报。两个字段都为空时，报错标签是 `Email Adresse / 联系方式`。

### Webhook 回写
从 Zoho Webhook payload 中提取 `request_id` → 查 `request_map.json` 获取 `record_id` → 下载 PDF → 上传到飞书 → 更新飞书记录的附件字段。

如果 `request_map.json` 中找不到映射（比如服务器重启后丢失），fallback 逻辑会遍历飞书表格按工单号查找对应记录。

## 部署（Render）

> ⚠️ 线上服务是在 Render Dashboard 手动创建的，**不受 `render.yaml` 管理**：其中的 `plan: free`、`region: frankfurt`、`healthCheckPath` 均未生效。线上实际为 Starter 付费版、oregon 区域。Render 不支持修改已有服务的区域，迁移需新建服务并更新飞书自动化与 Zoho Webhook 的 URL。

`render.yaml` 定义了：
- Python 3.12.3
- 启动命令：`python sign_server.py`
- 健康检查：`GET /health`
- 自动部署：开
- Plan：free

部署时注意：
- 密钥类环境变量在 Render Dashboard 中设置（`sync: false`）
- `REQUEST_MAP_FILE` 默认为 `request_map.json`，保存在 Render 实例文件系统上

## 已知风险 & 待办

1. **`request_map.json` 不持久**（高优先级）
   Render 免费版文件系统在重启/重新部署时会清空。如果创建签署请求后、收到 Webhook 前发生重启，`request_id → record_id` 映射丢失。代码中有 fallback（按工单号查飞书），但依赖 Webhook payload 中的 `request_name` 能正确解析出工单号。建议后续迁移到 Render Disk 或用飞书表格字段存储映射。

2. **`Onsite_sign.env` 已 gitignored**（已修复）
   已添加到 `.gitignore`，防止意外提交真实密钥。

3. **`ThreadingHTTPServer` 生产适用性**
   标准库的简易服务器，没有请求队列限制、连接超时等生产级特性。当前低流量场景可接受，流量增大后应考虑 gunicorn 或换用 Render 的 Docker 部署。

4. **OAuth Token 刷新无缓存**（已修复）
   Zoho access token 与飞书 tenant_access_token 均在内存中缓存，过期前 5 分钟刷新（`_cached_token`）。

5. **付费服务协议模板**
   目前只有一个 `VorortServiceProtokoll2.1` 模板（免费服务）。付费服务工单需要另一个模板和对应的字段映射，尚未实现。

6. **嵌入签署链接一次性、2 分钟有效**
   Zoho Sign embed token 生成的 `zsguest?sign_id=...` 链接只能打开一次，且按 Zoho 文档生成后约 2 分钟不打开就失效。重新点按钮会再建一单新请求，旧的挂在 Zoho 直到过期（5 月以来 312 单中 95 单 expired）。当前未做中转处理，如需改进可加 `GET /sign/embed` 重定向端点（每次点击重新获取 embed token）。现场操作说明已写进 BENELUX 说明文档。

7. **Zoho API 不支持程序化创建/修改 Dropdown 字段**
   `PUT /api/v1/templates` 和 `POST /api/v1/templates` 均拒绝 `field_type_name: "Dropdown"`。下拉框字段只能通过 Zoho 网页 UI 手动编辑。如需新增下拉框，先在 UI 操作后再用 API 更新 Template 其他部分。

8. **点击人解析不到时链接丢失**
   见上方「飞书应用权限」。可改为：解析不到点击人时先在飞书/日志报错，不创建 Zoho 请求（2026-11-13 用户决定暂不改）。

9. **BENELUX 抄送工程师收到德语邮件**（原因未明）
   客户收到英文；工程师（VIEW）收到德语。前一天英文邮件模板未自定义时工程师收到的是英文默认样式。API 读回看不到收件人语言，只能靠真实测试判断。

10. **飞书里有自动化把 `Email Adresse` 改成 `（空）`**
    签完后出现过。代码已容忍（见 kunden_contact），但客户邮箱被覆盖后不会恢复，需要在飞书里找到这个自动化。

11. **英文邮件模板的隐藏页脚未在 Outlook 验证**
    Gmail 正常；Outlook 桌面版可能露出原页脚的空白和 “here” 链接。

12. **Techniker 框只占半行**
    Zoho 模板里 Techniker 文本框约 154pt 宽、一行，4 个最长名字组合偶尔会被溢出预检拦截。要彻底解决需在 Zoho 网页里把框拉宽到整行（德语模板有下拉框，不能用 API 改）。

## 文档与外部配置位置
- BENELUX 同事说明文档（英文，私有 Artifact，需在 Share 里共享）：https://claude.ai/artifact/3ueKAfXND72s97uY4DZ5Ur
- Zoho 邮件模板：Zoho Sign → Settings → Branding → Email templates（English 26 个已改，见「区域（BENELUX）」）。
- 飞书应用权限：飞书开放平台 → 应用 → 可用范围 / 通讯录权限范围。

## PDF 字段溢出

Zoho 用 Roboto 渲染模板文本字段，**放不下的内容会被直接截掉，不报错**（历史 306 个请求中 23 个工单受影响，17 份已签）。因此发起前会：
1. 从 Zoho 模板读取每个文本字段的宽、高、字号（`get_text_field_boxes`），可容纳行数 = `int(高 / (字号 × 1.17))`（1.17 由真实已签 PDF 校准）
2. 规范化（`fit_fields_to_boxes`）：去空行和多余空格；单行字段（客户名、系统 SN 等）的换行改为 `, `；地址仅在分行写法放不下时才合并成一段
3. 用代码内置的 Roboto 字宽表（`ROBOTO_WIDTH`，与真实字体误差平均 0.14%）按空格折行计算行数，超出即作为 validation error 拦截

模板几何是实时读取的，换模板（如 BENELUX 英文版）无需改代码。

## 性能说明

飞书多维表格接口服务端很慢（读单条记录约 2–3s，全表 517 条约 8–9s，与返回字段数基本无关）。因此：
- 子记录查找用 `records/search` 按 `父记录` 过滤，**不要全表扫描**；`search` 返回的文本是分段数组，用 `_join_text_segments` 拼回字符串。
- 表中的 `子记录` 字段与 `父记录` 反查**不一致**，不可用来找子记录。
- `process_sign_start` 中解析发起人、读记录、查子记录、拉 Zoho 模板四个请求并行执行。

## 代码约定

- 全程使用 Python 标准库（`urllib.request`, `http.server`, `json`, `hmac`, `re`）
- 不使用任何 pip 依赖
- HTTP 客户端封装：`api_request()` 返回 JSON dict，`binary_request()` 返回 `(bytes, content_type)`
- 日志统一用 `print(json.dumps({...}), flush=True)` 输出结构化 JSON，便于 Render 日志收集
- 字段映射使用中文变量名对应的常数字符串（如 `WO_FIELD = "上门单号"` 即 "上门单号"）
- 鉴权统一用 `hmac.compare_digest()` 防时序攻击
- 所有飞书 API 调用需要先通过 `get_feishu_tenant_token()` 获取 tenant_access_token
- **禁止在 Python 源码中使用 `\uXXXX` 转义序列写中文**。必须直接使用 UTF-8 中文字符。`\uXXXX` 会导致 Edit/Grep/Search 等工具匹配失败，大幅降低编辑效率。

## 外部服务

| 服务 | 用途 | 关键 API |
|------|------|----------|
| 飞书开放平台 | 表格读写、用户查询、文件上传、消息发送 | `bitable/v1`, `contact/v3/users`, `im/v1/messages`, `drive/v1/medias/upload_all` |
| Zoho Sign EU | 模板管理、签署请求创建、嵌入式签署、PDF 下载 | `/api/v1/templates`, `/api/v1/requests` |
| Zoho Accounts EU | OAuth token 刷新 | `/oauth/v2/token` |
