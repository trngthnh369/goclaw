#!/usr/bin/env python3
"""Shopee Open Platform request signing utility.

Generates HMAC-SHA256 signed URLs for Shopee API requests.

Usage:
    python3 sign_shopee.py --partner_id 123 --partner_key "key" --path "/api/v2/product/get_item_list" \
        [--shop_id 789] [--access_token "token"] [--params '{"offset":0}']
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.parse


def sign_shopee(partner_id: int, partner_key: str, path: str,
                timestamp: int, access_token: str = "", shop_id: int = 0) -> str:
    """Generate Shopee HMAC-SHA256 signature."""
    base_string = f"{partner_id}{path}{timestamp}"
    if access_token:
        base_string += access_token
    if shop_id:
        base_string += str(shop_id)
    return hmac.new(
        partner_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_url(partner_id: int, partner_key: str, path: str,
              shop_id: int = 0, access_token: str = "",
              params: dict | None = None) -> str:
    """Build fully signed Shopee API URL."""
    base_url = "https://partner.shopeemobile.com"
    timestamp = int(time.time())
    sign = sign_shopee(partner_id, partner_key, path, timestamp, access_token, shop_id)

    query = {
        "partner_id": str(partner_id),
        "timestamp": str(timestamp),
        "sign": sign,
    }
    if access_token:
        query["access_token"] = access_token
    if shop_id:
        query["shop_id"] = str(shop_id)
    if params:
        query.update({k: str(v) for k, v in params.items()})

    return f"{base_url}{path}?{urllib.parse.urlencode(query)}"


def main():
    parser = argparse.ArgumentParser(description="Shopee API request signing")
    parser.add_argument("--partner_id", type=int, required=True)
    parser.add_argument("--partner_key", type=str, required=True)
    parser.add_argument("--path", type=str, required=True, help="API path, e.g. /api/v2/product/get_item_list")
    parser.add_argument("--shop_id", type=int, default=0)
    parser.add_argument("--access_token", type=str, default="")
    parser.add_argument("--params", type=str, default="{}", help="JSON string of query params")
    parser.add_argument("--body", type=str, default="", help="POST body (printed separately)")
    args = parser.parse_args()

    try:
        params = json.loads(args.params) if args.params != "{}" else None
    except json.JSONDecodeError:
        print(f"Error: invalid JSON in --params: {args.params}", file=sys.stderr)
        sys.exit(1)

    url = build_url(
        partner_id=args.partner_id,
        partner_key=args.partner_key,
        path=args.path,
        shop_id=args.shop_id,
        access_token=args.access_token,
        params=params,
    )

    print(url)
    if args.body:
        print(f"\nPOST body:\n{args.body}")


if __name__ == "__main__":
    main()
