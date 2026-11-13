#!/usr/bin/env python3
import base64
import hmac
import json
import os
import re
import threading
import time
import traceback
import uuid
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ZOHO_BASE_URL = os.getenv("ZOHO_BASE_URL", "https://sign.zoho.eu").strip()
ZOHO_TEMPLATE_ID = os.getenv("ZOHO_TEMPLATE_ID", "").strip()
ZOHO_ACCOUNTS_BASE_URL = os.getenv("ZOHO_ACCOUNTS_BASE_URL", "https://accounts.zoho.eu").strip()
ZOHO_SIGN_TOKEN = os.getenv("ZOHO_SIGN_TOKEN", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
ZOHO_EMBED_LOCALE = os.getenv("ZOHO_EMBED_LOCALE", "de").strip() or "de"
ZOHO_TEMPLATE_ID_BENELUX = os.getenv("ZOHO_TEMPLATE_ID_BENELUX", "").strip()

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
DIRECTION_FIELD = "方向"


@dataclass(frozen=True)
class SignRegion:
    """Per-region signing setup, chosen from the record's 方向 field."""

    name: str
    template_env: str  # env var holding the Zoho template id
    template_id: str
    # Language of every Zoho email to the recipients (incl. the completed PDF).
    language: str
    # Language of the embedded signing page (locale= in the link). Zoho ignores locale=en and falls back
    # to the device's browser language (e.g. Chinese), so only a non-English code reliably forces the UI.
    signing_page_locale: str
    fallback_email: str
    # Feishu stores the checklist answers in German; the English template needs them translated.
    zustand_values: dict[str, str] = field(default_factory=dict)


REGION_DE = SignRegion("DE", "ZOHO_TEMPLATE_ID", ZOHO_TEMPLATE_ID, ZOHO_EMBED_LOCALE, ZOHO_EMBED_LOCALE, "service@alpha-ess.de")
REGION_BENELUX = SignRegion(
    "BENELUX",
    "ZOHO_TEMPLATE_ID_BENELUX",
    ZOHO_TEMPLATE_ID_BENELUX,
    "en",
    "nl",
    "servicebenelux@alphaess.nl",
    {"Ja": "Yes", "Nein": "No", "Nicht vorhanden": "Not available"},
)

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

# --- PDF field overflow -------------------------------------------------------
# Zoho renders template text fields in Roboto and silently clips text that does not fit the box.
# Advance widths (1/1000 em) generated from Roboto-Regular.ttf; unknown characters use a wide fallback.
_ROBOTO_CHARS = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~\xa0¡¢£¤¥¦§¨©ª«¬\xad®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ€–—‘’‚“”„…•‹›°ŒœŠšŽžŸƒ†‡‰™'
_ROBOTO_WIDTHS = (248, 257, 320, 616, 562, 732, 622, 174, 342, 348, 431, 567, 196, 276, 263, 412, 562, 562, 562, 562, 562, 562, 562, 562, 562, 562, 242, 211, 508, 549, 522, 472, 898, 652, 623, 651, 656, 568, 553, 681, 713, 272, 552, 627, 538, 873, 713, 688, 631, 688, 616, 593, 597, 648, 636, 887, 627, 601, 599, 265, 410, 265, 418, 451, 309, 544, 561, 523, 564, 530, 347, 561, 551, 243, 239, 507, 243, 876, 552, 570, 561, 568, 338, 516, 327, 551, 484, 751, 496, 473, 496, 338, 244, 338, 680, 248, 244, 547, 581, 713, 525, 240, 613, 418, 786, 447, 469, 554, 276, 786, 458, 374, 534, 367, 367, 313, 566, 489, 261, 248, 367, 455, 469, 732, 776, 778, 473, 652, 652, 652, 652, 652, 652, 935, 651, 568, 568, 568, 568, 272, 272, 272, 272, 670, 713, 688, 688, 688, 688, 688, 533, 688, 648, 648, 648, 648, 601, 591, 595, 544, 544, 544, 544, 544, 544, 844, 523, 530, 530, 530, 530, 247, 247, 247, 247, 586, 552, 570, 570, 570, 570, 570, 571, 566, 551, 551, 551, 551, 473, 576, 473, 562, 656, 781, 200, 200, 199, 354, 357, 344, 669, 337, 300, 300, 374, 954, 908, 593, 516, 599, 496, 601, 340, 551, 570, 958, 625)
ROBOTO_WIDTH = dict(zip(_ROBOTO_CHARS, _ROBOTO_WIDTHS))
ROBOTO_FALLBACK_WIDTH = 650
# Calibrated against signed PDFs: a box of height h at font size s shows int(h / (s * 1.17)) lines.
LINE_HEIGHT_FACTOR = 1.17
BOX_PADDING_PT = 2
# Multi-line boxes whose line breaks only waste space; their lines are joined into one flowing text.
JOIN_LINES_FIELDS = {"kunden_addr"}
# Serial numbers cannot be shortened, so their overflow message points to the remarks field instead.
SN_FIELDS = {"system_sn", "austasuch_sn_alte", "austasuch_sn_neue"}


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


def extract_first_email(value: str) -> str:
    if not value or not value.strip():
        return ""
    parts = re.split(r"[,;\n\r]+", value)
    for part in parts:
        part = part.strip()
        if is_valid_email(part):
            return part
    return ""


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


def _http_open(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: int) -> tuple[bytes, str]:
    for attempt in (1, 2):
        request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            # A cached Zoho access token can be revoked early (Zoho caps live tokens per refresh token);
            # fetch a fresh one and retry once.
            if attempt == 1 and exc.code == 401 and headers.get("Authorization", "").startswith("Zoho-oauthtoken ") and _uses_zoho_refresh_token():
                _TOKEN_CACHE.pop("zoho", None)
                headers = {**headers, "Authorization": f"Zoho-oauthtoken {get_zoho_access_token()}"}
                print(json.dumps({"event": "zoho.token_refreshed_after_401", "url": url}, ensure_ascii=False), flush=True)
                continue
            raise RuntimeError(f"HTTP {exc.code} calling {url}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error calling {url}: {exc}") from exc
    raise AssertionError("unreachable")


def api_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> dict:
    raw, _ = _http_open(method, url, headers, body, timeout=60)
    text = raw.decode("utf-8", errors="replace")
    return json.loads(text) if text else {}


def binary_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[bytes, str]:
    return _http_open(method, url, headers, body, timeout=120)


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


# Access tokens are reused until shortly before they expire (Zoho ~1h, Feishu ~2h).
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()
_TOKEN_REFRESH_MARGIN_SECONDS = 300


def _cached_token(name: str, fetch: Callable[[], tuple[str, int]]) -> str:
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(name)
        if cached and cached[1] - time.time() > _TOKEN_REFRESH_MARGIN_SECONDS:
            return cached[0]
        token, expires_in = fetch()
        _TOKEN_CACHE[name] = (token, time.time() + expires_in)
        return token


def _uses_zoho_refresh_token() -> bool:
    return bool(ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET)


def get_zoho_access_token() -> str:
    if _uses_zoho_refresh_token():
        return _cached_token("zoho", _fetch_zoho_access_token)
    return require_env("ZOHO_SIGN_TOKEN", ZOHO_SIGN_TOKEN)


def _fetch_zoho_access_token() -> tuple[str, int]:
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
    return access_token, int(response.get("expires_in") or 3600)


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


_CN_PUNCT_TABLE = str.maketrans("，。；：？！、（）【】「」『』《》……——", ",.;:?!,()[]\"\"''<>..--")


def normalize_cn_punctuation(text: str) -> str:
    return text.translate(_CN_PUNCT_TABLE)


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
    return _cached_token("feishu", _fetch_feishu_tenant_token)


def _fetch_feishu_tenant_token() -> tuple[str, int]:
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
    return response["tenant_access_token"], int(response.get("expire") or 7200)


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
        return {"open_id": "", "email": "", "name": ""}

    users = list_feishu_users(tenant_token)
    if target.startswith("ou_"):
        for user in users:
            open_id = str(user.get("open_id") or "").strip()
            if open_id == target:
                email = (
                    str(user.get("enterprise_email") or "").strip()
                    or str(user.get("email") or "").strip()
                )
                name = str(user.get("en_name") or user.get("name") or "").strip()
                return {"open_id": open_id, "email": email, "name": name}
        return {"open_id": target, "email": "", "name": ""}

    candidates: list[tuple[str, str, str, str]] = []  # (matched_value, open_id, email, display_name)
    for user in users:
        open_id = str(user.get("open_id") or "").strip()
        if not open_id:
            continue
        email = (
            str(user.get("enterprise_email") or "").strip()
            or str(user.get("email") or "").strip()
        )
        display_name = str(user.get("en_name") or user.get("name") or "").strip()
        for key in ("name", "en_name", "nickname", "email", "enterprise_email"):
            value = str(user.get(key) or "").strip()
            if value:
                candidates.append((value, open_id, email, display_name))

    matched = [(open_id, email, display_name) for value, open_id, email, display_name in candidates if value == target]
    unique_matches = sorted(set(matched))
    if len(unique_matches) == 1:
        resolved_open_id, resolved_email, resolved_name = unique_matches[0]
        print(
            json.dumps(
                {
                    "event": "notify_target.resolved",
                    "raw_target": raw_target,
                    "resolved_open_id": resolved_open_id,
                    "resolved_email": resolved_email,
                    "resolved_name": resolved_name,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {"open_id": resolved_open_id, "email": resolved_email, "name": resolved_name}
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


SN_SPLIT_RE = re.compile(r"[\r\n,;/，；、／]+")


def format_sn_field(raw_value: str) -> str:
    """Join SNs with '; ' on one flowing line and let Zoho wrap by width (forced line breaks wasted box space)."""
    parts = [p.strip() for p in SN_SPLIT_RE.split(raw_value.strip()) if p.strip()]
    if not parts:
        return raw_value.strip()
    return "; ".join(parts)


def map_zoho_field_value(zoho_field: str, raw_value: str) -> str:
    if zoho_field == "service_date":
        return convert_service_date(raw_value)
    if zoho_field == "service_KW":
        return convert_service_kw(raw_value)
    if zoho_field == "kunden_contact":
        first_email = extract_first_email(raw_value)
        if first_email:
            return first_email
        return extract_first_line(raw_value)
    if zoho_field in ("austasuch_sn_alte", "austasuch_sn_neue"):
        return format_sn_field(raw_value).upper()
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
        mapped[zoho_field] = normalize_cn_punctuation(map_zoho_field_value(zoho_field, get_record_field_value(record_fields, feishu_fields)))
    for zoho_field, feishu_fields in OPTIONAL_FIELD_MAPPING.items():
        if zoho_field == "Techniker":
            raw_value = extract_techniker_name(record_fields)
        else:
            raw_value = get_record_field_value(record_fields, feishu_fields)
        if raw_value:
            mapped[zoho_field] = normalize_cn_punctuation(map_zoho_field_value(zoho_field, raw_value))
    return mapped


def text_width_pt(text: str, font_size: float) -> float:
    return sum(ROBOTO_WIDTH.get(ch, ROBOTO_FALLBACK_WIDTH) for ch in text) * font_size / 1000


def count_wrapped_lines(text: str, box_width_pt: float, font_size: float) -> int:
    """Word-wrap like Zoho: break at spaces; a single word wider than the box still takes one line."""
    lines = 0
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if current and text_width_pt(candidate, font_size) > box_width_pt:
                lines += 1
                current = word
            else:
                current = candidate
        lines += 1
    return lines


def get_text_field_boxes(template: dict) -> dict[str, tuple[float, float, int]]:
    """field_label -> (usable width pt, font size pt, visible lines) for the template's text fields."""
    boxes: dict[str, tuple[float, float, int]] = {}
    for document in template.get("document_fields") or []:
        for field in document.get("fields") or []:
            if field.get("field_type_name") != "Textfield":
                continue
            font_size = float((field.get("text_property") or {}).get("font_size") or 8)
            width = float(field.get("abs_width") or 0) - BOX_PADDING_PT
            lines = max(1, int(float(field.get("abs_height") or 0) / (font_size * LINE_HEIGHT_FACTOR)))
            boxes[field["field_label"]] = (width, font_size, lines)
    return boxes


def tidy_for_box(zoho_field: str, value: str, box: tuple[float, float, int]) -> str:
    """Drop blank lines and repeated spaces. Single-line boxes get their lines joined with ', ';
    JOIN_LINES_FIELDS are joined only when their line-per-item layout would not fit the box."""
    width, font_size, max_lines = box
    lines = [line.strip() for line in re.sub(r"[ \t]+", " ", value.replace("\r", "")).split("\n") if line.strip()]
    tidy = "\n".join(lines)
    if max_lines == 1 or (zoho_field in JOIN_LINES_FIELDS and count_wrapped_lines(tidy, width, font_size) > max_lines):
        return ", ".join(line.rstrip(",;") for line in lines)
    return tidy


def fit_fields_to_boxes(mapped_fields: dict[str, str], boxes: dict[str, tuple[float, float, int]]) -> dict[str, str]:
    return {
        zoho_field: tidy_for_box(zoho_field, value, boxes[zoho_field]) if value and zoho_field in boxes else value
        for zoho_field, value in mapped_fields.items()
    }


def find_overflowing_fields(mapped_fields: dict[str, str], boxes: dict[str, tuple[float, float, int]]) -> list[str]:
    errors: list[str] = []
    for zoho_field, value in mapped_fields.items():
        if not value or zoho_field not in boxes:
            continue
        width, font_size, max_lines = boxes[zoho_field]
        needed = count_wrapped_lines(value, width, font_size)
        if needed > max_lines and zoho_field in SN_FIELDS:
            errors.append(
                f"Field '{get_field_label(zoho_field)}' has too many serial numbers for the PDF: needs {needed} lines but only {max_lines} fit. "
                f"Please move the extra serial numbers to '{get_field_label('service_anmerkungen')}'."
            )
        elif needed > max_lines:
            errors.append(
                f"Field '{get_field_label(zoho_field)}' is too long for the PDF: needs {needed} lines but only {max_lines} fit "
                f"({len(value)} characters now, about {len(value) * max_lines // needed} fit). Please shorten it."
            )
    return errors


def validate_mapped_fields(mapped_fields: dict[str, str], boxes: dict[str, tuple[float, float, int]] | None = None) -> None:
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
    if boxes:
        errors.extend(find_overflowing_fields(mapped_fields, boxes))
    if errors:
        raise ValidationError(errors)


def get_sign_region(record_fields: dict[str, Any]) -> SignRegion:
    direction = normalize_value(record_fields.get(DIRECTION_FIELD)).strip().upper()
    return REGION_BENELUX if direction == "BENELUX" else REGION_DE


def translate_zustand_values(mapped_fields: dict[str, str], region: SignRegion) -> dict[str, str]:
    return {
        zoho_field: region.zustand_values.get(value, value) if zoho_field.startswith("zustand_") else value
        for zoho_field, value in mapped_fields.items()
    }


def get_zoho_template_detail(token: str, region: SignRegion = REGION_DE) -> dict:
    # Fail loudly rather than falling back to another region's template.
    template_id = require_env(region.template_env, region.template_id)
    response = api_request("GET", f"{ZOHO_BASE_URL}/api/v1/templates/{template_id}", {"Authorization": f"Zoho-oauthtoken {token}"})
    if response.get("status") != "success":
        raise RuntimeError(f"Zoho template fetch failed: {json.dumps(response, ensure_ascii=False)}")
    return response["templates"]


def build_embedded_actions(template: dict, recipient_name: str, recipient_email: str, cc_email: str = "", cc_name: str = "", language: str = ZOHO_EMBED_LOCALE) -> list[dict]:
    actions = []
    resolved_cc_email = cc_email.strip()
    if resolved_cc_email.lower() == recipient_email.strip().lower():
        resolved_cc_email = ""
    for action in template.get("actions", []):
        atype = action.get("action_type", "SIGN")
        if atype == "VIEW":
            if not resolved_cc_email:
                continue
            actions.append(
                {
                    "action_id": action["action_id"],
                    "action_type": "VIEW",
                    "recipient_name": cc_name or resolved_cc_email.split("@")[0],
                    "recipient_email": resolved_cc_email,
                    "send_completed_document": True,
                    "language": language,
                }
            )
        else:
            actions.append(
                {
                    "action_id": action["action_id"],
                    "action_type": atype,
                    "recipient_name": recipient_name,
                    "recipient_email": recipient_email,
                    "signing_order": action.get("signing_order", 1),
                    "verify_recipient": action.get("verify_recipient", False),
                    "private_notes": action.get("private_notes", ""),
                    "send_completed_document": True,
                    "is_embedded": True,
                    "language": language,
                }
            )
    return actions


def create_zoho_embedded_request(token: str, template: dict, wo_number: str, mapped_fields: dict[str, str], recipient_email: str, cc_email: str = "", cc_name: str = "", language: str = ZOHO_EMBED_LOCALE) -> dict:
    template_id = template["template_id"]
    recipient_name = mapped_fields.get("kunden_name", "").strip() or "Kunden Unterschrift"
    resolved_recipient_email = recipient_email.strip() or "service@alpha-ess.de"
    payload = {
        "templates": {
            "request_name": build_request_name(wo_number),
            "actions": build_embedded_actions(template, recipient_name, resolved_recipient_email, cc_email, cc_name, language),
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


def extract_embed_link(payload: dict, locale: str = ZOHO_EMBED_LOCALE) -> str:
    match = re.search(r"https://[^\s\"\\]+", json.dumps(payload, ensure_ascii=False))
    if not match:
        raise RuntimeError(f"Could not find embed link in payload: {json.dumps(payload, ensure_ascii=False)}")
    return force_embed_link_locale(match.group(0), locale)


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


def _join_text_segments(value: Any) -> Any:
    """Search API returns text as [{"text": ..., "type": ...}] segments; list API returns the joined string."""
    if isinstance(value, list) and value and all(isinstance(seg, dict) and "text" in seg and "record_ids" not in seg for seg in value):
        return "".join(str(seg.get("text") or "") for seg in value)
    return value


def find_child_records(tenant_token: str, parent_rid: str) -> list[dict]:
    """Find records whose 父记录 links to parent_rid (server-side filter instead of a full-table scan)."""
    app_token = require_env("FEISHU_APP_TOKEN", FEISHU_APP_TOKEN)
    table_id = require_env("FEISHU_TABLE_ID", FEISHU_TABLE_ID)
    body = json.dumps(
        {
            "field_names": [LINK_FIELD_PARENT, SN_ALT_FIELD, SN_NEU_FIELD],
            "filter": {"conjunction": "and", "conditions": [{"field_name": LINK_FIELD_PARENT, "operator": "is", "value": [parent_rid]}]},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    children: list[dict] = []
    page_token = ""
    while True:
        query = urllib.parse.urlencode({k: v for k, v in {"page_size": 500, "page_token": page_token or None}.items() if v is not None})
        response = api_request(
            "POST",
            f"{FEISHU_BASE_URL}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search?{query}",
            {"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json; charset=utf-8"},
            body,
        )
        if response.get("code") != 0:
            raise RuntimeError(f"Feishu search child records failed: {json.dumps(response, ensure_ascii=False)}")
        data = response.get("data") or {}
        for item in data.get("items") or []:
            if item.get("record_id") == parent_rid:
                continue
            fields = {name: _join_text_segments(value) for name, value in (item.get("fields") or {}).items()}
            children.append({**item, "fields": fields})
        if not data.get("has_more"):
            return children
        page_token = data.get("page_token") or ""


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
    def load_record_and_template() -> tuple[dict, SignRegion, dict]:
        # The template depends on the record's region, so it is fetched right after the record.
        loaded = get_feishu_record_by_id(tenant_token, record_id)
        loaded_region = get_sign_region(loaded.get("fields") or {})
        return loaded, loaded_region, get_zoho_template_detail(zoho_token, loaded_region)

    # The Feishu reads take ~1-3s each server-side and are independent, so run them concurrently.
    with ThreadPoolExecutor(max_workers=3) as pool:
        identity_future = pool.submit(resolve_user_identity, tenant_token, notify_open_id)
        record_future = pool.submit(load_record_and_template)
        children_future = pool.submit(find_child_records, tenant_token, record_id)
        notify_identity = identity_future.result()
        record, region, template = record_future.result()
        children = children_future.result()
    resolved_notify_open_id = notify_identity.get("open_id", "").strip() or notify_open_id
    initiator_email = notify_identity.get("email", "").strip()
    initiator_name = notify_identity.get("name", "").strip()
    record_fields = record.get("fields") or {}

    if children:
        record_fields = merge_child_sn_fields(record_fields, children)
        print(json.dumps({"event": "process_sign_start.children_merged", "record_id": record_id, "child_count": len(children)}, ensure_ascii=False), flush=True)
    customer_email_raw = normalize_value(record_fields.get(EMAIL_FIELD))
    first_customer_email = extract_first_email(customer_email_raw)
    resolved_notify_email = first_customer_email or region.fallback_email
    resolved_wo = normalize_value(record_fields.get(WO_FIELD)) or wo_number or record_id
    print(
        json.dumps(
            {
                "event": "process_sign_start.record_loaded",
                "record_id": record_id,
                "resolved_wo": resolved_wo,
                "region": region.name,
                "template_id": template.get("template_id"),
                "resolved_notify_open_id": resolved_notify_open_id,
                "resolved_notify_email": resolved_notify_email,
                "cc_email": initiator_email if initiator_email.strip().lower() != resolved_notify_email.strip().lower() else "",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    boxes = get_text_field_boxes(template)
    mapped_fields = fit_fields_to_boxes(translate_zustand_values(build_mapped_fields(record_fields), region), boxes)
    try:
        validate_mapped_fields(mapped_fields, boxes)
    except ValidationError as exc:
        print(json.dumps({"event": "process_sign_start.validation_failed", "record_id": record_id, "resolved_wo": resolved_wo, "validation_errors": exc.details}, ensure_ascii=False), flush=True)
        feishu_response = send_feishu_text(tenant_token, resolved_notify_open_id, f"{resolved_wo} validation failed:\n" + "\n".join(exc.details))
        return {"ok": False, "wo": resolved_wo, "record_id": record_id, "error_type": "validation", "validation_errors": exc.details, "feishu_message_response": feishu_response}

    created = create_zoho_embedded_request(zoho_token, template, resolved_wo, mapped_fields, resolved_notify_email, initiator_email, initiator_name, region.language)
    action_id = next(a["action_id"] for a in created["actions"] if a.get("action_type") == "SIGN")
    request_id = created["request_id"]
    store_request_mapping(request_id, record_id, resolved_wo, action_id)
    print(json.dumps({"event": "process_sign_start.zoho_request_created", "record_id": record_id, "resolved_wo": resolved_wo, "request_id": request_id, "action_id": action_id}, ensure_ascii=False), flush=True)
    embed_link = extract_embed_link(get_embed_token_payload(zoho_token, request_id, action_id), region.signing_page_locale)
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
