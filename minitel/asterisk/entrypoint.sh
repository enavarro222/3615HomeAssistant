#!/bin/sh
set -eu

: "${SIP_PASSWORD:?SIP_PASSWORD manquant, voir .env.example}"
: "${LAN_CIDR:?LAN_CIDR manquant, voir .env.example}"
# Compte du panneau de test : sans mot de passe fourni (Docker), on en tire un
# que personne ne connaît, et le compte reste inutilisable.
WEB_SIP_PASSWORD=${WEB_SIP_PASSWORD:-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')}

sed -e "s|@SIP_PASSWORD@|${SIP_PASSWORD}|" -e "s|@WEB_SIP_PASSWORD@|${WEB_SIP_PASSWORD}|" \
    -e "s|@LAN_CIDR@|${LAN_CIDR}|" /etc/asterisk/pjsip.conf.in > /etc/asterisk/pjsip.conf
chown asterisk:asterisk /etc/asterisk/pjsip.conf
chmod 640 /etc/asterisk/pjsip.conf

exec asterisk -f -U asterisk -G asterisk -vvv
