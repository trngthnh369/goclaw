"""Helper script to interact with GoClaw HTTP API from Windows PowerShell."""
import sys
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError

BASE = "http://localhost:18790"
TOKEN = "a7c9f3e1b2d4068f5a1c7e3b9d2f4a6c"
USER = "trngthnh369"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-GoClaw-User-Id": USER,
    "Content-Type": "application/json"
}

def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        err = e.read().decode()
        print(f"ERROR {e.code}: {err}", file=sys.stderr)
        return None

def list_providers():
    r = api("GET", "/v1/providers")
    if r and "providers" in r:
        for p in r["providers"]:
            print(f"{p['id']} | {p.get('type','')} | {p.get('name','')}")

def list_agents():
    r = api("GET", "/v1/agents")
    if r and "agents" in r:
        for a in r["agents"]:
            print(f"{a['id']} | {a.get('display_name','')} | {a.get('model','')}")

def create_agent():
    body = {
        "agent_key": "polymarket-intel",
        "name": "Polymarket Intel",
        "display_name": "Polymarket Intel",
        "type": "predefined",
        "description": "Monitors Polymarket prediction markets for unusual trading activity (volume spikes, large bets, price swings) that may signal insider knowledge.",
        "model": "gpt-4o",
        "provider_id": "019d3488-57c1-71a5-b249-eef5d9edada0",
        "tool_policy": "minimal",
        "tool_also_allow": ["exec"],
        "skill_allow_list": ["polymarket-scanner"],
        "self_evolve": False,
        "skill_evolve": False,
        "memory_config": {"enabled": False}
    }
    r = api("POST", "/v1/agents", body)
    if r:
        print(json.dumps(r, indent=2))
    return r

def create_cron(agent_id, discord_channel_id):
    # Hourly scan
    body = {
        "name": "polymarket-hourly-scan",
        "agent_id": agent_id,
        "schedule": "every 1 hour",
        "message": (
            "Run the polymarket-scanner skill now. "
            "Execute: python3 /app/skills/polymarket-scanner/scripts/scanner.py --mode=full\n\n"
            "Read the JSON output carefully. If anomalies are found (total_anomalies > 0), "
            "compose a Discord alert following the format in SKILL.md. "
            "If NO anomalies, respond only: No anomalies detected. Next scan in 1 hour."
        ),
        "deliver": "channel",
        "channel": "discord",
        "chat_id": discord_channel_id,
        "enabled": True,
        "isolated_session": True,
        "light_context": True
    }
    r = api("POST", "/v1/cron", body)
    if r:
        print(f"Hourly cron created: {json.dumps(r, indent=2)}")

    # Daily discovery
    body2 = {
        "name": "polymarket-daily-discovery",
        "agent_id": agent_id,
        "schedule": "at 06:00",
        "message": (
            "Refresh the Polymarket watchlist. "
            "Execute: python3 /app/skills/polymarket-scanner/scripts/scanner.py --mode=discover\n\n"
            "Report how many markets were found per category."
        ),
        "enabled": True,
        "isolated_session": True,
        "light_context": True
    }
    r2 = api("POST", "/v1/cron", body2)
    if r2:
        print(f"Daily discovery cron created: {json.dumps(r2, indent=2)}")

def test_chat(agent_id, message):
    body = {"message": message}
    r = api("POST", f"/v1/agents/{agent_id}/chat", body)
    if r:
        print(json.dumps(r, indent=2))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    
    if cmd == "providers":
        list_providers()
    elif cmd == "agents":
        list_agents()
    elif cmd == "create-agent":
        create_agent()
    elif cmd == "create-cron":
        if len(sys.argv) < 4:
            print("Usage: api_helper.py create-cron <agent_id> <discord_channel_id>")
            sys.exit(1)
        create_cron(sys.argv[2], sys.argv[3])
    elif cmd == "test":
        if len(sys.argv) < 4:
            print("Usage: api_helper.py test <agent_id> <message>")
            sys.exit(1)
        test_chat(sys.argv[2], sys.argv[3])
    else:
        print("Commands: providers, agents, create-agent, create-cron, test")
