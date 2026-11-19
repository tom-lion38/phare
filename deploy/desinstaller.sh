#!/bin/bash
# Retire Phare de l'hôte Proxmox : clé de l'agent, agent, jeton API.
# Ne détruit pas le CT de Phare et ne touche pas au Caddyfile. À lancer en root sur l'hôte.
set -euo pipefail
CONF=/etc/phare/agent.conf
[ "$(id -u)" = 0 ] || { echo "À lancer en root sur l'hôte Proxmox." >&2; exit 1; }
CT_PHARE=""
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

AUTH=/root/.ssh/authorized_keys
[ -e /etc/pve/priv/authorized_keys ] && AUTH=/etc/pve/priv/authorized_keys
if grep -q 'command="/usr/local/sbin/phare-agent"' "$AUTH"; then
  cp "$AUTH" "/root/authorized_keys.avant-retrait-phare-$(date +%Y%m%d-%H%M%S)"
  grep -v 'command="/usr/local/sbin/phare-agent"' "$AUTH" > "$AUTH.tmp" || true
  cat "$AUTH.tmp" > "$AUTH"
  rm -f "$AUTH.tmp"
  echo "> clé de Phare retirée de $AUTH"
fi

pveum user token remove phare@pve lecture >/dev/null 2>&1 && echo "> jeton phare@pve!lecture supprimé" || true
pveum user delete phare@pve >/dev/null 2>&1 && echo "> utilisateur phare@pve supprimé" || true
rm -f /usr/local/sbin/phare-agent /usr/local/sbin/modele-ct
echo "> agent retiré (journal gardé : /var/log/phare-agent.log, tâches : /var/lib/phare-agent)"
echo
echo "Le CT de Phare${CT_PHARE:+ ($CT_PHARE)} n'est pas touché. Pour le supprimer : pct stop <id> puis pct destroy <id>"
echo "Les blocs ajoutés par Phare restent dans le Caddyfile, après la ligne « # ==== Sites ajoutés par Phare ==== »."
echo "Les réglages restent dans $CONF."
