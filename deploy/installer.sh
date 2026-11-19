#!/bin/bash
# Installe ou met à jour Phare. À lancer en root sur l'hôte Proxmox, depuis le dossier du dépôt :
#   bash deploy/installer.sh
# Au premier passage, le script copie deploy/agent.conf.exemple dans /etc/phare/agent.conf et s'arrête :
# relis ce fichier, puis relance-le.
#   - hôte : /usr/local/sbin/phare-agent (commande forcée de la clé SSH de Phare) et /usr/local/sbin/modele-ct
#   - CT de Phare (créé s'il n'existe pas) : /opt/phare, service phare.service, /etc/phare/phare.env
set -euo pipefail
cd "$(dirname "$0")/.."
CONF=/etc/phare/agent.conf

[ "$(id -u)" = 0 ] || { echo "À lancer en root sur l'hôte Proxmox." >&2; exit 1; }
command -v pct >/dev/null || { echo "pct introuvable : ce script tourne sur l'hôte Proxmox." >&2; exit 1; }

if [ ! -f "$CONF" ]; then
  install -d -m 700 /etc/phare
  install -m 600 deploy/agent.conf.exemple "$CONF"
  echo "Réglages copiés dans $CONF."
  echo "Relis-les (IP, numéros de CT, stockage, réseaux autorisés), puis relance : bash deploy/installer.sh"
  exit 0
fi
# shellcheck disable=SC1090
. "$CONF"
for v in HOTE_IP CT_PHARE IP_PHARE PASSERELLE MASQUE STOCKAGE GABARIT PONT RESEAUX_WEB RESEAUX_SSH CADDY_IP; do
  [ -n "${!v:-}" ] || { echo "$v manquant dans $CONF" >&2; exit 1; }
done
DNS=${DNS:-$PASSERELLE}
DOMAINE_LOCAL=${DOMAINE_LOCAL:-lan}
FUSEAU=${FUSEAU:-Europe/Paris}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "> Agent sur l'hôte"
install -m 0755 -o root -g root agent/phare-agent /usr/local/sbin/phare-agent
install -m 0755 -o root -g root agent/modele-ct /usr/local/sbin/modele-ct
install -d -m 700 /var/lib/phare-agent /var/lib/phare-agent/jobs
touch /var/log/phare-agent.log && chmod 600 /var/log/phare-agent.log

if ! pct status "$CT_PHARE" >/dev/null 2>&1; then
  if ! pveam list "${GABARIT%%:*}" | grep -qF "$GABARIT"; then
    echo "Gabarit $GABARIT absent. Liste : pveam update && pveam available --section system" >&2
    echo "puis : pveam download ${GABARIT%%:*} <nom du gabarit>, et corrige GABARIT dans $CONF." >&2
    exit 1
  fi
  echo "> Création du CT $CT_PHARE phare ($IP_PHARE)"
  pct create "$CT_PHARE" "$GABARIT" --hostname phare --cores 1 --memory 512 --swap 256 \
    --rootfs "$STOCKAGE:4" --unprivileged 1 --features nesting=1 \
    --net0 "name=eth0,bridge=$PONT,ip=$IP_PHARE/$MASQUE,gw=$PASSERELLE" --nameserver "$DNS" --searchdomain "$DOMAINE_LOCAL" \
    --onboot 1 --timezone "$FUSEAU" --tags "infra;phare" \
    --description "Phare : gestion des sites (CT, services, Caddy, trafic). Réseau local et VPN seulement, sans mot de passe."
  pct start "$CT_PHARE"
  for _ in $(seq 1 30); do pct exec "$CT_PHARE" -- getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done
  pct exec "$CT_PHARE" -- sh -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -q && apt-get install -y -q --no-install-recommends python3 openssh-client nftables ca-certificates'
fi

echo "> Code dans /opt/phare"
pct exec "$CT_PHARE" -- sh -c 'id phare >/dev/null 2>&1 || useradd -r -d /var/lib/phare -s /usr/sbin/nologin phare; mkdir -p /opt/phare /var/lib/phare /etc/phare; chown phare:phare /var/lib/phare'
tar czf "$TMP/code.tgz" --exclude=__pycache__ phare web
pct push "$CT_PHARE" "$TMP/code.tgz" /tmp/code.tgz
pct exec "$CT_PHARE" -- sh -c 'rm -rf /opt/phare/phare /opt/phare/web && tar xzf /tmp/code.tgz --no-same-owner -C /opt/phare && rm /tmp/code.tgz'
pct push "$CT_PHARE" deploy/phare.service /etc/systemd/system/phare.service
sed -e "s#@RESEAUX_WEB@#$RESEAUX_WEB#" -e "s#@RESEAUX_SSH@#$RESEAUX_SSH#" deploy/nftables.conf > "$TMP/nftables.conf"
pct push "$CT_PHARE" "$TMP/nftables.conf" /etc/nftables.conf

echo "> Réglages de l'interface"
if ! pct exec "$CT_PHARE" -- test -f /etc/phare/phare.env; then
  cat > "$TMP/phare.env" <<EOF
# Réglages de Phare (créés par installer.sh). Après une modification : systemctl restart phare
PHARE_AGENT=root@$HOTE_IP
PHARE_CADDY=$CADDY_IP
PHARE_HOTES=$IP_PHARE,phare.$DOMAINE_LOCAL,phare,localhost,127.0.0.1
PHARE_IP_PUBLIQUE=${IP_PUBLIQUE:-}
PHARE_PVE_API=https://$HOTE_IP:8006/api2/json
PHARE_DOMAINE_EXEMPLE=${DOMAINE_EXEMPLE:-exemple.fr}
EOF
  pct push "$CT_PHARE" "$TMP/phare.env" /etc/phare/phare.env --perms 0644
  echo "  /etc/phare/phare.env créé"
fi

echo "> Clé SSH de Phare vers l'agent"
pct exec "$CT_PHARE" -- sh -c "test -f /etc/phare/cle_agent || ssh-keygen -q -t ed25519 -N '' -C phare@ct$CT_PHARE -f /etc/phare/cle_agent; chown phare:phare /etc/phare/cle_agent /etc/phare/cle_agent.pub; chmod 600 /etc/phare/cle_agent"
# empreinte de l'hôte, pour StrictHostKeyChecking=yes côté Phare
echo "$HOTE_IP $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)" > "$TMP/known_hosts"
pct push "$CT_PHARE" "$TMP/known_hosts" /etc/phare/known_hosts --perms 0644
CLE=$(pct exec "$CT_PHARE" -- cat /etc/phare/cle_agent.pub)
LIGNE="command=\"/usr/local/sbin/phare-agent\",from=\"$IP_PHARE\",restrict $CLE"
AUTH=/root/.ssh/authorized_keys
[ -e /etc/pve/priv/authorized_keys ] && AUTH=/etc/pve/priv/authorized_keys
if ! grep -qF "$CLE" "$AUTH"; then
  cp "$AUTH" "/root/authorized_keys.avant-phare-$(date +%Y%m%d-%H%M%S)"
  echo "$LIGNE" >> "$AUTH"
  echo "  clé ajoutée dans $AUTH (commande forcée phare-agent, depuis $IP_PHARE seulement)"
fi

echo "> Lecture de l'état par l'API Proxmox (jeton en lecture seule)"
# CA du cluster pour vérifier le certificat de l'API ; le jeton n'est jamais affiché.
pct push "$CT_PHARE" /etc/pve/pve-root-ca.pem /etc/phare/pve-ca.pem --perms 0644
if ! pct exec "$CT_PHARE" -- test -s /etc/phare/pve-jeton; then
  pveum user list --output-format json | python3 -c 'import json,sys;sys.exit(0 if any(u["userid"]=="phare@pve" for u in json.load(sys.stdin)) else 1)' \
    || pveum user add phare@pve --comment "Phare : lecture seule de l'état"
  pveum acl modify / --users phare@pve --roles PVEAuditor
  pveum user token remove phare@pve lecture >/dev/null 2>&1 || true
  pveum user token add phare@pve lecture --privsep 0 --comment "Phare, lecture seule" --output-format json \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["value"])' \
    | pct exec "$CT_PHARE" -- sh -c 'umask 077; cat > /etc/phare/pve-jeton; chown phare:phare /etc/phare/pve-jeton'
  echo "  jeton phare@pve!lecture créé (rôle PVEAuditor sur /)"
fi

echo "> Pare-feu et service"
pct exec "$CT_PHARE" -- sh -c 'set -e; nft -c -f /etc/nftables.conf; systemctl enable nftables >/dev/null 2>&1; nft -f /etc/nftables.conf; systemctl daemon-reload; systemctl enable phare >/dev/null 2>&1; systemctl restart phare; sleep 2; systemctl is-active phare'
echo "> Fini : http://$IP_PHARE/"
