#!/bin/bash
# Démarrage en app Home Assistant : la configuration vient de Home Assistant.
set -euo pipefail

OPTIONS=/data/options.json
SERVICE_OPTIONS=/run/minitel.json

SIP_PASSWORD=$(jq -r '.sip_password' "$OPTIONS")
if [[ ! $SIP_PASSWORD =~ ^[A-Za-z0-9]+$ ]]; then
    echo "sip_password : lettres et chiffres uniquement" >&2
    exit 1
fi

# Sans lan_cidr, on prend le réseau de l'interface par défaut.
LAN_CIDR=$(jq -r '.lan_cidr // ""' "$OPTIONS")
if [[ -z $LAN_CIDR ]]; then
    device=$(ip -4 route show default | awk 'NR == 1 { print $5 }')
    LAN_CIDR=$(ip -4 route show dev "$device" scope link | awk 'NR == 1 { print $1 }')
fi
echo "Réseau autorisé à joindre Asterisk : $LAN_CIDR"

# Le panneau de test appelle Asterisk avec son propre compte, au mot de passe
# tiré à chaque démarrage : il ne sort jamais du conteneur.
WEB_SIP_PASSWORD=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')

# Seuls le titre, le tableau de bord et les pages vont au service : il ne tourne pas en root.
jq '{title, dashboard, pages}' "$OPTIONS" > "$SERVICE_OPTIONS"
chmod 644 "$SERVICE_OPTIONS"

# En réseau hôte : sur 127.0.0.1, car le service n'a aucune authentification.
# Le panneau écoute sur le port d'ingress, mais ne répond qu'au Supervisor.
HA_URL=${HA_URL:-http://supervisor/core} \
HA_TOKEN=$SUPERVISOR_TOKEN \
OPTIONS_FILE=$SERVICE_OPTIONS \
LISTEN_HOST=127.0.0.1 \
WEB_HOST=0.0.0.0 \
WEB_PORT=3616 \
WEB_ALLOWED=172.30.32.2 \
WEB_SIP_PASSWORD=$WEB_SIP_PASSWORD \
    setpriv --reuid=65534 --regid=65534 --clear-groups /opt/service/bin/python -m minitel_ha &

SIP_PASSWORD=$SIP_PASSWORD WEB_SIP_PASSWORD=$WEB_SIP_PASSWORD LAN_CIDR=$LAN_CIDR /entrypoint.sh &

# Si l'un s'arrête, on arrête tout : le watchdog relance l'app.
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
wait -n
