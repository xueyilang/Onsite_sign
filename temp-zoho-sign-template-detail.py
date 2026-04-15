#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://sign.zoho.com"
KNOWN_BASE_URLS = [
    "https://sign.zoho.com",
    "https://sign.zoho.eu",
    "https://sign.zoho.in",
    "https://sign.zoho.jp",
    "https://sign.zoho.com.au",
    "https://sign.zoho.zohocloud.ca",
    "https://sign.zoho.sa",
]


def make_request(
    method: str,
    path: str,
    token: str,
    form_data: dict | None = None,
    base_url: str | None = None,
) -> dict:
    base_url = (base_url or os.getenv("ZOHO_SIGN_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
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


def list_templates(token: str, base_url: str | None = None) -> dict:
    return make_request("GET", "/api/v1/templates", token, base_url=base_url)


def get_template_detail(token: str, template_id: str, base_url: str | None = None) -> dict:
    return make_request("GET", f"/api/v1/templates/{template_id}", token, base_url=base_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List Zoho Sign templates and fetch a template's detail."
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ZOHO_SIGN_TOKEN"),
        help="Zoho Sign OAuth access token. Defaults to ZOHO_SIGN_TOKEN.",
    )
    parser.add_argument(
        "--template-id",
        help="Template ID to fetch. If omitted, the script lists templates first.",
    )
    parser.add_argument(
        "--fetch-first-detail",
        action="store_true",
        help="After listing templates, fetch detail for the first returned template.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("ZOHO_SIGN_BASE_URL"),
        help="Zoho Sign base URL, for example https://sign.zoho.eu.",
    )
    parser.add_argument(
        "--auto-dc",
        action="store_true",
        help="Try known Zoho Sign data center URLs until one succeeds.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the full API response, including image preview payloads.",
    )
    return parser.parse_args()


def strip_large_preview_payloads(data: dict) -> dict:
    scrubbed = copy.deepcopy(data)

    def walk(value):
        if isinstance(value, dict):
            if "image_string" in value:
                value["image_string"] = "<omitted>"
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(scrubbed)
    return scrubbed


def emit(payload: dict, raw: bool) -> None:
    if not raw:
        payload = strip_large_preview_payloads(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_with_base_url(args: argparse.Namespace, base_url: str | None) -> int:
    if args.template_id:
        detail = get_template_detail(args.token, args.template_id, base_url=base_url)
        emit(detail, args.raw)
        return 0

    listing = list_templates(args.token, base_url=base_url)
    emit(listing, args.raw)

    if not args.fetch_first_detail:
        return 0

    templates = listing.get("templates", [])
    if not templates:
        print("No templates returned, skipping detail fetch.", file=sys.stderr)
        return 1

    first = templates[0]
    template_id = first.get("template_id")
    if not template_id:
        print("First template did not contain template_id, skipping detail fetch.", file=sys.stderr)
        return 1

    detail = get_template_detail(args.token, template_id, base_url=base_url)
    print("\n--- first template detail ---")
    emit(detail, args.raw)
    return 0


def main() -> int:
    args = parse_args()
    if not args.token:
        print("Missing OAuth token. Pass --token or set ZOHO_SIGN_TOKEN.", file=sys.stderr)
        return 2

    if args.auto_dc:
        for candidate in [url for url in KNOWN_BASE_URLS if url != args.base_url]:
            try:
                print(f"Trying {candidate} ...", file=sys.stderr)
                return run_with_base_url(args, candidate)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
        return 1

    return run_with_base_url(args, args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
