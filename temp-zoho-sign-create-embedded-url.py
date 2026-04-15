#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://sign.zoho.eu"


def api_request(method: str, path: str, token: str, form_data: dict | None = None) -> dict:
    base_url = os.getenv("ZOHO_SIGN_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
    }
    data = None
    if form_data is not None:
        data = urllib.parse.urlencode(form_data).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc}") from exc


def get_template_detail(token: str, template_id: str) -> dict:
    payload = api_request("GET", f"/api/v1/templates/{template_id}", token)
    if payload.get("status") != "success":
        raise RuntimeError(f"Template fetch failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload["templates"]


def build_text_value(label: str, default_value) -> str:
    if default_value not in (None, "", False):
        return str(default_value)

    overrides = {
        "service_date": "15.04.2026",
        "service_KW": "16",
        "kunden_name": "Marco Xue",
        "kunden_addr": "Musterstrasse 12\n60311 Frankfurt am Main",
        "kunden_contact": "+49 15123456789\nmarco.xue@alpha-ess.de",
        "system_modell": "AlphaESS SMILE-G3",
        "system_sn": "SN-TEST-0005",
        "system_bat_modell": "BAT-TEST-02",
        "system_bat_anzahl": "2",
        "vorort_problem": "Vor-Ort-Test fuer Embedded Signing.",
        "vorort_arbeiten": "Diagnose, Testlauf und Abschlusskontrolle wurden simuliert.",
        "service_anmerkungen": "Automatisch per API erzeugter Embedded-Signing-Test.",
        "austasuch_sn_alt": "OLD-SN-0005",
        "austasuch_sn_neue": "NEW-SN-0005",
    }
    return overrides.get(label, f"test_{label}")


def build_field_data(template: dict) -> dict:
    text_data = {}
    date_data = {}
    boolean_data = {}

    for document in template.get("document_fields", []):
        for field in document.get("fields", []):
            category = (field.get("field_category") or "").lower()
            label = field.get("field_label")
            if not label:
                continue
            default_value = field.get("default_value")

            if category in {"textfield", "dropdown"}:
                text_data[label] = build_text_value(label, default_value)
            elif category == "datefield":
                date_data[label] = "15 April 2026"
            elif category == "checkbox":
                boolean_data[label] = False

    for label in [
        "zustand_schaeden_nein",
        "zustand_installationfehler_nein",
        "zustand_pv_ja",
        "zustand_bat_ja",
        "zustand_wr_ja",
        "zustand_meter_ja",
        "zustand_wb_ja",
        "zustand_behoben_ja",
    ]:
        if label in boolean_data:
            boolean_data[label] = True

    return {
        "field_text_data": text_data,
        "field_date_data": date_data,
        "field_boolean_data": boolean_data,
    }


def build_actions(template: dict, recipient_name: str, recipient_email: str) -> list[dict]:
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
                "is_embedded": True,
            }
        )
    return actions


def create_request(token: str, template_id: str, recipient_name: str, recipient_email: str, request_name: str) -> dict:
    template = get_template_detail(token, template_id)
    data = {
        "templates": {
            "request_name": request_name,
            "actions": build_actions(template, recipient_name, recipient_email),
            "field_data": build_field_data(template),
        }
    }
    payload = api_request(
        "POST",
        f"/api/v1/templates/{template_id}/createdocument",
        token,
        form_data={"data": json.dumps(data, ensure_ascii=False)},
    )
    if payload.get("status") != "success":
        raise RuntimeError(f"Create request failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload["requests"]


def get_embed_url(token: str, request_id: str, action_id: str) -> dict:
    payload = api_request(
        "POST",
        f"/api/v1/requests/{request_id}/actions/{action_id}/embedtoken",
        token,
        form_data={},
    )
    if payload.get("status") != "success":
        raise RuntimeError(f"Embed token fetch failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Zoho Sign embedded signing request and print the sign URL.")
    parser.add_argument("--token", default=os.getenv("ZOHO_SIGN_TOKEN"), help="Zoho Sign OAuth token.")
    parser.add_argument("--template-id", required=True, help="Zoho Sign template ID.")
    parser.add_argument("--recipient-name", required=True, help="Recipient display name.")
    parser.add_argument("--recipient-email", required=True, help="Recipient email address.")
    parser.add_argument("--request-name", required=True, help="Document/request name.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.token:
        print("Missing OAuth token. Pass --token or set ZOHO_SIGN_TOKEN.", file=sys.stderr)
        return 2

    created = create_request(
        token=args.token,
        template_id=args.template_id,
        recipient_name=args.recipient_name,
        recipient_email=args.recipient_email,
        request_name=args.request_name,
    )
    action_id = created["actions"][0]["action_id"]
    request_id = created["request_id"]
    embed_payload = get_embed_url(args.token, request_id, action_id)

    print(json.dumps(
        {
            "request_id": request_id,
            "action_id": action_id,
            "request_name": created["request_name"],
            "request_status": created["request_status"],
            "embed_response": embed_payload,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
