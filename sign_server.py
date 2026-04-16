#!/usr/bin/env python3
import hmac
import json
import os
import re
import traceback
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
ZOHO_WEBHOOK_SECRET = os.getenv("ZOHO_WEBHOOK_SECRET", "").strip()
REQUEST_MAP_FILE = Path(os.getenv("REQUEST_MAP_FILE", "request_map.json"))

try:
    BERLIN_TZ = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:
    BERLIN_TZ = None

WO_FIELD = "\u4e0a\u95e8\u5355\u53f7"
ATTACHMENT_FIELD_NAME = "\u9644\u4ef6"

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


def binary_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[bytes, str]:
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read(), response.headers.get("Content-Type", "")
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


def verify_zoho_webhook_signature(raw_body: bytes, signature: str) -> bool:
    secret = require_env("ZOHO_WEBHOOK_SECRET", ZOHO_WEBHOOK_SECRET)
    expected = hmac.new(secret.encode("utf-8"), raw_body, "sha256").hexdigest()
    return bool(signature) and hmac.compare_digest(signature.strip().lower(), expected.lower())


def load_request_map() -> dict[str, dict[str, str]]:
    if not REQUEST_MAP_FILE.exists():
        return {}
    return json.loads(REQUEST_MAP_FILE.read_text(encoding="utf-8"))


def save_request_map(request_map: dict[str, dict[str, str]]) -> None:
    REQUEST_MAP_FILE.write_text(json.dumps(request_map, ensure_ascii=False, indent=2), encoding="utf-8")


def store_request_mapping(request_id: str, record_id: str, wo: str, action_id: str) -> None:
    request_map = load_request_map()
    request_map[request_id] = {
        "record_id": record_id,
        "wo": wo,
        "action_id": action_id,
        "stored_at": datetime.now(UTC).isoformat(),
    }
    save_request_map(request_map)


def get_request_mapping(request_id: str) -> dict[str, str] | None:
    return load_request_map().get(request_id)


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
        response = api_request(
            "GET",
            f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?{query}",
            {"Authorization": f"Bearer {tenant_token}"},
        )
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


def list_feishu_users(tenant_token: str) -> list[dict]:
    users: list[dict] = []
    page_token = ""
    while True:
        query = urllib.parse.urlencode(
            {k: v for k, v in {"page_size": 100, "page_token": page_token or None}.items() if v is not None}
        )
        response = api_request(
            "GET",
            f"{FEISHU_BASE_URL}/open-apis/contact/v3/users?{query}",
            {"Authorization": f"Bearer {tenant_token}"},
        )
        if response.get("code") != 0:
            raise RuntimeError(f"Feishu list users failed: {json.dumps(response, ensure_ascii=False)}")
        data = response.get("data") or {}
        users.extend(data.get("items") or [])
        if not data.get("has_more"):
            return users
        page_token = data.get("page_token") or ""


def resolve_open_id(tenant_token: str, raw_target: str) -> str:
    target = raw_target.strip()
    if not target:
        return target
    if target.startswith("ou_"):
        return target

    candidates: list[tuple[str, str]] = []
    for user in list_feishu_users(tenant_token):
        open_id = str(user.get("open_id") or "").strip()
        if not open_id:
            continue
        for key in ("name", "en_name", "nickname", "email", "enterprise_email"):
            value = str(user.get(key) or "").strip()
            if value:
                candidates.append((value, open_id))

    matched_ids = [open_id for value, open_id in candidates if value == target]
    unique_ids = sorted(set(matched_ids))
    if len(unique_ids) == 1:
        print(json.dumps({"event": "notify_target.resolved", "raw_target": raw_target, "resolved_open_id": unique_ids[0]}, ensure_ascii=False), flush=True)
        return unique_ids[0]
    if len(unique_ids) > 1:
        raise RuntimeError(f"Multiple Feishu users matched notify target: {raw_target}")
    raise RuntimeError(f"Could not resolve Feishu open_id from notify target: {raw_target}")


def find_record_by_wo(tenant_token: str, wo_number: str) -> dict:
    for item in list_feishu_records(tenant_token):
        fields = item.get("fields") or {}
        if normalize_value(fields.get(WO_FIELD)) == wo_number:
            return item
    raise RuntimeError(f"No Feishu record found for {wo_number}")


def convert_service_date(raw: str) -> str:
    raw = raw.strip()
    if raw.isdigit():
        dt_utc = datetime.fromtimestamp(int(raw) / 1000, UTC)
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
        mapped[zoho_field] = map_zoho_field_value(zoho_field, normalize_value(record_fields.get(feishu_field)))
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
    response = api_request("GET", f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}", {"Authorization": f"Zoho-oauthtoken {token}"})
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
            "field_data": {"field_text_data": mapped_fields, "field_date_data": {}, "field_boolean_data": {}},
        }
    }
    response = api_request(
        "POST",
        f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}/createdocument",
        {"Authorization": f"Zoho-oauthtoken {token}"},
        urllib.parse.urlencode({"data": json.dumps(payload, ensure_ascii=False)}).encode("utf-8"),
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
    match = re.search(r"https://[^\s\"\\]+", json.dumps(payload, ensure_ascii=False))
    if not match:
        raise RuntimeError(f"Could not find embed link in payload: {json.dumps(payload, ensure_ascii=False)}")
    return match.group(0)


def send_feishu_text(tenant_token: str, open_id: str, text: str) -> dict:
    resolved_open_id = resolve_open_id(tenant_token, open_id)
    payload = {"receive_id": resolved_open_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
    return api_request(
        "POST",
        f"{FEISHU_BASE_URL}/open-apis/im/v1/messages?receive_id_type=open_id",
        {"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def upload_feishu_file(tenant_token: str, filename: str, file_bytes: bytes) -> str:
    app_token = require_env("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
    boundary = "----OnsiteSignBoundary" + uuid.uuid4().hex
    crlf = "\r\n"
    parts: list[bytes] = []

    def add_field(name: str, value: str | int) -> None:
        parts.append(f"--{boundary}{crlf}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}'.encode())
        parts.append(str(value).encode("utf-8"))
        parts.append(crlf.encode())

    def add_file(name: str, filename_value: str, content: bytes, content_type: str) -> None:
        parts.append(f"--{boundary}{crlf}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename_value}"{crlf}'.encode())
        parts.append(f"Content-Type: {content_type}{crlf}{crlf}".encode())
        parts.append(content)
        parts.append(crlf.encode())

    add_field("file_name", filename)
    add_field("parent_type", "bitable_file")
    add_field("parent_node", app_token)
    add_field("size", len(file_bytes))
    add_file("file", filename, file_bytes, "application/pdf")
    parts.append(f"--{boundary}--{crlf}".encode())

    response = api_request(
        "POST",
        f"{FEISHU_BASE_URL}/open-apis/drive/v1/medias/upload_all",
        {
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        b"".join(parts),
    )
    if response.get("code") != 0:
        raise RuntimeError(f"Feishu upload failed: {json.dumps(response, ensure_ascii=False)}")
    file_token = ((response.get("data") or {}).get("file_token") or "").strip()
    if not file_token:
        raise RuntimeError(f"Feishu upload returned no file_token: {json.dumps(response, ensure_ascii=False)}")
    return file_token


def update_feishu_attachment_field(tenant_token: str, record_id: str, file_token: str, filename: str) -> dict:
    app_token = require_env("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
    table_id = require_env("FEISHU_TABLE_ID", FEISHU_TABLE_ID)
    payload = {"fields": {ATTACHMENT_FIELD_NAME: [{"file_token": file_token}]}}
    response = api_request(
        "PUT",
        f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        {"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    if response.get("code") != 0:
        raise RuntimeError(f"Feishu record update failed: {json.dumps(response, ensure_ascii=False)}")
    print(json.dumps({"event": "feishu.attachment_updated", "record_id": record_id, "filename": filename, "file_token": file_token}, ensure_ascii=False), flush=True)
    return response


def download_zoho_signed_pdf(token: str, request_id: str, document_id: str | None = None) -> tuple[bytes, str]:
    url = (
        f"{ZOHO_BASE_URL}/api/v1/requests/{request_id}/documents/{document_id}/pdf"
        if document_id
        else f"{ZOHO_BASE_URL}/api/v1/requests/{request_id}/pdf"
    )
    return binary_request("GET", url, {"Authorization": f"Zoho-oauthtoken {token}"})


def walk_json(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)


def first_string_by_key(payload: Any, target_keys: set[str]) -> str:
    for key, value in walk_json(payload):
        if key in target_keys and isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def find_document_id(payload: Any) -> str:
    for key, value in walk_json(payload):
        if key == "document_id" and isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def process_sign_start(record_id: str, notify_open_id: str, zoho_token: str, wo_number: str = "") -> dict:
    print(json.dumps({"event": "process_sign_start.begin", "record_id": record_id, "notify_open_id": notify_open_id, "wo_number_hint": wo_number}, ensure_ascii=False), flush=True)
    tenant_token = get_feishu_tenant_token()
    record = get_feishu_record_by_id(tenant_token, record_id)
    record_fields = record.get("fields") or {}
    resolved_wo = normalize_value(record_fields.get(WO_FIELD)) or wo_number or record_id
    print(json.dumps({"event": "process_sign_start.record_loaded", "record_id": record_id, "resolved_wo": resolved_wo}, ensure_ascii=False), flush=True)
    mapped_fields = build_mapped_fields(record_fields)
    try:
        validate_mapped_fields(mapped_fields)
    except ValidationError as exc:
        print(json.dumps({"event": "process_sign_start.validation_failed", "record_id": record_id, "resolved_wo": resolved_wo, "validation_errors": exc.details}, ensure_ascii=False), flush=True)
        feishu_response = send_feishu_text(tenant_token, notify_open_id, f"{resolved_wo} validation failed:\n" + "\n".join(exc.details))
        return {"ok": False, "wo": resolved_wo, "record_id": record_id, "error_type": "validation", "validation_errors": exc.details, "feishu_message_response": feishu_response}

    created = create_zoho_embedded_request(zoho_token, resolved_wo, mapped_fields)
    action_id = created["actions"][0]["action_id"]
    request_id = created["request_id"]
    store_request_mapping(request_id, record_id, resolved_wo, action_id)
    print(json.dumps({"event": "process_sign_start.zoho_request_created", "record_id": record_id, "resolved_wo": resolved_wo, "request_id": request_id, "action_id": action_id}, ensure_ascii=False), flush=True)
    embed_link = extract_embed_link(get_embed_token_payload(zoho_token, request_id, action_id))
    feishu_response = send_feishu_text(tenant_token, notify_open_id, f"{resolved_wo} embedded sign link:\n{embed_link}")
    print(json.dumps({"event": "process_sign_start.completed", "record_id": record_id, "resolved_wo": resolved_wo, "request_id": request_id, "message_sent": feishu_response.get("msg")}, ensure_ascii=False), flush=True)
    return {"ok": True, "wo": resolved_wo, "record_id": record_id, "request_id": request_id, "action_id": action_id, "request_status": created.get("request_status"), "embed_link": embed_link, "feishu_message_response": feishu_response}


def process_zoho_webhook(payload: dict, raw_body: bytes) -> dict:
    request_id = first_string_by_key(payload, {"request_id"})
    request_name = first_string_by_key(payload, {"request_name"})
    event_name = first_string_by_key(payload, {"event_type", "event", "operation"})
    request_status = first_string_by_key(payload, {"request_status", "status"})
    document_id = find_document_id(payload)
    print(json.dumps({"event": "zoho.webhook.received", "request_id": request_id, "request_name": request_name, "event_name": event_name, "request_status": request_status, "document_id": document_id, "body_len": len(raw_body)}, ensure_ascii=False), flush=True)
    if not request_id:
        raise RuntimeError("Zoho webhook payload does not contain request_id")

    mapping = get_request_mapping(request_id)
    if mapping:
        record_id = mapping.get("record_id", "")
        resolved_wo = mapping.get("wo", "") or request_name or request_id
    else:
        tenant_token = get_feishu_tenant_token()
        resolved_wo = request_name or request_id
        record_id = find_record_by_wo(tenant_token, resolved_wo).get("record_id", "")

    if not record_id:
        raise RuntimeError(f"No Feishu record mapping found for request_id={request_id}")

    normalized_event = f"{event_name} {request_status}".lower()
    should_writeback = any(token in normalized_event for token in ["completed", "signed"]) or request_status.lower() == "completed"
    if not should_writeback:
        return {"ok": True, "ignored": True, "request_id": request_id, "record_id": record_id, "reason": "event_not_relevant"}

    zoho_token = require_env("ZOHO_SIGN_TOKEN", os.getenv("ZOHO_SIGN_TOKEN", "").strip())
    pdf_bytes, content_type = download_zoho_signed_pdf(zoho_token, request_id, document_id or None)
    tenant_token = get_feishu_tenant_token()
    filename = f"{resolved_wo}-signed.pdf"
    file_token = upload_feishu_file(tenant_token, filename, pdf_bytes)
    update_feishu_attachment_field(tenant_token, record_id, file_token, filename)
    return {"ok": True, "request_id": request_id, "record_id": record_id, "wo": resolved_wo, "file_token": file_token, "content_type": content_type, "pdf_size": len(pdf_bytes)}


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
        if self.path == "/webhooks/zoho-sign":
            self.handle_zoho_webhook()
            return
        if self.path != "/sign/start":
            self._json_response(404, {"error": "not_found"})
            return

        print(json.dumps({"event": "http.post.sign_start.received", "client_address": self.client_address[0] if self.client_address else "", "path": self.path}, ensure_ascii=False), flush=True)
        try:
            authorized = is_authorized(self)
        except RuntimeError as exc:
            print(json.dumps({"event": "http.post.sign_start.auth_config_missing", "detail": str(exc)}, ensure_ascii=False), flush=True)
            self._json_response(500, {"error": "missing_trigger_auth_token", "detail": str(exc)})
            return
        if not authorized:
            print(json.dumps({"event": "http.post.sign_start.unauthorized", "client_address": self.client_address[0] if self.client_address else ""}, ensure_ascii=False), flush=True)
            self._json_response(401, {"error": "unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            print(json.dumps({"event": "http.post.sign_start.invalid_json", "detail": str(exc)}, ensure_ascii=False), flush=True)
            self._json_response(400, {"error": "invalid_json", "detail": str(exc)})
            return

        record_id = str(payload.get("record_id") or "").strip()
        trigger_open_id = str(payload.get("trigger_open_id") or "").strip()
        wo_number = str(payload.get("wo") or payload.get("WO") or "").strip()
        notify_open_id = str(payload.get("notify_open_id") or trigger_open_id or DEFAULT_NOTIFY_OPEN_ID).strip()
        print(json.dumps({"event": "http.post.sign_start.parsed", "record_id": record_id, "trigger_open_id": trigger_open_id, "notify_open_id": notify_open_id, "wo_number": wo_number}, ensure_ascii=False), flush=True)
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
            print(json.dumps({"event": "http.post.sign_start.processing_failed", "record_id": record_id, "notify_open_id": notify_open_id, "detail": str(exc)}, ensure_ascii=False), flush=True)
            traceback.print_exc()
            try:
                tenant_token = get_feishu_tenant_token()
                error_label = wo_number or record_id
                send_feishu_text(tenant_token, notify_open_id, f"{error_label} sign start failed:\n{exc}")
            except Exception:
                pass
            self._json_response(500, {"error": "processing_failed", "detail": str(exc)})
            return

        self._json_response(200 if result.get("ok") else 400, result)

    def handle_zoho_webhook(self) -> None:
        print(json.dumps({"event": "http.post.zoho_webhook.received", "client_address": self.client_address[0] if self.client_address else "", "path": self.path}, ensure_ascii=False), flush=True)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            signature = self.headers.get("X-ZS-WEBHOOK-SIGNATURE", "").strip() or self.headers.get("X-Zs-Webhook-Signature", "").strip()
            if not verify_zoho_webhook_signature(raw_body, signature):
                self._json_response(401, {"error": "invalid_zoho_webhook_signature"})
                return
            payload = json.loads(raw_body.decode("utf-8"))
            result = process_zoho_webhook(payload, raw_body)
            self._json_response(200, result)
        except Exception as exc:
            print(json.dumps({"event": "http.post.zoho_webhook.failed", "detail": str(exc)}, ensure_ascii=False), flush=True)
            traceback.print_exc()
            self._json_response(500, {"error": "zoho_webhook_failed", "detail": str(exc)})


def main() -> int:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SignHandler)
    print(json.dumps({"event": "server.start", "port": port}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
