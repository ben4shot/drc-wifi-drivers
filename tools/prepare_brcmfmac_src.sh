#!/usr/bin/env bash
set -euo pipefail

# Script para preparar el código fuente de brcmfmac para compilarse como módulo externo con linux-headers
TARGET_HOST="${1:-pi@raspberrypi.local}"
REMOTE_SRC_DIR="~/brcmfmac-src"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Destino local: por defecto brcmfmac-src/ del repo (parcheado, ojo: se sobrescribe); las pruebas pasan uno temporal.
LOCAL_SRC_DIR="${2:-${SCRIPT_DIR}/../brcmfmac-src}"

echo "=== Verificando e instalando linux-source en ${TARGET_HOST} ==="
ssh -o BatchMode=yes "${TARGET_HOST}" bash << 'EOF'
set -euo pipefail
if [ ! -f /usr/src/linux-source-6.12.tar.xz ]; then
    echo "Instalando linux-source-6.12..."
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends linux-source-6.12
fi

echo "Extrayendo fuentes de brcm80211 en ~/brcmfmac-src..."
mkdir -p ~/brcmfmac-src
tar -xf /usr/src/linux-source-6.12.tar.xz --strip-components=6 -C ~/brcmfmac-src linux-source-6.12/drivers/net/wireless/broadcom/brcm80211
EOF

echo "=== Sincronizando fuentes hacia el host local (${LOCAL_SRC_DIR}) ==="
mkdir -p "${LOCAL_SRC_DIR}"
rsync -avz --exclude="*.o" --exclude="*.ko" --exclude="*.mod*" --exclude=".*.cmd" \
    "${TARGET_HOST}:brcmfmac-src/" "${LOCAL_SRC_DIR}/"

echo "=== Fuentes de brcmfmac preparadas correctamente ==="
