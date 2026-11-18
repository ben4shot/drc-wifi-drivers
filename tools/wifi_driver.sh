#!/usr/bin/env bash
# Alterna en la Pi entre brcmfmac/firmware ORIGINALES y PARCHEADOS (o mezclados).
# Uso:  wifi_driver.sh <modo> [usuario@host]      (sin host: se ejecuta en local, como root)
#   status    muestra qué hay instalado (módulos y firmware)
#   orig      módulos y firmware originales
#   patched   módulos y firmware parcheados
#   fw-orig   firmware original + módulos parcheados
#   mod-orig  módulos originales + firmware parcheado
# Tras cambiar recarga brcmfmac. start_drc.sh no restaura los parcheados por sí solo.
set -euo pipefail
MODE="${1:-status}"; HOST="${2:-}"

if [ -n "$HOST" ]; then
    exec ssh -o BatchMode=yes "$HOST" sudo bash -s -- "$MODE" < "${BASH_SOURCE[0]}"
fi
[ "$(id -u)" -eq 0 ] || exec sudo bash "${BASH_SOURCE[0]}" "$MODE"
export PATH=/usr/sbin:/sbin:$PATH

M="/lib/modules/$(uname -r)/kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac"
FW=/usr/lib/firmware/cypress/cyfmac43455-sdio-standard.bin
BAK=/var/lib/drc-patched
MODS=(brcmfmac.ko.xz wcc/brcmfmac-wcc.ko.xz cyw/brcmfmac-cyw.ko.xz bca/brcmfmac-bca.ko.xz)
mkdir -p "$BAK"

is_orig() { cmp -s "$1" "$1.orig"; }

# Guarda una vez las copias parcheadas (solo si lo instalado ahora difiere del .orig).
save_patched() {
    for f in "${MODS[@]}"; do
        is_orig "$M/$f" || cp -p "$M/$f" "$BAK/$(basename "$f")"
    done
    is_orig "$FW" || cp -p "$FW" "$BAK/fw.bin"
}
need_backup() {
    [ -f "$BAK/$1" ] || { echo "ERROR: no hay copia parcheada $BAK/$1 (instala los parches con install_brcmfmac.sh/install_firmware.sh)" >&2; exit 1; }
}
set_mods() {  # orig|patched
    for f in "${MODS[@]}"; do
        if [ "$1" = orig ]; then cp -p "$M/$f.orig" "$M/$f"
        else need_backup "$(basename "$f")"; cp -p "$BAK/$(basename "$f")" "$M/$f"; fi
    done
}
set_fw() {
    if [ "$1" = orig ]; then cp -p "$FW.orig" "$FW"
    else need_backup fw.bin; cp -p "$BAK/fw.bin" "$FW"; fi
}
reload() {
    modprobe -r brcmfmac_wcc brcmfmac_cyw brcmfmac_bca brcmfmac
    sleep 1; modprobe brcmfmac
    for _ in $(seq 1 50); do ip link show wlan0 >/dev/null 2>&1 && break; sleep 0.2; done
}
status() {
    local m=parcheados f=parcheado
    is_orig "$M/brcmfmac.ko.xz" && m=ORIGINALES
    is_orig "$FW" && f=ORIGINAL
    echo "módulos: $m | firmware: $f | wlan0: $(ip -br link show wlan0 2>/dev/null | awk '{print $2}')"
}

case "$MODE" in
    status) status; exit 0 ;;
    orig)     save_patched; set_mods orig;    set_fw orig ;;
    patched)  set_mods patched; set_fw patched ;;
    fw-orig)  save_patched; set_mods patched; set_fw orig ;;
    mod-orig) save_patched; set_mods orig;    set_fw patched ;;
    *) echo "modo desconocido: $MODE (status|orig|patched|fw-orig|mod-orig)" >&2; exit 2 ;;
esac
reload
status
