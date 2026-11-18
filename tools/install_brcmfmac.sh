#!/usr/bin/env bash
set -euo pipefail

# Script de compilación, instalación con watchdog y restauración de brcmfmac en Raspberry Pi 4B

ACTION="--install"
TARGET_HOST="${TARGET_HOST:-pi@raspberrypi.local}"

for arg in "$@"; do
    case "$arg" in
        --build-only)
            ACTION="--build-only"
            ;;
        --install)
            ACTION="--install"
            ;;
        --restore)
            ACTION="--restore"
            ;;
        *@*)
            TARGET_HOST="$arg"
            ;;
        *)
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SRC_DIR="${BRCMFMAC_SRC:-${SCRIPT_DIR}/../brcmfmac-src}"

if [ "${ACTION}" = "--restore" ]; then
    echo "=== Restaurando módulos brcm80211 originales en ${TARGET_HOST} ==="
    ssh -o BatchMode=yes "${TARGET_HOST}" sudo bash << 'EOF'
set -euo pipefail
PATH="/usr/sbin:/sbin:$PATH"
KVER=$(uname -r)
BASE_DIR="/lib/modules/${KVER}/kernel/drivers/net/wireless/broadcom/brcm80211"

MODS=(
    "brcmfmac/brcmfmac.ko"
    "brcmfmac/wcc/brcmfmac-wcc.ko"
    "brcmfmac/cyw/brcmfmac-cyw.ko"
    "brcmfmac/bca/brcmfmac-bca.ko"
    "brcmutil/brcmutil.ko"
)

for m in "${MODS[@]}"; do
    tgt="${BASE_DIR}/${m}.xz"
    orig="${tgt}.orig"
    if [ -f "${orig}" ]; then
        echo "Restaurando ${tgt} desde ${orig}..."
        cp -p "${orig}" "${tgt}"
    fi
done

depmod -a
(modprobe -r brcmfmac_wcc brcmfmac_cyw brcmfmac_bca brcmfmac brcmutil || true)
modprobe brcmutil || true
modprobe brcmfmac || true
modprobe brcmfmac_wcc || true
echo "Restauración de módulos originales completada."
EOF
    exit 0
fi

echo "=== Aplicando parches en fuentes locales (${LOCAL_SRC_DIR}/brcmfmac) ==="
PYTHONPATH="${SCRIPT_DIR}" python3 -c "
from pathlib import Path
from patch_brcmfmac import apply_all_driver_patches
apply_all_driver_patches(Path('${LOCAL_SRC_DIR}/brcmfmac'))
print('Parches aplicados a cfg80211.c, core.h y core.c')
"

echo "=== Sincronizando fuentes parcheadas hacia ${TARGET_HOST} ==="
rsync -avz --exclude="*.o" --exclude="*.ko*" --exclude="*.mod*" --exclude=".*.cmd" --exclude=".*.d" \
    "${LOCAL_SRC_DIR}/" "${TARGET_HOST}:brcmfmac-src/"

echo "=== Compilando módulo brcmfmac parcheado en ${TARGET_HOST} ==="
ssh -o BatchMode=yes "${TARGET_HOST}" bash << 'EOF'
set -euo pipefail
PATH="/usr/sbin:/sbin:$PATH"
KVER=$(uname -r)
cd ~/brcmfmac-src
make -C "/lib/modules/${KVER}/build" M="$HOME/brcmfmac-src" clean
make -C "/lib/modules/${KVER}/build" M="$HOME/brcmfmac-src" modules -j4
if [ -f "$HOME/brcmfmac-src/brcmfmac/brcmfmac.ko" ] && [ -f "$HOME/brcmfmac-src/brcmfmac/wcc/brcmfmac-wcc.ko" ]; then
    echo "BUILD_SUCCESSFUL"
else
    echo "BUILD_FAILED" >&2
    exit 1
fi
EOF

if [ "${ACTION}" = "--build-only" ]; then
    echo "=== Compilación completada (--build-only) ==="
    exit 0
fi

echo "=== Instalando conjunto de módulos brcm80211 parcheados en ${TARGET_HOST} ==="
ssh -o BatchMode=yes "${TARGET_HOST}" sudo bash << 'EOF'
set -euo pipefail
PATH="/usr/sbin:/sbin:$PATH"
KVER=$(uname -r)
BASE_DIR="/lib/modules/${KVER}/kernel/drivers/net/wireless/broadcom/brcm80211"
SRC_DIR="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)/brcmfmac-src"

MODS=(
    "brcmfmac/brcmfmac.ko"
    "brcmfmac/wcc/brcmfmac-wcc.ko"
    "brcmfmac/cyw/brcmfmac-cyw.ko"
    "brcmfmac/bca/brcmfmac-bca.ko"
    "brcmutil/brcmutil.ko"
)

# 1. Crear respaldos si no existen
for m in "${MODS[@]}"; do
    tgt="${BASE_DIR}/${m}.xz"
    orig="${tgt}.orig"
    if [ ! -f "${orig}" ] && [ -f "${tgt}" ]; then
        echo "Creando respaldo de ${tgt}..."
        cp -p "${tgt}" "${orig}"
    fi
done

# 2. Instalar módulos nuevos comprimidos con xz
for m in "${MODS[@]}"; do
    src="${SRC_DIR}/${m}"
    tgt="${BASE_DIR}/${m}.xz"
    if [ -f "${src}" ]; then
        echo "Instalando ${tgt}..."
        xz -T0 -c "${src}" > "${tgt}"
    fi
done

# 3. Actualizar dependencias de módulos
depmod -a

echo "Recargando módulos del driver..."
(modprobe -r brcmfmac_wcc brcmfmac_cyw brcmfmac_bca brcmfmac brcmutil || true)
modprobe brcmutil
modprobe brcmfmac
modprobe brcmfmac_wcc || true

# 4. Esperar a inicialización del chip
sleep 2
dmesg | grep -i brcmfmac | tail -n 10
EOF

echo "=== Conjunto de módulos brcmfmac instalado y recargado con éxito ==="
