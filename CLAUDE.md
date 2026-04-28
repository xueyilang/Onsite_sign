# CLAUDE.md — Onsite Sign

## 项目概述

在飞书多维表格中实现一键发起 Zoho Sign 现场服务工单签署。工程师在飞书表格中触发按钮 → 服务器创建 Zoho 嵌入式签署请求 → 将签署链接通过飞书机器人发给现场人员 → 客户在 Zoho 嵌入式页面完成签名 → Zoho Webhook 回调 → 服务器下载已签 PDF 并回写到飞书表格附件字段。

部署平台：Render（免费版），单文件 Python 服务，零外部依赖。

## 架构

```
飞书多维表格 (Onsite Service)
    │
    ├─ 按钮触发 POST /sign/start ──→ sign_server.py (Render)
    │                                       │
    │                   ① 读取飞书记录       │
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
- `ZOHO_TEMPLATE_ID` — 当前生产模板 `VorortServiceProtokoll`
- `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` — OAuth 刷新令牌方式获取 access token（优先级高于 `ZOHO_SIGN_TOKEN`）
- `ZOHO_SIGN_TOKEN` — 静态 token（OAuth 不可用时的 fallback）
- `ZOHO_EMBED_LOCALE` — 嵌入签署页面语言，默认 `de`
- `ZOHO_WEBHOOK_SECRET` — Webhook HMAC 签名密钥

### 飞书
- `FEISHU_BASE_URL` — `https://open.feishu.cn`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` — 获取 tenant_access_token
- `FEISHU_APP_TOKEN` — 多维表格 app token
- `FEISHU_TABLE_ID` — 表格 ID
- `DEFAULT_NOTIFY_OPEN_ID` — 默认通知目标（未指定 `notify_open_id` 时的 fallback）

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
飞书表格字段 → Zoho 模板字段，定义在 `REQUIRED_FIELD_MAPPING` 和 `OPTIONAL_FIELD_MAPPING` 字典中（`sign_server.py:46-73`）。

必填字段：`service_date`, `service_KW`, `kunden_name`, `kunden_addr`, `kunden_contact`, `system_modell`, `system_sn`, `system_bat_modell`, `system_bat_anzahl`, `vorort_problem`, `vorort_arbeiten`, 以及 9 个 `zustand_*` 状态字段。

可选字段：`austasuch_sn_alte`, `austasuch_sn_neue`, `service_anmerkungen`。

### 校验规则（`validate_mapped_fields`，行 429-450）
1. 所有必填字段不能为空
2. 任何字段值不能包含中文字符
3. 如果 `zustand_austausch = Ja`，则 `austasuch_sn_alte` 和 `austasuch_sn_neue` 也必填

### 特殊值转换
- `日期`：飞书存的是毫秒时间戳 → 转为 `DD.MM.YYYY` 格式（柏林时区）
- `周数 KW`：`KW07` → `7`，只保留整数
- 嵌入签署链接：强制追加 `locale=de` 参数

### 签署请求命名
Zoho 请求名格式：`VorortProtocol_{工单号}`

### Zoho 签署收件人优先级
Zoho 嵌入式签署的收件人邮箱按以下优先级确定：
1. 飞书记录中的 `Email Adresse` 字段（如果格式为合法 email）
2. 发起人（`notify_open_id` 对应的飞书用户邮箱）
3. `service@alpha-ess.de`（硬编码兜底）

客户签完后 Zoho 自动发送已签文档副本（`send_completed_document: True`）。

### kunden_contact 字段优先级
1. 优先取 `Email Adresse` 字段的值
2. 为空时取 `联系方式` 字段 → 多行号码时只取第一行

### Webhook 回写
从 Zoho Webhook payload 中提取 `request_id` → 查 `request_map.json` 获取 `record_id` → 下载 PDF → 上传到飞书 → 更新飞书记录的附件字段。

如果 `request_map.json` 中找不到映射（比如服务器重启后丢失），fallback 逻辑会遍历飞书表格按工单号查找对应记录。

## 部署（Render）

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

4. **OAuth Token 刷新无缓存**
   每次请求都调用 `get_zoho_access_token()` 去 Zoho 换新的 access token，没有缓存或过期管理。Zoho access token 通常有效期 1 小时，当前实现每次都换，浪费请求且增加延迟。

5. **付费服务协议模板**
   目前只有一个 `VorortServiceProtokoll` 模板（免费服务）。付费服务工单需要另一个模板和对应的字段映射，尚未实现。

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
