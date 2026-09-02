#!/bin/bash
# ============================================================================
# Ziggs — Deploy completo (servidor principal)
# Rodar como root numa VPS Ubuntu 22.04/24.04 limpa.
#
# Instala: Postgres 16, Python 3.12, Node 20, Caddy (reverse proxy + TLS),
#          backend (uvicorn), bot-v2 (discord.py), tudo como systemd services.
#
# Uso:
#   scp deploy-server.sh root@SERVER:/root/
#   ssh root@SERVER "bash /root/deploy-server.sh <DOMAIN>"
#
# Onde <DOMAIN> é o domínio apontado pra esta VPS (ex: ziggs.xyz).
# Cloudflare DNS deve apontar @ e www pro IP da VPS (A record, proxied ou DNS only).
# ============================================================================

set -e

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Uso: $0 <DOMAIN>"
  echo "  ex: $0 ziggs.xyz"
  exit 1
fi

echo "==> Ziggs deploy — domínio: $DOMAIN"

# ── Variáveis que você PRECISA editar antes de rodar ──────────────────────
# Geradas automaticamente se não definidas (recomendado — copie do output no fim).
export DB_PASS="${DB_PASS:-$(openssl rand -hex 16)}"
export SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
export BOT_API_SECRET="${BOT_API_SECRET:-$(openssl rand -hex 32)}"
export COMPANION_API_SECRET="${COMPANION_API_SECRET:-$(openssl rand -hex 32)}"

# Estas PRECISAM ser preenchidas pelo usuário (não dá pra gerar):
# DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_BOT_TOKEN
# Se não estiverem no ambiente, o script pergunta.
if [ -z "$DISCORD_CLIENT_ID" ]; then
  read -rp "Discord Client ID: " DISCORD_CLIENT_ID
fi
if [ -z "$DISCORD_CLIENT_SECRET" ]; then
  read -rp "Discord Client Secret: " DISCORD_CLIENT_SECRET
fi
if [ -z "$DISCORD_BOT_TOKEN" ]; then
  read -rp "Discord Bot Token: " DISCORD_BOT_TOKEN
fi

# ── 1. Sistema base ───────────────────────────────────────────────────────
echo "==> Atualizando sistema"
apt-get update -qq
apt-get upgrade -y -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  curl git build-essential python3.12 python3.12-venv python3-pip \
  nginx ufw fail2ban unattended-upgrades \
  libpq-dev libffi-dev libssl-dev tesseract-ocr \
  ca-certificates gnupg lsb-release >/dev/null

# ── 2. Postgres 16 ────────────────────────────────────────────────────────
echo "==> Instalando Postgres 16"
if ! command -v psql &>/dev/null; then
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
    gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
  echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] \
    http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-16 >/dev/null
fi

echo "==> Criando banco e usuário"
sudo -u postgres psql -c "CREATE USER ziggs WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE ziggs OWNER ziggs;" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER ziggs WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ziggs TO ziggs;" 2>/dev/null || true

# ── 3. Node 20 (para build do frontend) ───────────────────────────────────
echo "==> Instalando Node 20"
if ! command -v node &>/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi

# ── 4. Caddy (reverse proxy + HTTPS automático via Let's Encrypt) ─────────
echo "==> Instalando Caddy"
if ! command -v caddy &>/dev/null; then
  curl -fsSL https://dl.cloudflare.com/cloudflare-main.gpg | \
    gpg --dearmor -o /usr/share/keyrings/caddy.gpg 2>/dev/null || true
  curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" | \
    gpg --dearmor -o /usr/share/keyrings/caddy-stable.gpg
  echo "deb [signed-by=/usr/share/keyrings/caddy-stable.gpg] \
    https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi

# ── 5. Usuário do sistema ─────────────────────────────────────────────────
echo "==> Criando usuário ziggs"
id -u ziggs &>/dev/null || useradd -m -s /bin/bash -d /home/ziggs ziggs

# ── 6. Clonar/Atualizar o código ──────────────────────────────────────────
ZIGGS_HOME=/home/ziggs/ziggs
echo "==> Clonando código em $ZIGGS_HOME"
if [ -d "$ZIGGS_HOME/.git" ]; then
  sudo -u ziggs git -C "$ZIGGS_HOME" pull --ff-only 2>/dev/null || true
else
  # EDITAR: trocar pela URL do repo (privado precisa de deploy key)
  sudo -u ziggs git clone --depth 1 https://github.com/SUA-ORG/ziggs.git "$ZIGGS_HOME" 2>/dev/null || {
    echo "ERRO: não consegui clonar. Se o repo for privado, configure deploy key."
    echo "  Alternativa: scp o código da sua máquina:"
    echo "  scp -r C:\\Users\\Gabriel\\Documents\\Code\\ziggs root@SERVER:/home/ziggs/ziggs"
    exit 1
  }
fi

# ── 7. Backend: venv + deps + migrations ──────────────────────────────────
echo "==> Configurando backend"
cd "$ZIGGS_HOME/backend"
sudo -u ziggs python3.12 -m venv venv
sudo -u ziggs venv/bin/pip install --upgrade pip -q
sudo -u ziggs venv/bin/pip install -r requirements.txt -q

# .env de produção
cat > "$ZIGGS_HOME/backend/.env" <<EOF
DATABASE_URL=postgresql+psycopg://ziggs:$DB_PASS@localhost:5432/ziggs

DISCORD_CLIENT_ID=$DISCORD_CLIENT_ID
DISCORD_CLIENT_SECRET=$DISCORD_CLIENT_SECRET
DISCORD_REDIRECT_URI=https://$DOMAIN/auth/discord/callback
DISCORD_SCOPES=identify guilds email
DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN

FRONTEND_URL=https://$DOMAIN
SECRET_KEY=$SECRET_KEY
BOT_API_SECRET=$BOT_API_SECRET
COMPANION_API_SECRET=$COMPANION_API_SECRET

ENVIRONMENT=production
DISABLE_BACKGROUND_FETCHERS=false
EOF
chown ziggs:ziggs "$ZIGGS_HOME/backend/.env"
chmod 600 "$ZIGGS_HOME/backend/.env"

# Migrations
echo "==> Rodando migrations"
sudo -u ziggs venv/bin/alembic upgrade head

# ── 8. Frontend: build ────────────────────────────────────────────────────
echo "==> Buildando frontend"
cd "$ZIGGS_HOME/frontend"
sudo -u ziggs npm ci --silent 2>/dev/null || sudo -u ziggs npm install --silent
sudo -u ziggs npm run build --silent

# ── 9. Bot-v2: venv + deps ────────────────────────────────────────────────
echo "==> Configurando bot-v2"
cd "$ZIGGS_HOME/bot-v2"
sudo -u ziggs python3.12 -m venv venv
sudo -u ziggs venv/bin/pip install --upgrade pip -q
sudo -u ziggs venv/bin/pip install -r requirements.txt -q

cat > "$ZIGGS_HOME/bot-v2/.env" <<EOF
DISCORD_TOKEN=$DISCORD_BOT_TOKEN
BOT_SITE_URL=http://localhost:8000
BOT_PUBLIC_URL=https://$DOMAIN
BOT_API_SECRET=$BOT_API_SECRET
EOF
chown ziggs:ziggs "$ZIGGS_HOME/bot-v2/.env"
chmod 600 "$ZIGGS_HOME/bot-v2/.env"

# ── 10. systemd services ──────────────────────────────────────────────────
echo "==> Criando serviços systemd"

# Backend (uvicorn)
cat > /etc/systemd/system/ziggs-backend.service <<EOF
[Unit]
Description=Ziggs Backend (FastAPI)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=ziggs
Group=ziggs
WorkingDirectory=$ZIGGS_HOME/backend
EnvironmentFile=$ZIGGS_HOME/backend/.env
ExecStart=$ZIGGS_HOME/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Bot-v2 (discord.py)
cat > /etc/systemd/system/ziggs-bot.service <<EOF
[Unit]
Description=Ziggs Bot-v2 (Discord)
After=network.target ziggs-backend.service

[Service]
Type=simple
User=ziggs
Group=ziggs
WorkingDirectory=$ZIGGS_HOME/bot-v2
EnvironmentFile=$ZIGGS_HOME/bot-v2/.env
ExecStart=$ZIGGS_HOME/bot-v2/venv/bin/python -m bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ziggs-backend ziggs-bot
systemctl restart ziggs-backend
sleep 2
systemctl restart ziggs-bot

# ── 11. Caddy (reverse proxy + TLS automático) ────────────────────────────
echo "==> Configurando Caddy"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip zstd

    # API + SPA — tudo no backend (porta 8000)
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    # Arquivos estáticos do frontend (JS/CSS/imagens) servidos direto
    # pelo Caddy (mais rápido que passar pelo uvicorn)
    handle /assets/* {
        root * $ZIGGS_HOME/frontend/dist
        file_server
        header Cache-Control "public, max-age=31536000, immutable"
    }

    # Logs de acesso
    log {
        output file /var/log/caddy/ziggs.log
        format json
    }
}

www.$DOMAIN {
    redir https://$DOMAIN{uri} permanent
}
EOF

systemctl restart caddy

# ── 12. Firewall ──────────────────────────────────────────────────────────
echo "==> Configurando firewall"
ufw allow 22/tcp    comment "SSH"
ufw allow 80/tcp    comment "HTTP"
ufw allow 443/tcp   comment "HTTPS"
ufw allow 51820/udp comment "WireGuard (se esta VPS também for túnel)"
ufw --force enable

# ── 13. Postgres: tuning baseado na RAM disponível ────────────────────────
PG_CONF=$(find /etc/postgresql -name postgresql.conf -path "*/16/*" | head -1)
if [ -n "$PG_CONF" ]; then
  echo "==> Tuning Postgres"
  TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
  TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
  cp "$PG_CONF" "$PG_CONF.bak"

  # shared_buffers = 20% da RAM (mín 128MB), effective_cache_size = 60% da RAM
  SHARED_BUF_MB=$((TOTAL_RAM_MB / 5))
  [ "$SHARED_BUF_MB" -lt 128 ] && SHARED_BUF_MB=128
  CACHE_SIZE_MB=$((TOTAL_RAM_MB * 6 / 10))
  # work_mem baixo em servers pequenos (evita OOM com muitas conexões)
  WORK_MEM_MB=$((TOTAL_RAM_MB >= 4096 ? 16 : 4))

  cat >> "$PG_CONF" <<EOF

# Ziggs — tuning de produção (RAM: $((TOTAL_RAM_MB / 1024))GB)
shared_buffers = ${SHARED_BUF_MB}MB
effective_cache_size = ${CACHE_SIZE_MB}MB
maintenance_work_mem = 64MB
work_mem = ${WORK_MEM_MB}MB
max_connections = 50
random_page_cost = 1.1
effective_io_concurrency = 200
EOF
  systemctl restart postgresql
fi

# ── 14. Backup automático (diário, 7 dias retenção) ───────────────────────
echo "==> Configurando backup automático"
cat > /usr/local/bin/ziggs-backup.sh <<'BKEOF'
#!/bin/bash
# Backup diário do Postgres — 7 dias de retenção.
set -e
BACKUP_DIR=/home/ziggs/backups
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
pg_dump -U ziggs -d ziggs -F c -f "$BACKUP_DIR/ziggs_$DATE.dump"
# Remove backups com mais de 7 dias
find "$BACKUP_DIR" -name "ziggs_*.dump" -mtime +7 -delete
BKEOF
chmod +x /usr/local/bin/ziggs-backup.sh

cat > /etc/cron.d/ziggs-backup <<'CRONEOF'
# Backup diário do banco às 4h da manhã
0 4 * * * root /usr/local/bin/ziggs-backup.sh >/dev/null 2>&1
CRONEOF
chmod 644 /etc/cron.d/ziggs-backup

# ── 15. Auto-update de segurança ──────────────────────────────────────────
echo "==> Auto-update de segurança"
dpkg-reconfigure -plow unattended-upgrades 2>/dev/null || true

# ── Fim ────────────────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
echo "========================================"
echo "  Ziggs deployado com sucesso!"
echo "========================================"
echo ""
echo "Domínio:        https://$DOMAIN"
echo "IP da VPS:      $PUBLIC_IP"
echo ""
echo "DNS (Cloudflare):"
echo "  A     @       $PUBLIC_IP   (Proxied)"
echo "  A     www     $PUBLIC_IP   (Proxied)"
echo ""
echo "Caddy obtém o certificado TLS automaticamente (Let's Encrypt)."
echo "Acesse https://$DOMAIN — deve funcionar em ~30s."
echo ""
echo "Serviços:"
echo "  systemctl status ziggs-backend"
echo "  systemctl status ziggs-bot"
echo "  systemctl status caddy"
echo ""
echo "Logs:"
echo "  journalctl -u ziggs-backend -f"
echo "  journalctl -u ziggs-bot -f"
echo "  journalctl -u caddy -f"
echo ""
echo "Backup: /home/ziggs/backups/ (diário às 4h, 7 dias retenção)"
echo ""
echo "SECRETS GUARDADOS (NÃO compartilhe):"
echo "  DB_PASS:             $DB_PASS"
echo "  SECRET_KEY:          $SECRET_KEY"
echo "  BOT_API_SECRET:      $BOT_API_SECRET"
echo "  COMPANION_API_SECRET: $COMPANION_API_SECRET"
echo ""
echo "  Guarde estes valores — estão em:"
echo "    $ZIGGS_HOME/backend/.env"
echo "    $ZIGGS_HOME/bot-v2/.env"
echo "========================================"