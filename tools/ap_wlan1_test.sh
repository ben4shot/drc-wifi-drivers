#!/usr/bin/env bash
# Prueba (sin GamePad): AP de la consola en wlan1 (TP-Link, rtw89 parcheado) con la vinculación guardada, MTU 1756,
# lectura del TSF y limpieza al terminar. Lanzar desde una unidad: sudo systemd-run --unit=ap1 bash ap_wlan1_test.sh
export PATH=/usr/sbin:/sbin:$PATH
exec >/tmp/ap1.log 2>&1
cleanup() { pkill -f "hostapd.*ap1.conf"; sleep 1; ip link set wlan1 down; ip link set wlan1 address "$ORIG"; nmcli dev set wlan1 managed yes; echo "limpio $(date +%T)"; echo FIN; }
ORIG=$(cat /sys/class/net/wlan1/address)
trap cleanup EXIT
iw phy | grep -A2 " 5180.0 MHz" | head -3
nmcli dev set wlan1 managed no; ip link set wlan1 down
ip link set wlan1 address d8:3a:dd:12:34:00 && echo "mac ok"
ip link set wlan1 mtu 1756 2>&1 && echo "mtu1756 ok"
ip link set wlan1 up
ip addr add 192.168.1.10/29 dev wlan1
sed -e 's/^interface=wlan0/interface=wlan1/' /var/lib/drc/generated_ap_normal.conf > /tmp/ap1.conf
HP="$HOME/drc-build/vanilla-drc-hostap/hostapd/hostapd"
timeout 60 $HP -dd /tmp/ap1.conf > /tmp/ap1_hostapd.log 2>&1 &
sleep 10
iw dev wlan1 info | grep -E "type|ssid|channel|addr"
for i in 1 2 3 4; do python3 -c '
import struct,time
f=open("/sys/class/net/wlan1/tsf","rb"); a=struct.unpack("<Q",f.read(8))[0]; m=time.monotonic()
time.sleep(1); f.seek(0); b=struct.unpack("<Q",f.read(8))[0]; n=time.monotonic()
print("tsf",b,"dTSF=%d dPi=%d us"%(b-a,(n-m)*1e6))' 2>&1 | tail -1; done
grep -E "AP-ENABLED|ready|failed|Could not|rejected|ERROR" /tmp/ap1_hostapd.log | head -8
sleep 40
