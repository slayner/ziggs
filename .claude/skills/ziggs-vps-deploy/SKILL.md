---
name: ziggs-vps-deploy
description: Deploy to Ziggs VPS fleet and inspect production.
version: 0.1.0
author: Hermes
metadata:
  hermes:
    tags: [Ziggs, VPS, Deploy, SSH, Hetzner, WireGuard]
---

# Ziggs VPS Infrastructure and Deploy

Map and deploy to the Ziggs VPS fleet: one production server (backend+bot+frontend+Postgres+Caddy) and three WireGuard tunnel servers. All VPS share a single SSH key. Backend deploys via SCP+install scripts — no git on the VPS, no full repo copy.

## When to Use

- User says "faz o deploy" or "publica na VPS"
- Need to understand which VPS exist, what they run, or how to SSH into them
- Backend or bot code changed and needs publishing to production
- Need to check production health, logs, or service status

## Prerequisites

- SSH key: `~/.ssh/hetzner_ziggs` (same key for ALL VPS — production + tunnels)
- Deploy scripts: `deploy/publish-backend.ps1`, `deploy/publish-bot.ps1` (PowerShell, run from repo root on Windows)
- `deploy/PRODUCAO.md` — the SOP; read before any production action

## Quick Reference

```
VPS Principal:   root@167.233.241.191   (main)              hetzner_ziggs
Túnel Singapura: root@45.32.110.2      (tunnel-singapura)   hetzner_ziggs
Túnel Amsterdã:  root@95.179.145.72    (tunnel-amsterdan)   hetzner_ziggs
Túnel Nova York: root@173.199.116.252 (tunnel-new-york)    hetzner_ziggs

Checkout VPS:  /home/ziggs/ziggs (NOT a git repo — SCP-only)
Services:      ziggs-backend, ziggs-bot, caddy, postgresql
SSH:          ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191
Health:       curl -fsS http://127.0.0.1:8000/health
```

## Procedure

### Deploy backend (no migration)

1. Run local tests first: `cd backend && PYTHONPATH=. venv/Scripts/python -m pytest tests/test_render.py -q` (or relevant tests).
2. Invoke through the `terminal` tool:
   ```bash
   powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 app/api/routes/render.py
   ```
   Paths are relative to `backend/`. Multiple files space-separated. Script SCPs each file, runs `install -D -o ziggs -g ziggs -m 0644`, restarts `ziggs-backend`, polls `/health` up to 15 times.
3. Script does NOT return until `/health` responds `{"status":"ok"...}` or throws.

### Deploy backend (with migration)

```bash
powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 -Migrate app/models/example.py alembic/versions/example.py
```

`-Migrate` runs `venv/bin/alembic upgrade head` before restart. Pass model file + migration file.

### Deploy bot-v2

```bash
powershell -ExecutionPolicy Bypass -File deploy/publish-bot.ps1 cogs/some_cog.py
```

Paths relative to `bot-v2/`. Restarts `ziggs-bot` (no health check — bot has no HTTP endpoint).

### Deploy frontend

No publish script. SSH in and build on the VPS:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191
cd /home/ziggs/ziggs/frontend
sudo -u ziggs npm ci && sudo -u ziggs npm run build
# Caddy serves dist/ directly — no restart needed
```

### Check production status

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191 \
  "systemctl is-active ziggs-backend ziggs-bot caddy postgresql && \
   curl -fsS http://127.0.0.1:8000/health && echo"
```

### Check logs

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191 \
  "journalctl -u ziggs-backend -n 50 --no-pager"
```

## Pitfalls

- **VPS checkout is NOT a git repo.** Deploy is SCP-only via `publish-backend.ps1`/`publish-bot.ps1`. Never `git pull` on the VPS.
- **Same SSH key for all VPS.** `~/.ssh/vultr_americas` is a legacy key that no longer connects anywhere. Use `~/.ssh/hetzner_ziggs` for everything.
- **Restart order matters.** If both backend and bot need restart: `ziggs-backend` first, wait for `/health` ok, THEN `ziggs-bot`. Bot depends on backend API to start work.
- **No frontend restart.** Caddy serves `frontend/dist/` directly — `npm run build` is enough.
- **Never copy `.env`, `data/`, or venvs to the VPS.** Publish scripts only send specific source files.
- **Tunnel VPS are not production.** They only run WireGuard (`wg0`) for the companion split-tunnel. Do not deploy app code there.
- **`publish-backend.ps1` is PowerShell.** On the Windows host, invoke through the `terminal` tool with `powershell -ExecutionPolicy Bypass -File ...`.

## Verification

After any backend deploy, confirm the healthcheck:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191 \
  "curl -fsS http://127.0.0.1:8000/health"
```

Must return `{"status":"ok"...}`. If it fails, check logs:
```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/hetzner_ziggs root@167.233.241.191 \
  "journalctl -u ziggs-backend -n 50 --no-pager"
```