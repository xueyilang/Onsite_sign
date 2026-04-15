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
        "system_sn": "SN-TEST-0004",
        "system_bat_modell": "BAT-TEST-01",
        "system_bat_anzahl": "2",
        "vorort_problem": "Testeinsatz vor Ort, Fehlerbild wurde simuliert.",
        "vorort_arbeiten": "Sichtpruefung, Diagnose, Neustart und Funktionskontrolle durchgefuehrt.",
        "service_anmerkungen": "Automatisch per API erzeugter Testvorgang.",
        "austasuch_sn_alt": "OLD-SN-0004",
        "austasuch_sn_neue": "NEW-SN-0004",
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
                # Template-level date fields expect a string; use a German-style business date.
                date_data[label] = "15 April 2026"
            elif category == "checkbox":
                boolean_data[label] = False

    # Make the sample payload less empty.
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
            }
        )
    return actions


def create_document(
    token: str,
    template_id: str,
    recipient_name: str,
    recipient_email: str,
    request_name: str,
) -> dict:
    template = get_template_detail(token, template_id)
    data = {
        "templates": {
            "request_name": request_name,
            "actions": build_actions(template, recipient_name, recipient_email),
            "field_data": build_field_data(template),
        }
    }
    form_data = {
        "data": json.dumps(data, ensure_ascii=False),
        "is_quicksend": "true",
    }
    return api_request("POST", f"/api/v1/templates/{template_id}/createdocument", token, form_data=form_data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Zoho Sign document from a template.")
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

    response = create_document(
        token=args.token,
        template_id=args.template_id,
        recipient_name=args.recipient_name,
        recipient_email=args.recipient_email,
        request_name=args.request_name,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
