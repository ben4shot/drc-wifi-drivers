#!/usr/bin/env bash
# Compila e instala en ESTE equipo el rtw89 parcheado (third_party/rtw89 = fork morrownr, + patch_rtw89.py), que
# expone el TSF en /sys/class/net/<if>/tsf. El rtw89 del kernel no lo expone: el GamePad se asocia pero
# descarta el vídeo. Sustituye al del kernel (queda en la lista negra de /etc/modprobe.d/rtw89.conf).
# Uso:  sudo bash tools/install_rtw89.sh            (deshacer: sudo bash tools/install_rtw89.sh --uninstall)
# Tras actualizar el kernel hay que volver a ejecutarlo.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "${BASH_SOURCE[0]}" "$@"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${RTW89_SRC:-$HERE/../third_party/rtw89}"
# Fuentes: el fork de morrownr (RTW89_REF cambia el commit; por defecto el probado, 3cdbb6b). Se descargan solas la primera vez.
if [ ! -d "$SRC" ]; then
    git clone https://github.com/morrownr/rtw89.git "$SRC"
    git -C "$SRC" checkout "${RTW89_REF:-3cdbb6b81e00b72cf27bbdd4d4f1b96f9c15be27}"
fi
KVER="$(uname -r)"
INTREE="rtw89_8852au rtw89_8852cu rtw89_8852bu rtw89_8851bu rtw89_8922au rtw89_8852a rtw89_8852b rtw89_8852c rtw89_8851b rtw89_8922a rtw89_8852b_common rtw89_usb rtw89_core"
unload() { for m in $INTREE; do modprobe -r "$m" 2>/dev/null || true; modprobe -r "${m}_git" 2>/dev/null || true; done; }

BUILD="$(mktemp -d)"; trap 'rm -rf "$BUILD"' EXIT
cp -r "$SRC/." "$BUILD/"     # se compila fuera del árbol (el disco del proyecto puede no admitir permisos Unix)

if [ "${1:-}" = "--uninstall" ]; then
    unload
    make -C "$BUILD" KVER="$KVER" uninstall
    rm -f /etc/modprobe.d/rtw89.conf
    modprobe rtw89_8852au 2>/dev/null || true
    echo "rtw89 del kernel restaurado."; exit 0
fi

[ -d "/lib/modules/$KVER/build" ] || { echo "Faltan las cabeceras del kernel $KVER (linux-headers)." >&2; exit 1; }
grep -q dev_attr_tsf "$BUILD/mac80211.c" || python3 "$HERE/patch_rtw89.py" "$BUILD"
make -C "$BUILD" -j"$(nproc)" KVER="$KVER"
make -C "$BUILD" KVER="$KVER" install
install -m 644 "$BUILD/rtw89.conf" /etc/modprobe.d/rtw89.conf
unload
modprobe rtw89_8852au_git
# El adaptador se reenumera al cargar el módulo (cambio de modo USB): la interfaz tarda ~7 s en volver.
for _ in $(seq 1 20); do
    for t in /sys/class/net/*/tsf; do [ -e "$t" ] && { echo "OK: TSF disponible en $t"; exit 0; }; done
    sleep 1
done
echo "Instalado, pero ninguna interfaz expone aún el TSF (¿adaptador desconectado?). Reconéctalo y mira /sys/class/net/<if>/tsf." >&2
exit 1
