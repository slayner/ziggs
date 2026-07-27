#!/bin/bash
# Provisionamento de VPS WireGuard pro Ziggs Companion.
# Rodar como root numa VPS Ubuntu/Debian perto do datacenter do Albion.
#
# Uso:
#   curl -sSL https://raw.githubusercontent.com/.../companion-vps-setup.sh | bash -s -- <CLIENT_PUBKEY>
#
# Ou local:
#   scp companion-vps-setup.sh root@vps:/root/ && ssh root@vps "bash /root/companion-vps-setup.sh <CLIENT_PUBKEY>"
#
# Depois de rodar, copia de volta a chave pública do SERVER e o endpoint.
#
# SEGURANÇA: o túnel só encaminha tráfego pros IPs dos servidores do Albion.
# Default DROP no FORWARD do wg0 + ALLOW só pra IPs do Albion (resolvidos dos
# hostnames oficiais). Mesmo que o client seja modificado pra adicionar rotas
# extras, a VPS droppa o tráfego não-Albion. Um cronjob re-resolve os IPs a
# cada hora porque os datacenters do Albion rotacionam.

set -e

CLIENT_PUBKEY="${1:-}"

if [ -z "$CLIENT_PUBKEY" ]; then
  echo "Uso: $0 <CLIENT_PUBKEY_BASE64>"
  echo "  Gere no companion: botão 'gerar' → copie a chave pública do cliente."
  exit 1
fi

# Hostnames oficiais dos servidores do Albion (batem com companion/albion_ips.rs)
ALBION_HOSTNAMES=(
  "gameinfo.albiononline.com"
  "gameinfo-ams.albiononline.com"
  "gameinfo-sgp.albiononline.com"
)

echo "==> Instalando wireguard + iptables-persistent"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard qrencode iptables-persistent dnsutils >/dev/null

echo "==> Gerando chaves do servidor"
SERVER_PRIVKEY=$(wg genkey)
SERVER_PUBKEY=$(echo "$SERVER_PRIVKEY" | wg pubkey)
SERVER_PORT=51820

echo "==> Resolvendo IPs dos servidores do Albion"
albion_ips=()
for host in "${ALBION_HOSTNAMES[@]}"; do
  while read -r ip; do
    [ -n "$ip" ] && albion_ips+=("$ip")
  done < <(dig +short "$host" A 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
done
# dedup
mapfile -t albion_ips < <(printf '%s\n' "${albion_ips[@]}" | sort -u)
echo "    IPs resolvidos: ${albion_ips[*]}"

echo "==> Configurando interface wg0"
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
PrivateKey = $SERVER_PRIVKEY
Address = 10.99.0.1/24
ListenPort = $SERVER_PORT
# Default DROP no FORWARD do wg0 — só tráfego pros IPs do Albion passa.
# As regras ALLOW são geradas por PostUp e pelo cron /etc/cron.daily/ziggs-albion-routes.
PostUp = iptables -P FORWARD DROP; iptables -A FORWARD -i wg0 -m state --state ESTABLISHED,RELATED -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -P FORWARD ACCEPT; iptables -D FORWARD -i wg0 -m state --state ESTABLISHED,RELATED -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
# Ziggs Companion (cole aqui a chave pública gerada no companion)
PublicKey = $CLIENT_PUBKEY
AllowedIPs = 10.99.0.2/32
EOF

chmod 600 /etc/wireguard/wg0.conf

echo "==> Gerando script de atualização de rotas dos IPs do Albion"
# Este script adiciona/remove as regras ALLOW para os IPs do Albion.
# Roda no PostUp do wg0 e a cada hora via cron (IPs rotacionam).
cat > /usr/local/bin/ziggs-albion-routes.sh <<'SCRIPT_EOF'
#!/bin/bash
# Adiciona regras iptables ALLOW pros IPs atuais dos servidores do Albion,
# remove as antigas. Idempotente.
set -e
CHAIN="ZIGGS_ALBION"

HOSTNAMES=(
  "gameinfo.albiononline.com"
  "gameinfo-ams.albiononline.com"
  "gameinfo-sgp.albiononline.com"
)

# Resolve IPs atuais
ips=()
for host in "${HOSTNAMES[@]}"; do
  while read -r ip; do
    [ -n "$ip" ] && ips+=("$ip")
  done < <(dig +short "$host" A 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
done
mapfile -t ips < <(printf '%s\n' "${ips[@]}" | sort -u)

# Cria chain dedicada (idempotente)
iptables -N "$CHAIN" 2>/dev/null || true
# Limpa a chain
iptables -F "$CHAIN" 2>/dev/null || true

# Para cada IP do Albion: ALLOW forward do wg0 pra esse IP
for ip in "${ips[@]}"; do
  iptables -A "$CHAIN" -i wg0 -d "$ip" -j ACCEPT
done

# Gameplay Photon: os mesmos /24 reconhecidos pelo sniffer do companion.
iptables -A "$CHAIN" -i wg0 -d 5.188.125.0/24 -p udp -m multiport --dports 5055,5056,4535 -j ACCEPT
iptables -A "$CHAIN" -i wg0 -d 5.45.187.0/24 -p udp -m multiport --dports 5055,5056,4535 -j ACCEPT
iptables -A "$CHAIN" -i wg0 -d 193.169.238.0/24 -p udp -m multiport --dports 5055,5056,4535 -j ACCEPT

# Loga o que foi droppado (com rate-limit pra não encher o log)
iptables -A "$CHAIN" -i wg0 -m limit --limit 5/min -j LOG --log-prefix "ZIGGS_DROP " --log-level 4
# Drop default
iptables -A "$CHAIN" -i wg0 -j DROP

# Remove jumps antigos da FORWARD pra essa chain
while iptables -D FORWARD -i wg0 -j "$CHAIN" 2>/dev/null; do :; done
# Adiciona jump no topo do FORWARD (depois do ESTABLISHED,RELATED)
iptables -I FORWARD 2 -i wg0 -j "$CHAIN"

echo "ziggs-albion-routes: ${#ips[@]} IPs do Albion liberados no FORWARD"
SCRIPT_EOF
chmod +x /usr/local/bin/ziggs-albion-routes.sh

echo "==> Habilitando IP forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sed -i 's/^#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf

echo "==> Subindo wg0"
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

# Aplica as regras dos IPs do Albion pela primeira vez
/usr/local/bin/ziggs-albion-routes.sh

echo "==> Persistindo regras iptables"
netfilter-persistent save

echo "==> Instalando cron pra re-resolver IPs do Albion a cada hora"
# Os datacenters do Albion rotacionam IPs — atualiza as regras periodicamente.
cat > /etc/cron.d/ziggs-albion-routes <<'CRON_EOF'
# Re-resolve os IPs dos servidores do Albion e atualiza as regras iptables.
# Ziggs Companion — split-tunneling seguro (só Albion passa pelo túnel).
0 * * * * root /usr/local/bin/ziggs-albion-routes.sh >/dev/null 2>&1
CRON_EOF
chmod 644 /etc/cron.d/ziggs-albion-routes
systemctl reload cron 2>/dev/null || systemctl reload crond 2>/dev/null || true

PUBLIC_IP=$(curl -s ifconfig.me)

echo ""
echo "========================================"
echo "  VPS WireGuard configurada"
echo "========================================"
echo ""
echo "Cole no companion (aba Rota / Túnel):"
echo ""
echo "  Endpoint:      $PUBLIC_IP:$SERVER_PORT"
echo "  Server pubkey: $SERVER_PUBKEY"
echo ""
echo "Estado do túnel:"
wg show
echo ""
echo "Regras iptables (só Albion passa):"
iptables -L ZIGGS_ALBION -n -v 2>/dev/null || echo "  (chain será criada no PostUp)"
echo ""
echo "Cron ativo: /etc/cron.d/ziggs-albion-routes (re-resolve IPs a cada hora)"
echo "========================================"
