#!/usr/bin/env python3
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ZOHO_BASE_URL = os.getenv("ZOHO_BASE_URL", "https://sign.zoho.eu").strip()
ZOHO_TEMPLATE_ID = os.getenv("ZOHO_TEMPLATE_ID", "").strip()

FEISHU_BASE_URL = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").strip()
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "").strip()
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "").strip()
FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN", "").strip()
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID", "").strip()
DEFAULT_NOTIFY_OPEN_ID = os.getenv("DEFAULT_NOTIFY_OPEN_ID", "").strip()
TRIGGER_AUTH_TOKEN = os.getenv("TRIGGER_AUTH_TOKEN", "").strip()

try:
    BERLIN_TZ = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:
    BERLIN_TZ = None
WO_FIELD = "\u4e0a\u95e8\u5355\u53f7"

REQUIRED_FIELD_MAPPING = {
    "service_date": "\u65e5\u671f",
    "service_KW": "\u5468\u6570 KW",
    "kunden_name": "\u8054\u7cfb\u4eba(\u5de5\u5355)",
    "kunden_addr": "\u5730\u5740\u4fe1\u606f",
    "kunden_contact": "\u8054\u7cfb\u65b9\u5f0f",
    "system_modell": "vorort_system_modell",
    "system_sn": "SN\u7f16\u53f7",
    "system_bat_modell": "vorort_system_bat_modell",
    "system_bat_anzahl": "vorort_system_bat_anzahl",
    "vorort_problem": "vorort_problem",
    "vorort_arbeiten": "vorort_arbeiten",
    "zustand_Schaeden": "zustand_Schaeden",
    "zustand_Installationsfehler": "zustand_Installationsfehler",
    "zustand_PVfunktions": "zustand_PVfunktions",
    "zustand_Batterie": "zustand_Batterie",
    "zustand_WR": "zustand_WR",
    "zustand_Meter": "zustand_Meter",
    "zustand_WB": "zustand_WB",
    "zustand_austausch": "zustand_austausch",
    "zustand_behoben": "zustand_behoben",
}

OPTIONAL_FIELD_MAPPING = {
    "austasuch_sn_alte": "SN(\u88ab\u53d6\u56de)",
    "austasuch_sn_neue": "SN(\u88ab\u4f7f\u7528)",
    "service_anmerkungen": "vorort_anmerkungen",
}

CHINESE_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


class ValidationError(Exception):
    def __init__(self, details: list[str]):
        super().__init__("Validation failed")
        self.details = details


def require_env(name: str, value: str) -> str:
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def api_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> dict:
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc}") from exc


def get_bearer_token(auth_header: str) -> str:
    if not auth_header:
        return ""
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def is_authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = require_env("TRIGGER_AUTH_TOKEN", TRIGGER_AUTH_TOKEN)
    provided = (
        handler.headers.get("X-Trigger-Token", "").strip()
        or get_bearer_token(handler.headers.get("Authorization", "").strip())
    )
    return bool(provided) and hmac.compare_digest(provided, expected)


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        parts = [normalize_value(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "email"):
            if key in value and value[key] is not None:
                return normalize_value(value[key])
        return ""
    return str(value).strip()


def get_feishu_tenant_token() -> str:
    app_id = require_env("FEISHU_APP_ID", FEISHU_APP_ID)
    app_secret = require_env("FEISHU_APP_SECRET", FEISHU_APP_SECRET)
    response = api_request(
        "POST",
        f"{FEISHU_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json"},
        json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
    )
    if response.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {json.dumps(response, ensure_ascii=False)}")
    return response["tenant_access_token"]


def list_feishu_records(tenant_token: str) -> list[dict]:
    app_token = require_env("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
    table_id = require_env("FEISHU_TABLE_ID", FEISHU_TABLE_ID)
    items: list[dict] = []
    page_token = ""
    while True:
        query = urllib.parse.urlencode(
            {k: v for k, v in {"page_size": 500, "page_token": page_token or None}.items() if v is not None}
        )
        url = f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?{query}"
        response = api_request("GET", url, {"Authorization": f"Bearer {tenant_token}"})
        if response.get("code") != 0:
            raise RuntimeError(f"Feishu list failed: {json.dumps(response, ensure_ascii=False)}")
        data = response.get("data") or {}
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token") or ""


def get_feishu_record_by_id(tenant_token: str, record_id: str) -> dict:
    app_token = require_env("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
    table_id = require_env("FEISHU_TABLE_ID", FEISHU_TABLE_ID)
    response = api_request(
        "GET",
        f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        {"Authorization": f"Bearer {tenant_token}"},
    )
    if response.get("code") != 0:
        raise RuntimeError(f"Feishu get record failed: {json.dumps(response, ensure_ascii=False)}")
    data = response.get("data") or {}
    item = data.get("record")
    if not item:
        raise RuntimeError(f"No Feishu record found for record_id={record_id}")
    return item


def find_record_by_wo(tenant_token: str, wo_number: str) -> dict:
    for item in list_feishu_records(tenant_token):
        fields = item.get("fields") or {}
        if normalize_value(fields.get(WO_FIELD)) == wo_number:
            return item
    raise RuntimeError(f"No Feishu record found for {wo_number}")


def convert_service_date(raw: str) -> str:
    raw = raw.strip()
    if raw.isdigit():
        timestamp_ms = int(raw)
        dt_utc = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        dt = dt_utc.astimezone(BERLIN_TZ) if BERLIN_TZ else dt_utc.astimezone()
        return dt.strftime("%d.%m.%Y")
    return raw


def convert_service_kw(raw: str) -> str:
    raw = raw.strip()
    match = re.search(r"(\d+)", raw)
    return str(int(match.group(1))) if match else raw


def map_zoho_field_value(zoho_field: str, raw_value: str) -> str:
    if zoho_field == "service_date":
        return convert_service_date(raw_value)
    if zoho_field == "service_KW":
        return convert_service_kw(raw_value)
    return raw_value


def build_mapped_fields(record_fields: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for zoho_field, feishu_field in REQUIRED_FIELD_MAPPING.items():
        raw_value = normalize_value(record_fields.get(feishu_field))
        mapped[zoho_field] = map_zoho_field_value(zoho_field, raw_value)
    for zoho_field, feishu_field in OPTIONAL_FIELD_MAPPING.items():
        raw_value = normalize_value(record_fields.get(feishu_field))
        if raw_value:
            mapped[zoho_field] = map_zoho_field_value(zoho_field, raw_value)
    return mapped


def validate_mapped_fields(mapped_fields: dict[str, str]) -> None:
    errors: list[str] = []
    for zoho_field in REQUIRED_FIELD_MAPPING:
        value = mapped_fields.get(zoho_field, "").strip()
        if not value:
            errors.append(f"{zoho_field}: empty")
        elif CHINESE_RE.search(value):
            errors.append(f"{zoho_field}: contains Chinese -> {value}")
    for zoho_field, value in mapped_fields.items():
        if value and CHINESE_RE.search(value):
            message = f"{zoho_field}: contains Chinese -> {value}"
            if message not in errors:
                errors.append(message)
    if errors:
        raise ValidationError(errors)


def get_zoho_template_detail(token: str) -> dict:
    template_id = require_env("ZOHO_TEMPLATE_ID", ZOHO_TEMPLATE_ID)
    response = api_request(
        "GET",
        f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}",
        {"Authorization": f"Zoho-oauthtoken {token}"},
    )
    if response.get("status") != "success":
        raise RuntimeError(f"Zoho template fetch failed: {json.dumps(response, ensure_ascii=False)}")
    return response["templates"]


def build_embedded_actions(template: dict) -> list[dict]:
    actions = []
    for action in template.get("actions", []):
        actions.append(
            {
                "action_id": action["action_id"],
                "action_type": action["action_type"],
                "recipient_name": "",
                "recipient_email": "",
                "signing_order": action.get("signing_order", 1),
                "verify_recipient": action.get("verify_recipient", False),
                "private_notes": action.get("private_notes", ""),
                "is_embedded": True,
            }
        )
    return actions


def create_zoho_embedded_request(token: str, wo_number: str, mapped_fields: dict[str, str]) -> dict:
    template_id = require_env("ZOHO_TEMPLATE_ID", ZOHO_TEMPLATE_ID)
    template = get_zoho_template_detail(token)
    payload = {
        "templates": {
            "request_name": wo_number,
            "actions": build_embedded_actions(template),
            "field_data": {
                "field_text_data": mapped_fields,
                "field_date_data": {},
                "field_boolean_data": {},
            },
        }
    }
    body = urllib.parse.urlencode({"data": json.dumps(payload, ensure_ascii=False)}).encode("utf-8")
    response = api_request(
        "POST",
        f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}/createdocument",
        {"Authorization": f"Zoho-oauthtoken {token}"},
        body,
    )
    if response.get("status") != "success":
        raise RuntimeError(f"Zoho embedded request create failed: {json.dumps(response, ensure_ascii=False)}")
    return response["requests"]


def get_embed_token_payload(token: str, request_id: str, action_id: str) -> dict:
    response = api_request(
        "POST",
        f"{ZOHO_BASE_URL}/api/v1/requests/{request_id}/actions/{action_id}/embedtoken",
        {"Authorization": f"Zoho-oauthtoken {token}"},
        urllib.parse.urlencode({}).encode("utf-8"),
    )
    if response.get("status") != "success":
        raise RuntimeError(f"Zoho embed token fetch failed: {json.dumps(response, ensure_ascii=False)}")
    return response


def extract_embed_link(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    match = re.search(r"https://[^\s\"\\]+", text)
    if not match:
        raise RuntimeError(f"Could not find embed link in payload: {text}")
    return match.group(0)


def send_feishu_text(tenant_token: str, open_id: str, text: str) -> dict:
    payload = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    return api_request(
        "POST",
        f"{FEISHU_BASE_URL}/open-apis/im/v1/messages?receive_id_type=open_id",
        {"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def process_sign_start(record_id: str, notify_open_id: str, zoho_token: str, wo_number: str = "") -> dict:
    tenant_token = get_feishu_tenant_token()
    record = get_feishu_record_by_id(tenant_token, record_id)
    record_fields = record.get("fields") or {}
    resolved_wo = normalize_value(record_fields.get(WO_FIELD)) or wo_number or record_id
    mapped_fields = build_mapped_fields(record_fields)
    try:
        validate_mapped_fields(mapped_fields)
    except ValidationError as exc:
        error_text = f"{resolved_wo} validation failed:\n" + "\n".join(exc.details)
        feishu_response = send_feishu_text(tenant_token, notify_open_id, error_text)
        return {
            "ok": False,
            "wo": resolved_wo,
            "record_id": record_id,
            "error_type": "validation",
            "validation_errors": exc.details,
            "feishu_message_response": feishu_response,
        }

    created = create_zoho_embedded_request(zoho_token, resolved_wo, mapped_fields)
    action_id = created["actions"][0]["action_id"]
    request_id = created["request_id"]
    embed_payload = get_embed_token_payload(zoho_token, request_id, action_id)
    embed_link = extract_embed_link(embed_payload)
    message_text = f"{resolved_wo} embedded sign link:\n{embed_link}"
    feishu_response = send_feishu_text(tenant_token, notify_open_id, message_text)
    return {
        "ok": True,
        "wo": resolved_wo,
        "record_id": record_id,
        "request_id": request_id,
        "action_id": action_id,
        "request_status": created.get("request_status"),
        "embed_link": embed_link,
        "feishu_message_response": feishu_response,
    }


class SignHandler(BaseHTTPRequestHandler):
    server_version = "OnsiteSignServer/0.1"

    def _json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/health"}:
            self._json_response(200, {"status": "ok"})
            return
        self._json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/sign/start":
            self._json_response(404, {"error": "not_found"})
            return

        try:
            authorized = is_authorized(self)
        except RuntimeError as exc:
            self._json_response(500, {"error": "missing_trigger_auth_token", "detail": str(exc)})
            return
        if not authorized:
            self._json_response(401, {"error": "unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
            return

        record_id = str(payload.get("record_id") or "").strip()
        trigger_open_id = str(payload.get("trigger_open_id") or "").strip()
        wo_number = str(payload.get("wo") or payload.get("WO") or "").strip()
        default_notify_open_id = DEFAULT_NOTIFY_OPEN_ID.strip()
        notify_open_id = str(payload.get("notify_open_id") or trigger_open_id or default_notify_open_id).strip()
        if not record_id:
            self._json_response(400, {"error": "missing_record_id"})
            return
        if not notify_open_id:
            self._json_response(500, {"error": "missing_notify_open_id"})
            return

        zoho_token = os.getenv("ZOHO_SIGN_TOKEN", "").strip()
        if not zoho_token:
            self._json_response(500, {"error": "missing_zoho_token"})
            return

        try:
            result = process_sign_start(record_id, notify_open_id, zoho_token, wo_number=wo_number)
        except Exception as exc:
            try:
                tenant_token = get_feishu_tenant_token()
                error_label = wo_number or record_id
                send_feishu_text(tenant_token, notify_open_id, f"{error_label} sign start failed:\n{exc}")
            except Exception:
                pass
            self._json_response(500, {"error": "processing_failed", "detail": str(exc)})
            return

        self._json_response(200 if result.get("ok") else 400, result)


def main() -> int:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SignHandler)
    print(f"Listening on http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
