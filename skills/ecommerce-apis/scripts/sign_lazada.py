#!/usr/bin/env python3
"""Lazada Open Platform request signing utility.

Generates HMAC-SHA256 signed URLs for Lazada API requests.

Usage:
    python3 sign_lazada.py --app_key "key" --app_secret "secret" --path "/products/get" \
        [--access_token "token"] [--params '{"filter":"live","limit":"50"}']
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.parse


def sign_lazada(app_secret: str, path: str, params: dict) -> str:
    """Generate Lazada HMAC-SHA256 signature.

    Sign string = path + sorted(key+value pairs concatenated).
    """
    sorted_params = sorted(params.items())
    base_string = path + "".join(f"{k}{v}" for k, v in sorted_params)
    return hmac.new(
        app_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def build_url(app_key: str, app_secret: str, path: str,
              access_token: str = "", params: dict | None = None,
              region: str = "vn") -> str:
    """Build fully signed Lazada API URL."""
    base_urls = {
        "vn": "https://api.lazada.vn/rest",
        "th": "https://api.lazada.co.th/rest",
        "sg": "https://api.lazada.sg/rest",
        "my": "https://api.lazada.com.my/rest",
        "ph": "https://api.lazada.com.ph/rest",
        "id": "https://api.lazada.co.id/rest",
    }
    base_url = base_urls.get(region, base_urls["vn"])
    timestamp = str(int(time.time() * 1000))

    all_params = {
        "app_key": app_key,
        "timestamp": timestamp,
        "sign_method": "sha256",
    }
    if access_token:
        all_params["access_token"] = access_token
    if params:
        all_params.update(params)

    sign = sign_lazada(app_secret, path, all_params)
    all_params["sign"] = sign

    return f"{base_url}{path}?{urllib.parse.urlencode(all_params)}"


def main():
    parser = argparse.ArgumentParser(description="Lazada API request signing")
    parser.add_argument("--app_key", type=str, required=True)
    parser.add_argument("--app_secret", type=str, required=True)
    parser.add_argument("--path", type=str, required=True, help="API path, e.g. /products/get")
    parser.add_argument("--access_token", type=str, default="")
    parser.add_argument("--params", type=str, default="{}", help="JSON string of API params")
    parser.add_argument("--region", type=str, default="vn", help="Region: vn, th, sg, my, ph, id")
    args = parser.parse_args()

    try:
        params = json.loads(args.params) if args.params != "{}" else None
    except json.JSONDecodeError:
        print(f"Error: invalid JSON in --params: {args.params}", file=sys.stderr)
        sys.exit(1)

    url = build_url(
        app_key=args.app_key,
        app_secret=args.app_secret,
        path=args.path,
        access_token=args.access_token,
        params=params,
        region=args.region,
    )

    print(url)


if __name__ == "__main__":
    main()
