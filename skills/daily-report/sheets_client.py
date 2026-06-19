#!/usr/bin/env python3
# sheets_client.py — minimal Google Sheets v4 client for the container.
#
# Uses a service-account key + google-auth (installed into ./pylib) to get a token, then plain
# stdlib urllib for the REST calls. The SA key + pylib live in the workspace volume (persist
# across container restarts); the host gws CLI is NOT available inside the container.
#
# Sheet must be shared with the service account email.
import json
import os
import sys
import urllib.parse
import urllib.request

_PYLIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib")
if _PYLIB not in sys.path:
    sys.path.insert(0, _PYLIB)

from google.oauth2 import service_account  # noqa: E402
import google.auth.transport.requests as gauth_requests  # noqa: E402

WORK = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.environ.get("DAILY_REPORT_SA_KEY", os.path.join(WORK, "sa-key.json"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API = "https://sheets.googleapis.com/v4/spreadsheets"

_token_cache: dict = {}


def _token() -> str:
    if not _token_cache.get("tok"):
        creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
        creds.refresh(gauth_requests.Request())
        _token_cache["tok"] = creds.token
    return _token_cache["tok"]


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + _token(),
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _q(a1: str) -> str:
    # safe='' so '/' inside sheet names (e.g. "(01-07/06)") is encoded as %2F and does not
    # break the URL path.
    return urllib.parse.quote(a1, safe="")


def read_range(spreadsheet_id: str, a1: str) -> list:
    """Return rows (list of lists) for an A1 range. Sheet names with special chars must already
    be single-quoted by the caller, e.g. "'(01-07/06)'!A1:F50"."""
    url = f"{API}/{spreadsheet_id}/values/{_q(a1)}"
    return _req("GET", url).get("values", [])


def update_range(spreadsheet_id: str, a1: str, values: list) -> dict:
    url = f"{API}/{spreadsheet_id}/values/{_q(a1)}?valueInputOption=USER_ENTERED"
    return _req("PUT", url, {"range": a1, "values": values})


def append_rows(spreadsheet_id: str, a1: str, values: list) -> dict:
    url = (f"{API}/{spreadsheet_id}/values/{_q(a1)}:append"
           "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    return _req("POST", url, {"values": values})


def get_meta(spreadsheet_id: str) -> list:
    """Return tabs as [{'sheetId': int, 'title': str}]."""
    url = f"{API}/{spreadsheet_id}?fields=sheets.properties(sheetId,title)"
    data = _req("GET", url)
    return [s["properties"] for s in data.get("sheets", [])]


def batch_update(spreadsheet_id: str, requests: list) -> dict:
    url = f"{API}/{spreadsheet_id}:batchUpdate"
    return _req("POST", url, {"requests": requests})


def insert_column(spreadsheet_id: str, sheet_id: int, index: int) -> dict:
    """Insert one blank column at 0-based column `index` (shifts later columns right)."""
    return batch_update(spreadsheet_id, [{
        "insertDimension": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": index, "endIndex": index + 1},
            "inheritFromBefore": False,
        }
    }])


# CLI for quick testing: python3 sheets_client.py read <SID> "<A1>"
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "read":
        rows = read_range(sys.argv[2], sys.argv[3])
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print("usage: sheets_client.py read <spreadsheet_id> <A1range>", file=sys.stderr)
        sys.exit(1)
