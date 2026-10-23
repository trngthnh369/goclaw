#!/usr/bin/env python3
"""TikTok Shop Open API request signing utility.

Generates HMAC-SHA256 signed URLs for TikTok Shop API requests.

Usage:
    python3 sign_tiktok.py --app_key "key" --app_secret "secret" \
        --path "/product/202309/products/search" \
        [--access_token "token"] [--shop_cipher "cipher"] \
        [--params '{"page_size":"20"}']
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.parse


def sign_tiktok(app_secret: str, path: str, params: dict) -> str:
    """Generate TikTok Shop HMAC-SHA256 signature.

    Sign string = app_secret + path + sorted(key+value) + app_secret (wrapping).
    Excludes: sign, access_token, app_key, timestamp from signing.
    """
    exclude_keys = {"sign", "access_token", "app_key", "timestamp"}
    sign_params = {k: v for k, v in sorted(params.items()) if k not in exclude_keys}
    base_string = app_secret + path + "".join(f"{k}{v}" for k, v in sign_params.items()) + app_secret
    return hmac.new(
        app_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_url(app_key: str, app_secret: str, path: str,
              access_token: str = "", shop_cipher: str = "",
              params: dict | None = None) -> str:
    """Build fully signed TikTok Shop API URL."""
    base_url = "https://open-api.tiktokglobalshop.com"
    timestamp = str(int(time.time()))

    all_params = {
        "app_key": app_key,
        "timestamp": timestamp,
    }
    if access_token:
        all_params["access_token"] = access_token
    if shop_cipher:
        all_params["shop_cipher"] = shop_cipher
    if params:
        all_params.update(params)

    sign = sign_tiktok(app_secret, path, all_params)
    all_params["sign"] = sign

    return f"{base_url}{path}?{urllib.parse.urlencode(all_params)}"


def main():
    parser = argparse.ArgumentParser(description="TikTok Shop API request signing")
    parser.add_argument("--app_key", type=str, required=True)
    parser.add_argument("--app_secret", type=str, required=True)
    parser.add_argument("--path", type=str, required=True, help="API path, e.g. /product/202309/products/search")
    parser.add_argument("--access_token", type=str, default="")
    parser.add_argument("--shop_cipher", type=str, default="")
    parser.add_argument("--params", type=str, default="{}", help="JSON string of query params")
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
        shop_cipher=args.shop_cipher,
        params=params,
    )

    print(url)


if __name__ == "__main__":
    main()
