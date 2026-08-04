# Ziggs — Deploy Guide

## Arquitetura

```
                    ┌─────────────────────────┐
                    │   Cloudflare DNS (Free)  │
                    │   @ → SERVER_IP           │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   VPS Principal (Hetzner) │
                    │   Caddy (443/80) + TLS    │
                    │   ├─ Backend (8000)       │
                    │   ├─ Frontend (dist/)     │
                    │   ├─ Bot-v2               │
                    │   └─ Postgres 16          │
                    └───────────────────────────┘

                    ┌─────────────────────────┐
                    │   VPS Túnel (Hetzner)    │
                    │   WireGuard (51820/udp)  │
                    │   Split-tunnel Albion    │
                    └───────────────────────────┘
```

## VPS necessárias

| # | Função | Inicial | Escalar p/ | Custo inicial |
|---|--------|---------|------------|---------------|
| 1 | Servidor principal | CPX21 (3 vCPU, 4GB, 80GB) | AX41-NVMe (64GB, 512GB) | ~$8/mês |
| 2 | Túnel WireGuard companion | CX22 (2 vCPU, 4GB, 40GB) | — | ~$5/mês |

**Total inicial: ~$13/mês.** Quando a DB passar de ~50GB, migra o servidor
principal pra AX41-NVMe (~$35) — `pg_dump` + `pg_restore` + troca IP no
Cloudflare, 5 min de downtime.

## Antes de rodar

### 1. DNS (Cloudflare)
- Crie conta em cloudflare.com (grátis)
- Adicione seu domínio
- Aponte o DNS nameservers da Cloudflare no registrador
- Crie A records:
  - `@` → IP do servidor principal (Proxied)
  - `www` → IP do servidor principal (Proxied)

### 2. Discord Developer Portal
- **OAuth2:** Client ID + Client Secret (já tem)
- **Redirect URI:** adicione `https://SEU_DOMINIO/auth/discord/callback`
- **Bot Token:** já tem

### 3. Hetzner
- Cloud: console.hetzner.com (VPS, verificação instantânea por cartão)
- Dedicado: robot.hetzner.com (verificação por carta postal, ~1 semana)
- Região: Falkenstein (EU) ou Hillsboro (EUA)

## Deploy — Servidor Principal

```bash
# 1. Apsonte DNS pra VPS (Cloudflare)
# 2. SSH na VPS
ssh root@SEU_IP

# 3. Baixe e rode o script
curl -sSL https://raw.githubusercontent.com/SUA-ORG/ziggs/main/deploy/server-setup.sh -o deploy.sh
bash deploy.sh ziggs.xyz

# Ou passe as secrets via env (sem prompt):
DISCORD_CLIENT_ID=xxx \
DISCORD_CLIENT_SECRET=xxx \
DISCORD_BOT_TOKEN=xxx \
bash deploy.sh ziggs.xyz
```

O script:
- Instala Postgres 16, Python 3.12, Node 20, Caddy
- Cria banco + usuário
- Clona o repo, instala deps, roda migrations
- Builda o frontend
- Cria serviços systemd (backend + bot)
- Configura Caddy com TLS automático (Let's Encrypt)
- Firewall (ufw), backup diário, auto-update de segurança

**Resultado:** `https://SEU_DOMINIO` funcionando em ~5min.

### Se o repo for privado
O script tenta `git clone`. Se falhar (repo privado sem deploy key):
```bash
# Na sua máquina local:
scp -r C:\Users\Gabriel\Documents\Code\ziggs root@SEU_IP:/home/ziggs/ziggs
# Depois na VPS:
chown -R ziggs:ziggs /home/ziggs/ziggs
# E rode o script de novo — ele pula o clone se já existir
```

## Deploy — VPS Túnel (companion)

A VPS de túnel roda só WireGuard — split-tunneling dos IPs do Albion.

```bash
ssh root@VPS_TUNEL_IP

# Baixe o script de túnel (já existe no repo)
curl -sSL https://raw.githubusercontent.com/SUA-ORG/ziggs/main/companion/docs/companion-vps-setup.sh -o tunnel.sh

# Rode passando a PUBLIC KEY do companion (gerada na aba Túnel do companion)
bash tunnel.sh "CLIENT_PUBLIC_KEY_BASE64"
```

O script devolve:
- Endpoint (IP:porta)
- Server pubkey

Cole esses valores na aba **Rota / Túnel** do companion.

## Pós-deploy

### Verificar serviços
```bash
systemctl status ziggs-backend
systemctl status ziggs-bot
systemctl status caddy
systemctl status postgresql
```

### Logs
```bash
journalctl -u ziggs-backend -f    # backend
journalctl -u ziggs-bot -f        # bot
journalctl -u caddy -f            # reverse proxy
```

### Atualizar código (deploy novo)
```bash
ssh root@SEU_IP
cd /home/ziggs/ziggs

# Pull
sudo -u ziggs git pull

# Backend
cd backend
sudo -u ziggs venv/bin/alembic upgrade head
systemctl restart ziggs-backend

# Frontend (se mudou)
cd ../frontend
sudo -u ziggs npm ci && sudo -u ziggs npm run build
# (não precisa restartar — Caddy serve o dist/ direto)

# Bot (se mudou)
cd ../bot-v2
sudo -u ziggs venv/bin/pip install -r requirements.txt
systemctl restart ziggs-bot
```

### Backup
- Automático: diário às 4h, 7 dias retenção em `/home/ziggs/backups/`
- Manual: `/usr/local/bin/ziggs-backup.sh`
- Restore: `pg_restore -U ziggs -d ziggs -c ziggs_YYYYMMDD_HHMMSS.dump`

### Migrar para servidor maior (quando DB crescer)
```bash
# Na VPS antiga:
pg_dump -U ziggs -d ziggs -F c -f ziggs_full.dump

# Transfere pra nova VPS:
scp ziggs_full.dump root@NOVA_VPS:/tmp/

# Na nova VPS (depois de rodar o deploy script):
pg_restore -U ziggs -d ziggs -c /tmp/ziggs_full.dump

# Atualiza DNS no Cloudflare pro novo IP
```

## Portas

| Porta | Protocol | Serviço | Origem |
|-------|----------|---------|--------|
| 22 | TCP | SSH | só seu IP (recomendado: `ufw allow from SEU_IP to any port 22`) |
| 80 | TCP | HTTP → redireciona pra 443 | todos |
| 443 | TCP | HTTPS (Caddy) | todos |
| 5432 | TCP | Postgres | só localhost (não expor) |
| 8000 | TCP | Backend (uvicorn) | só localhost (Caddy proxy) |
| 51820 | UDP | WireGuard | companion (só na VPS de túnel) |