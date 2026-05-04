#!/usr/bin/env python3
import base64
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
ZOHO_ACCOUNTS_BASE_URL = os.getenv("ZOHO_ACCOUNTS_BASE_URL", "https://accounts.zoho.eu").strip()
ZOHO_SIGN_TOKEN = os.getenv("ZOHO_SIGN_TOKEN", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
ZOHO_EMBED_LOCALE = os.getenv("ZOHO_EMBED_LOCALE", "de").strip() or "de"

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

WO_FIELD = "上门单号"
ATTACHMENT_FIELD_NAME = "附件"
EMAIL_FIELD = "Email Adresse"
SN_ALT_FIELD = "SN(被取回)"
SN_NEU_FIELD = "SN(被使用)"
LINK_FIELD_PARENT = "父记录"

REQUIRED_FIELD_MAPPING = {
    "service_date": ["日期"],
    "service_KW": ["周数 KW"],
    "kunden_name": ["联系人(工单)"],
    "kunden_addr": ["地址信息"],
    "kunden_contact": ["Email Adresse", "联系方式"],
    "system_modell": ["vorort_system_modell"],
    "system_sn": ["SN编号"],
    "system_bat_modell": ["vorort_system_bat_modell"],
    "system_bat_anzahl": ["vorort_system_bat_anzahl"],
    "vorort_problem": ["vorort_problem"],
    "vorort_arbeiten": ["vorort_arbeiten"],
    "zustand_Schaeden": ["Schaeden vorhanden", "zustand_Schaeden"],
    "zustand_Installationsfehler": ["Installationsfehler vorhanden", "zustand_Installationsfehler"],
    "zustand_PVfunktions": ["PV funktionsfaehig", "zustand_PVfunktions"],
    "zustand_Batterie": ["Batterie funktionsfaehig", "zustand_Batterie"],
    "zustand_WR": ["Wechselrichter funktionsfaehig", "zustand_WR"],
    "zustand_Meter": ["Meter funktionsfaehig", "zustand_Meter"],
    "zustand_WB": ["Wallbox funktionsfaehig", "zustand_WB"],
    "zustand_austausch": ["Austausch durchgefuehrt", "zustand_austausch"],
    "zustand_behoben": ["Problem behoben", "zustand_behoben"],
}

OPTIONAL_FIELD_MAPPING = {
    "austasuch_sn_alte": ["SN(被取回)"],
    "austasuch_sn_neue": ["SN(被使用)"],
    "service_anmerkungen": ["vorort_anmerkungen"],
    "Abfahrt": ["Abfahrt (Uhr)"],
    "Ankunft": ["Ankunft (Uhr)"],
    "Von": ["Arbeitzeit_Von (Uhr)"],
    "Bis": ["Arbeitzeit_Bis (Uhr)"],
    "Entfernung": ["Entfernung (km)"],
    "Techniker": ["人员"],
}

FIELD_LABELS = {
    "service_date": "日期",
    "service_KW": "周数 KW",
    "kunden_name": "联系人(工单)",
    "kunden_addr": "地址信息",
    "kunden_contact": "联系方式",
    "system_modell": "vorort_system_modell",
    "system_sn": "SN编号",
    "system_bat_modell": "vorort_system_bat_modell",
    "system_bat_anzahl": "vorort_system_bat_anzahl",
    "vorort_problem": "vorort_problem",
    "vorort_arbeiten": "vorort_arbeiten",
    "service_anmerkungen": "vorort_anmerkungen",
    "austasuch_sn_alte": "SN(被取回)",
    "austasuch_sn_neue": "SN(被使用)",
    "Abfahrt": "Abfahrt (Uhr)",
    "Ankunft": "Ankunft (Uhr)",
    "Von": "Arbeitzeit_Von (Uhr)",
    "Bis": "Arbeitzeit_Bis (Uhr)",
    "Entfernung": "Entfernung (km)",
    "Techniker": "Techniker",
    "zustand_Schaeden": "Schaeden vorhanden",
    "zustand_Installationsfehler": "Installationsfehler vorhanden",
    "zustand_PVfunktions": "PV funktionsfaehig",
    "zustand_Batterie": "Batterie funktionsfaehig",
    "zustand_WR": "Wechselrichter funktionsfaehig",
    "zustand_Meter": "Meter funktionsfaehig",
    "zustand_WB": "Wallbox funktionsfaehig",
    "zustand_austausch": "Austausch durchgefuehrt",
    "zustand_behoben": "Problem behoben",
}

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
CHINESE_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


class ValidationError(Exception):
    def __init__(self, details: list[str]):
        super().__init__("Validation failed")
        self.details = details

def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))

def extract_first_line(value: str) -> str:
    lines = value.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return value.strip()


def infer_english_given_name_from_email(email: str) -> str:
    """Extract English given name from email local part, e.g. marco.xue@... -> Marco."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    local_part = email.split("@", 1)[0].strip()
    if not local_part:
        return ""
    first_part = local_part.split(".", 1)[0].split("_", 1)[0].split("-", 1)[0].strip()
    if not first_part:
        return ""
    return first_part[:1].upper() + first_part[1:]


def extract_techniker_name(record_fields: dict[str, Any]) -> str:
    """Extract English given names from the Feishu '人员' (Personnel) field.
    1 person → 'Marco'
    2 people → 'Marco & Tri'
    """
    personnel = record_fields.get("人员")
    if not isinstance(personnel, list) or not personnel:
        return ""

    def resolve_name(item: Any) -> str:
        if isinstance(item, dict):
            email = str(item.get("email") or "").strip()
            en_name = str(item.get("en_name") or "").strip()
            name = str(item.get("name") or "").strip()
            return infer_english_given_name_from_email(email) or en_name or name
        return str(item).strip()

    names = [n for n in (resolve_name(p) for p in personnel[:2]) if n]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} & {names[1]}"


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
    expected = base64.b64encode(hmac.new(secret.encode("utf-8"), raw_body, "sha256").digest()).decode("utf-8")
    return bool(signature) and hmac.compare_digest(signature.strip().lower(), expected.lower())


def get_zoho_access_token() -> str:
    if ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET:
        response = api_request(
            "POST",
            f"{ZOHO_ACCOUNTS_BASE_URL}/oauth/v2/token",
            {"Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode(
                {
                    "refresh_token": ZOHO_REFRESH_TOKEN,
                    "client_id": ZOHO_CLIENT_ID,
                    "client_secret": ZOHO_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                }
            ).encode("utf-8"),
        )
        access_token = str(response.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"Zoho refresh-token exchange returned no access_token: {json.dumps(response, ensure_ascii=False)}")
        return access_token
    return require_env("ZOHO_SIGN_TOKEN", ZOHO_SIGN_TOKEN)


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


def get_record_field_value(record_fields: dict[str, Any], field_names: list[str]) -> str:
    for field_name in field_names:
        if field_name in record_fields:
            value = normalize_value(record_fields.get(field_name))
            if value:
                return value
    for field_name in field_names:
        if field_name in record_fields:
            return normalize_value(record_fields.get(field_name))
    return ""


def get_field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


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


def resolve_user_identity(tenant_token: str, raw_target: str) -> dict[str, str]:
    target = raw_target.strip()
    if not target:
        return {"open_id": "", "email": ""}

    users = list_feishu_users(tenant_token)
    if target.startswith("ou_"):
        for user in users:
            open_id = str(user.get("open_id") or "").strip()
            if open_id == target:
                email = (
                    str(user.get("enterprise_email") or "").strip()
                    or str(user.get("email") or "").strip()
                )
                return {"open_id": open_id, "email": email}
        return {"open_id": target, "email": ""}

    candidates: list[tuple[str, str, str]] = []
    for user in list_feishu_users(tenant_token):
        open_id = str(user.get("open_id") or "").strip()
        if not open_id:
            continue
        email = (
            str(user.get("enterprise_email") or "").strip()
            or str(user.get("email") or "").strip()
        )
        for key in ("name", "en_name", "nickname", "email", "enterprise_email"):
            value = str(user.get(key) or "").strip()
            if value:
                candidates.append((value, open_id, email))

    matched = [(open_id, email) for value, open_id, email in candidates if value == target]
    unique_matches = sorted(set(matched))
    if len(unique_matches) == 1:
        resolved_open_id, resolved_email = unique_matches[0]
        print(
            json.dumps(
                {
                    "event": "notify_target.resolved",
                    "raw_target": raw_target,
                    "resolved_open_id": resolved_open_id,
                    "resolved_email": resolved_email,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {"open_id": resolved_open_id, "email": resolved_email}
    if len(unique_matches) > 1:
        raise RuntimeError(f"Multiple Feishu users matched notify target: {raw_target}")
    raise RuntimeError(f"Could not resolve Feishu open_id from notify target: {raw_target}")


def resolve_open_id(tenant_token: str, raw_target: str) -> str:
    return resolve_user_identity(tenant_token, raw_target).get("open_id", "")


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


SN_SPLIT_RE = re.compile(r"[\n,;/]+")


def format_sn_field(raw_value: str) -> str:
    """Distribute SNs across at most 2 lines, ;-separated per line."""
    parts = [p.strip() for p in SN_SPLIT_RE.split(raw_value.strip()) if p.strip()]
    if not parts:
        return raw_value.strip()
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}; {parts[1]}"

    # 3+ parts: distribute evenly across 2 lines
    per_line = (len(parts) + 1) // 2  # ceil(N/2)
    line1 = "; ".join(parts[:per_line])
    line2 = "; ".join(parts[per_line:])
    return f"{line1}\n{line2}"


def map_zoho_field_value(zoho_field: str, raw_value: str) -> str:
    if zoho_field == "service_date":
        return convert_service_date(raw_value)
    if zoho_field == "service_KW":
        return convert_service_kw(raw_value)
    if zoho_field == "kunden_contact":
        if not is_valid_email(raw_value):
            return extract_first_line(raw_value)
        return raw_value
    if zoho_field in ("austasuch_sn_alte", "austasuch_sn_neue"):
        return format_sn_field(raw_value)
    return raw_value


def build_request_name(wo_number: str) -> str:
    return f"VorortProtocol_{wo_number}"


def extract_wo_from_request_name(request_name: str) -> str:
    prefix = "VorortProtocol_"
    if request_name.startswith(prefix):
        return request_name[len(prefix) :].strip()
    return request_name.strip()


def build_mapped_fields(record_fields: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for zoho_field, feishu_fields in REQUIRED_FIELD_MAPPING.items():
        mapped[zoho_field] = map_zoho_field_value(zoho_field, get_record_field_value(record_fields, feishu_fields))
    for zoho_field, feishu_fields in OPTIONAL_FIELD_MAPPING.items():
        if zoho_field == "Techniker":
            raw_value = extract_techniker_name(record_fields)
        else:
            raw_value = get_record_field_value(record_fields, feishu_fields)
        if raw_value:
            mapped[zoho_field] = map_zoho_field_value(zoho_field, raw_value)
    return mapped


def validate_mapped_fields(mapped_fields: dict[str, str]) -> None:
    errors: list[str] = []
    for zoho_field in REQUIRED_FIELD_MAPPING:
        value = mapped_fields.get(zoho_field, "").strip()
        field_label = get_field_label(zoho_field)
        if not value:
            errors.append(f"Field '{field_label}' is invalid: value is required but currently empty.")
        elif CHINESE_RE.search(value):
            errors.append(f"Field '{field_label}' is invalid: value should not contain Chinese characters. Current value: {value}")
    for zoho_field, value in mapped_fields.items():
        if value and CHINESE_RE.search(value):
            message = f"Field '{get_field_label(zoho_field)}' is invalid: value should not contain Chinese characters. Current value: {value}"
            if message not in errors:
                errors.append(message)
    if mapped_fields.get("zustand_austausch", "").strip() == "Ja":
        for field_name in ("austasuch_sn_alte", "austasuch_sn_neue"):
            if not mapped_fields.get(field_name, "").strip():
                errors.append(
                    f"Field '{get_field_label(field_name)}' is invalid: value is required when 'Austausch durchgefuehrt' is 'Ja'."
                )
    if errors:
        raise ValidationError(errors)


def get_zoho_template_detail(token: str) -> dict:
    template_id = require_env("ZOHO_TEMPLATE_ID", ZOHO_TEMPLATE_ID)
    response = api_request("GET", f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}", {"Authorization": f"Zoho-oauthtoken {token}"})
    if response.get("status") != "success":
        raise RuntimeError(f"Zoho template fetch failed: {json.dumps(response, ensure_ascii=False)}")
    return response["templates"]


def build_embedded_actions(template: dict, recipient_name: str, recipient_email: str) -> list[dict]:
    actions = []
    for action in template.get("actions", []):
        actions.append(
            {
                "action_id": action["action_id"],
                "action_type": action["action_type"],
                "recipient_name": recipient_name,
                "recipient_email": recipient_email,
                "signing_order": action.get("signing_order", 1),
                "verify_recipient": action.get("verify_recipient", False),
                "private_notes": action.get("private_notes", ""),
                "send_completed_document": True,
                "is_embedded": True,
            }
        )
    return actions


def create_zoho_embedded_request(token: str, wo_number: str, mapped_fields: dict[str, str], recipient_email: str) -> dict:
    template_id = require_env("ZOHO_TEMPLATE_ID", ZOHO_TEMPLATE_ID)
    template = get_zoho_template_detail(token)
    recipient_name = mapped_fields.get("kunden_name", "").strip() or "Kunden Unterschrift"
    resolved_recipient_email = recipient_email.strip() or "service@alpha-ess.de"
    payload = {
        "templates": {
            "request_name": build_request_name(wo_number),
            "actions": build_embedded_actions(template, recipient_name, resolved_recipient_email),
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
    return force_embed_link_locale(match.group(0))


def force_embed_link_locale(url: str, locale: str = ZOHO_EMBED_LOCALE) -> str:
    parsed = urllib.parse.urlsplit(url)
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_items = [(key, value) for key, value in query_items if key.lower() != "locale"]
    query_items.append(("locale", locale))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query_items), parsed.fragment)
    )


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


def find_child_records(tenant_token: str, parent_rid: str) -> list[dict]:
    """Find records whose 父记录 links to parent_rid."""
    children: list[dict] = []
    for item in list_feishu_records(tenant_token):
        if item["record_id"] == parent_rid:
            continue
        link_val = (item.get("fields") or {}).get(LINK_FIELD_PARENT)
        if not isinstance(link_val, list):
            continue
        for entry in link_val:
            rids = entry.get("record_ids") if isinstance(entry, dict) else None
            if rids and parent_rid in rids:
                children.append(item)
                break
    return children


def merge_child_sn_fields(record_fields: dict[str, Any], child_records: list[dict]) -> dict[str, Any]:
    """Merge SN(被取回) and SN(被使用) from child records into parent fields."""
    alt_parts = [normalize_value(record_fields.get(SN_ALT_FIELD))]
    neu_parts = [normalize_value(record_fields.get(SN_NEU_FIELD))]

    for child in child_records:
        cfields = child.get("fields") or {}
        alt_v = normalize_value(cfields.get(SN_ALT_FIELD))
        neu_v = normalize_value(cfields.get(SN_NEU_FIELD))
        if alt_v:
            alt_parts.append(alt_v)
        if neu_v:
            neu_parts.append(neu_v)

    alt_merged = ", ".join(v for v in alt_parts if v)
    neu_merged = ", ".join(v for v in neu_parts if v)

    merged = dict(record_fields)
    if alt_merged:
        merged[SN_ALT_FIELD] = alt_merged
    if neu_merged:
        merged[SN_NEU_FIELD] = neu_merged
    return merged


def process_sign_start(record_id: str, notify_open_id: str, zoho_token: str, wo_number: str = "") -> dict:
    print(json.dumps({"event": "process_sign_start.begin", "record_id": record_id, "notify_open_id": notify_open_id, "wo_number_hint": wo_number}, ensure_ascii=False), flush=True)
    tenant_token = get_feishu_tenant_token()
    notify_identity = resolve_user_identity(tenant_token, notify_open_id)
    resolved_notify_open_id = notify_identity.get("open_id", "").strip() or notify_open_id
    initiator_email = notify_identity.get("email", "").strip()
    record = get_feishu_record_by_id(tenant_token, record_id)
    record_fields = record.get("fields") or {}

    children = find_child_records(tenant_token, record_id)
    if children:
        record_fields = merge_child_sn_fields(record_fields, children)
        print(json.dumps({"event": "process_sign_start.children_merged", "record_id": record_id, "child_count": len(children)}, ensure_ascii=False), flush=True)
    customer_email = normalize_value(record_fields.get(EMAIL_FIELD))
    resolved_notify_email = customer_email if is_valid_email(customer_email) else initiator_email
    resolved_wo = normalize_value(record_fields.get(WO_FIELD)) or wo_number or record_id
    print(
        json.dumps(
            {
                "event": "process_sign_start.record_loaded",
                "record_id": record_id,
                "resolved_wo": resolved_wo,
                "resolved_notify_open_id": resolved_notify_open_id,
                "resolved_notify_email": resolved_notify_email,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    mapped_fields = build_mapped_fields(record_fields)
    try:
        validate_mapped_fields(mapped_fields)
    except ValidationError as exc:
        print(json.dumps({"event": "process_sign_start.validation_failed", "record_id": record_id, "resolved_wo": resolved_wo, "validation_errors": exc.details}, ensure_ascii=False), flush=True)
        feishu_response = send_feishu_text(tenant_token, resolved_notify_open_id, f"{resolved_wo} validation failed:\n" + "\n".join(exc.details))
        return {"ok": False, "wo": resolved_wo, "record_id": record_id, "error_type": "validation", "validation_errors": exc.details, "feishu_message_response": feishu_response}

    created = create_zoho_embedded_request(zoho_token, resolved_wo, mapped_fields, resolved_notify_email)
    action_id = created["actions"][0]["action_id"]
    request_id = created["request_id"]
    store_request_mapping(request_id, record_id, resolved_wo, action_id)
    print(json.dumps({"event": "process_sign_start.zoho_request_created", "record_id": record_id, "resolved_wo": resolved_wo, "request_id": request_id, "action_id": action_id}, ensure_ascii=False), flush=True)
    embed_link = extract_embed_link(get_embed_token_payload(zoho_token, request_id, action_id))
    feishu_response = send_feishu_text(tenant_token, resolved_notify_open_id, f"{resolved_wo} embedded sign link:\n{embed_link}")
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
        resolved_wo = extract_wo_from_request_name(request_name) if request_name else request_id
        record_id = find_record_by_wo(tenant_token, resolved_wo).get("record_id", "")

    if not record_id:
        raise RuntimeError(f"No Feishu record mapping found for request_id={request_id}")

    normalized_event = f"{event_name} {request_status}".lower()
    should_writeback = any(token in normalized_event for token in ["completed", "signed"]) or request_status.lower() == "completed"
    if not should_writeback:
        return {"ok": True, "ignored": True, "request_id": request_id, "record_id": record_id, "reason": "event_not_relevant"}

    zoho_token = get_zoho_access_token()
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
        if self.path.startswith("/sign/embed"):
            self.handle_embed_redirect()
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

        try:
            result = process_sign_start(record_id, notify_open_id, get_zoho_access_token(), wo_number=wo_number)
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
