#!/usr/bin/env bash
set -euo pipefail

# Script de instalación y recarga de firmware en Raspberry Pi 4B
TARGET_HOST="${1:-pi@raspberrypi.local}"
REMOTE_FW_DIR="/usr/lib/firmware/cypress"
REMOTE_FW_FILE="${REMOTE_FW_DIR}/cyfmac43455-sdio-standard.bin"
REMOTE_ORIG_FILE="${REMOTE_FW_FILE}.orig"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Firmware ORIGINAL de la Pi (no se distribuye en el repo): cópialo de /lib/firmware/cypress/ de la Pi y pásalo con
# DRC_FW_BIN, o déjalo junto a este script.
LOCAL_FW="${DRC_FW_BIN:-${SCRIPT_DIR}/cyfmac43455-sdio-standard.bin}"
LOCAL_PATCHED="/tmp/cyfmac43455-sdio-standard.patched.bin"
[ -f "${LOCAL_FW}" ] || { echo "No está el firmware original: ${LOCAL_FW} (usa DRC_FW_BIN=ruta)" >&2; exit 1; }

echo "=== Generando firmware parcheado ==="
PYTHONPATH="${SCRIPT_DIR}" python3 -c "
from pathlib import Path
from patch_firmware import load_and_verify_firmware, apply_all_patches
buf = load_and_verify_firmware(Path('${LOCAL_FW}'))
apply_all_patches(buf)
Path('${LOCAL_PATCHED}').write_bytes(buf)
print(f'Firmware parcheado generado: {len(buf)} bytes')
"

echo "=== Transfiriendo firmware parcheado a ${TARGET_HOST} ==="
scp "${LOCAL_PATCHED}" "${TARGET_HOST}:/tmp/cyfmac43455-sdio-standard.patched.bin"

echo "=== Instalando en ${TARGET_HOST} con respaldo .orig ==="
ssh -o BatchMode=yes "${TARGET_HOST}" sudo bash << 'EOF'
set -euo pipefail
REMOTE_FW_DIR="/usr/lib/firmware/cypress"
REMOTE_FW_FILE="${REMOTE_FW_DIR}/cyfmac43455-sdio-standard.bin"
REMOTE_ORIG_FILE="${REMOTE_FW_FILE}.orig"

if [ ! -f "${REMOTE_ORIG_FILE}" ]; then
    echo "Creando respaldo original en ${REMOTE_ORIG_FILE}..."
    cp -p "${REMOTE_FW_FILE}" "${REMOTE_ORIG_FILE}"
fi

echo "Copiando firmware parcheado a ${REMOTE_FW_FILE}..."
cp /tmp/cyfmac43455-sdio-standard.patched.bin "${REMOTE_FW_FILE}"
rm -f /tmp/cyfmac43455-sdio-standard.patched.bin

echo "Recargando módulo brcmfmac..."
# brcmfmac_wcc depende de brcmfmac: si no se descarga, `modprobe -r brcmfmac` falla y el
# firmware nuevo no llega a cargarse.
modprobe -r brcmfmac_wcc brcmfmac_cyw brcmfmac_bca brcmfmac || true
if lsmod | grep -q '^brcmfmac '; then
    echo "ERROR: brcmfmac sigue cargado; el firmware nuevo no se ha cargado" >&2
    exit 1
fi
modprobe brcmfmac
modprobe brcmfmac_wcc || true

echo "Verificando inicialización en dmesg..."
sleep 2
dmesg | grep -i 'brcmfmac' | tail -n 10
EOF


echo "=== Instalación completada con éxito ==="
