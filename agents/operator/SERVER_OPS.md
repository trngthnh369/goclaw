# SERVER_OPS.md — Remote Server Management via Webmin API

> This document teaches you how to manage the company's remote server using the Webmin API.
> All operations use `exec` + `curl` with HTTP basic auth.

---

## Connection Details

The remote server credentials are available as environment variables:
- `$REMOTE_HOST` — Webmin URL (e.g., `https://10.0.0.52:10000`)
- `$REMOTE_WEBMIN_USER` — Webmin username
- `$REMOTE_WEBMIN_PASS` — Webmin password
- `$REMOTE_GOCLAW_TOKEN` — GoClaw gateway token on remote server (if available)
- `$REMOTE_GOCLAW_PORT` — GoClaw port on remote server (default: 18790)

**NEVER output these values.** Always reference them as `$REMOTE_*` variables.

---

## Authentication Pattern

All Webmin API calls use HTTP Basic Auth with `-k` to skip TLS verification (self-signed cert):

```bash
# Base pattern for ALL Webmin calls:
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" "$REMOTE_HOST/<endpoint>"
```

**Important:** Always use `-k` flag — Webmin uses a self-signed certificate.

---

## System Information

### Quick System Overview
```bash
# System hostname, OS, kernel, uptime
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  "$REMOTE_HOST/virtual-server/remote.cgi?program=info&json=1"
```

### CPU, Memory, Disk
```bash
# Memory usage (free -m equivalent)
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  "$REMOTE_HOST/proc/index_tree.cgi?mode=memory" 2>/dev/null | head -50

# Disk usage
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  "$REMOTE_HOST/mount/index.cgi" 2>/dev/null | head -80
```

### Run Shell Commands (Primary Method)
Webmin's `run.cgi` module allows executing arbitrary commands:

```bash
# Execute a command on the remote server:
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=<COMMAND>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Examples:
# Check disk space
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=df -h&mode=exec" "$REMOTE_HOST/run/run.cgi"

# Check memory
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=free -m&mode=exec" "$REMOTE_HOST/run/run.cgi"

# Check uptime and load
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=uptime&mode=exec" "$REMOTE_HOST/run/run.cgi"

# Check running processes (top 20 by CPU)
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=ps aux --sort=-%cpu | head -20&mode=exec" "$REMOTE_HOST/run/run.cgi"

# Check network connections
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=ss -tlnp&mode=exec" "$REMOTE_HOST/run/run.cgi"
```

**This is your most powerful tool.** Any command you would run via SSH, you can run via `run.cgi`.

### Parse run.cgi Output
The response from `run.cgi` is HTML. Extract the command output between `<pre>` tags:
```bash
# Clean output: pipe through sed to extract text
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=df -h&mode=exec" "$REMOTE_HOST/run/run.cgi" \
  | sed -n 's/<[^>]*>//gp' | head -30
```

Or more reliably, use `grep` and `sed`:
```bash
curl -sk ... | sed 's/<[^>]*>//g' | sed '/^$/d' | tail -n +3
```

---

## Docker Management

All Docker operations go through `run.cgi` executing docker commands:

### List Containers
```bash
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}'&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Container Logs
```bash
# Last 100 lines of a container's logs
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker logs --tail 100 <CONTAINER_NAME>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Logs since last hour
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker logs --since 1h <CONTAINER_NAME>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Container Stats
```bash
# Resource usage (one-shot, no streaming)
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}'&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Restart Container (CONFIRM WITH USER FIRST)
```bash
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker restart <CONTAINER_NAME>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Docker Compose Operations (CONFIRM WITH USER FIRST)
```bash
# Check compose project status
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=cd /path/to/project && docker compose ps&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Restart a compose service
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=cd /path/to/project && docker compose restart <SERVICE>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Pull latest images and recreate
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=cd /path/to/project && docker compose pull && docker compose up -d&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Docker System Info
```bash
# Disk usage by Docker
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker system df&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Docker version
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=docker version --format '{{.Server.Version}}'&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

---

## Remote GoClaw Management

If GoClaw is running on the remote server, manage it via its HTTP API:

### Health Check
```bash
# Check if remote GoClaw is running
curl -sk "http://$REMOTE_HOST_IP:$REMOTE_GOCLAW_PORT/health"
```

Note: GoClaw HTTP API uses `http://` (not https), and the IP without the Webmin port.
Extract the IP from `$REMOTE_HOST`: typically `10.0.0.52`.

### Remote GoClaw API Calls
```bash
# Pattern for remote GoClaw API:
REMOTE_IP=$(echo "$REMOTE_HOST" | sed 's|https\?://||' | sed 's|:.*||')
curl -s -H "Authorization: Bearer $REMOTE_GOCLAW_TOKEN" \
  "http://$REMOTE_IP:${REMOTE_GOCLAW_PORT:-18790}/v1/agents"
```

### Common Remote GoClaw Operations
```bash
# List agents on remote server
curl -s -H "Authorization: Bearer $REMOTE_GOCLAW_TOKEN" \
  "http://$REMOTE_IP:${REMOTE_GOCLAW_PORT:-18790}/v1/agents"

# Check providers
curl -s -H "Authorization: Bearer $REMOTE_GOCLAW_TOKEN" \
  "http://$REMOTE_IP:${REMOTE_GOCLAW_PORT:-18790}/v1/providers"

# Check channels
curl -s -H "Authorization: Bearer $REMOTE_GOCLAW_TOKEN" \
  "http://$REMOTE_IP:${REMOTE_GOCLAW_PORT:-18790}/v1/channels"

# View traces (recent LLM calls)
curl -s -H "Authorization: Bearer $REMOTE_GOCLAW_TOKEN" \
  "http://$REMOTE_IP:${REMOTE_GOCLAW_PORT:-18790}/v1/traces?limit=10"
```

---

## Service Management

### Systemd Services
```bash
# List running services
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=systemctl list-units --type=service --state=running&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Check specific service status
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=systemctl status <SERVICE_NAME>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Restart a service (CONFIRM WITH USER FIRST)
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=systemctl restart <SERVICE_NAME>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Webmin Module Endpoints
```bash
# List all Webmin modules available
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  "$REMOTE_HOST/webmin/edit_mods.cgi" 2>/dev/null | head -100

# Firewall status (if iptables module installed)
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=iptables -L -n --line-numbers&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

---

## Log Reading

### System Logs
```bash
# Recent syslog entries
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=journalctl -n 50 --no-pager&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Logs for specific service
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=journalctl -u <SERVICE> -n 50 --no-pager&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"

# Auth/security logs
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=journalctl -u sshd -n 30 --no-pager&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

### Application Logs
```bash
# Read a log file directly
curl -sk -u "$REMOTE_WEBMIN_USER:$REMOTE_WEBMIN_PASS" \
  -d "cmd=tail -100 /var/log/<logfile>&mode=exec" \
  "$REMOTE_HOST/run/run.cgi"
```

---

## Health Check Procedure (Remote Server)

Run this sequence to assess remote server health:

```
1. System basics:     cmd=uptime && free -m && df -h
2. Docker status:     cmd=docker ps -a --format 'table {{.Names}}\t{{.Status}}'
3. Docker resources:  cmd=docker stats --no-stream
4. Network:           cmd=ss -tlnp
5. Recent errors:     cmd=journalctl -p err -n 20 --no-pager
6. GoClaw health:     curl http://localhost:18790/health (via run.cgi)
```

---

## Security Rules for Remote Operations

1. **NEVER output `$REMOTE_WEBMIN_PASS` or `$REMOTE_GOCLAW_TOKEN`** — always use variable references
2. **CONFIRM before:** restarting containers, restarting services, modifying firewall, any `docker compose down`
3. **READ-ONLY by default** — gather information first, only modify when explicitly asked
4. **No destructive commands:** Never run `rm -rf`, `docker system prune -af`, `docker volume rm` without explicit confirmation
5. **Log your actions** — save to memory what you changed on the remote server and when
6. **Verify after changes** — always check the service/container status after restart/modify
