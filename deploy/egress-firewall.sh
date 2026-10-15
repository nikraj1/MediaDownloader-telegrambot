#!/usr/bin/env bash
# Egress firewall for the bot's Docker network (run on the HOST, as root).
#
# Drops traffic from the bot subnet to private/loopback/link-local/CGNAT
# ranges and the host's own IPs. This is the backstop for every request
# yt-dlp/FFmpeg make after the app's pre-flight check, and for DNS rebinding.
#
#   sudo deploy/egress-firewall.sh apply     # install / refresh rules
#   sudo deploy/egress-firewall.sh remove    # remove rules
#   sudo deploy/egress-firewall.sh show
#
# Review before running. Persist with iptables-persistent or the systemd
# unit in deploy/mediabot-egress.service (runs after docker.service).
set -euo pipefail

BOT_SUBNET="${BOT_SUBNET:-172.30.99.0/24}"
CHAIN="MEDIABOT-EGRESS"
# Upstream resolvers (Docker's embedded DNS forwards from the host, but allow
# direct DNS too in case a container resolver is configured).
RESOLVERS="${RESOLVERS:-$(awk '/^nameserver/{print $2}' /etc/resolv.conf | grep -Ev '^(127\.|::1)' | grep -v ':' || true)}"
HOST_IPS="${HOST_IPS:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v ':' || true)}"
BLOCKED_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8 169.254.0.0/16 100.64.0.0/10 0.0.0.0/8 224.0.0.0/4 240.0.0.0/4"

apply() {
  iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
  iptables -C DOCKER-USER -s "$BOT_SUBNET" -j "$CHAIN" 2>/dev/null \
    || iptables -I DOCKER-USER 1 -s "$BOT_SUBNET" -j "$CHAIN"

  iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  for r in $RESOLVERS; do
    iptables -A "$CHAIN" -d "$r" -p udp --dport 53 -j RETURN
    iptables -A "$CHAIN" -d "$r" -p tcp --dport 53 -j RETURN
  done
  for net in $BLOCKED_NETS; do
    iptables -A "$CHAIN" -d "$net" -j DROP
  done
  for ip in $HOST_IPS; do
    iptables -A "$CHAIN" -d "$ip" -j DROP
  done
  iptables -A "$CHAIN" -j RETURN
  echo "applied egress rules for $BOT_SUBNET"
}

remove() {
  while iptables -C DOCKER-USER -s "$BOT_SUBNET" -j "$CHAIN" 2>/dev/null; do
    iptables -D DOCKER-USER -s "$BOT_SUBNET" -j "$CHAIN"
  done
  iptables -F "$CHAIN" 2>/dev/null || true
  iptables -X "$CHAIN" 2>/dev/null || true
  echo "removed egress rules"
}

case "${1:-}" in
  apply) apply ;;
  remove) remove ;;
  show) iptables -S DOCKER-USER; iptables -S "$CHAIN" 2>/dev/null || true ;;
  *) echo "usage: $0 apply|remove|show" >&2; exit 2 ;;
esac
# NOTE: IPv6 is not covered. Docker networks are IPv4-only unless you enable
# IPv6; if you do, add equivalent ip6tables rules (::1, fc00::/7, fe80::/10).
