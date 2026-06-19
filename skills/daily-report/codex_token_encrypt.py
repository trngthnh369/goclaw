import json, os, base64, sys
sys.path.insert(0, "/app/workspace/_daily-report/pylib")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

enc_key_hex = os.environ["GOCLAW_ENCRYPTION_KEY"]
key = bytes.fromhex(enc_key_hex)  # 64 hex -> 32 bytes
auth = json.load(open("/app/.codex-host/auth.json", encoding="utf-8"))
tok = auth.get("tokens", {})
access = tok.get("access_token", "")
refresh = tok.get("refresh_token", "")
account = tok.get("account_id", "")

def enc(plain):
    if not plain: return ""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plain.encode(), None)  # ct+tag
    return "aes-gcm:" + base64.b64encode(nonce + ct).decode()

print("ENC_ACCESS=" + enc(access))
print("ENC_REFRESH=" + enc(refresh))
print("ACCOUNT_ID=" + account)
print("ACCESS_LEN=%d REFRESH_LEN=%d" % (len(access), len(refresh)), file=sys.stderr)
