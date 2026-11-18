# drc-wifi-drivers

Parches de driver Wi-Fi para usar un adaptador como AP del Wii U GamePad (necesita el TSF del chip). Se usa junto al
repo principal `drc`.

- `tools/patch_rtw89.py` + `tools/install_rtw89.sh`: rtw89 USB (TP-Link Archer TX20U Plus, RTL8852AU; fork de morrownr) con
  `/sys/class/net/<if>/tsf`. Repetir tras cada actualización del kernel.
- `tools/patch_brcmfmac.py`, `prepare_brcmfmac_src.sh`, `install_brcmfmac.sh`, `patch_firmware.py`,
  `install_firmware.sh`, `wifi_driver.sh`: chip interno de la Raspberry Pi 4 (CYW43455), módulo externo y firmware.
- `brcmfmac-src/`: fuentes de brcmfmac/brcmutil usadas para el módulo externo.
- `tools/fw_tsf_probe.py`, `wliov.py`: sondas del TSF por iovar del firmware.
- Los perfiles de cada adaptador (payload, `TS_OFFSET_US`, etc.) están en el repo `drc`, en `config/platforms/`.

## Requisitos (antes de instalar nada)

| | Arch | Debian / Ubuntu / Raspberry Pi OS |
|---|---|---|
| Compilar módulos | `base-devel git dkms` | `build-essential git dkms` |
| Cabeceras del kernel en uso | `linux-headers` | `linux-headers-$(uname -r)` |
| Python 3 | `python` | `python3` |
| Herramientas Wi-Fi | `iw` | `iw` |
| Desplegar a otra máquina por SSH | `openssh rsync` | `openssh-client rsync` |
| Fuentes del kernel (brcmfmac) | | `linux-source-6.12` (lo instala `prepare_brcmfmac_src.sh`) |

Cada parche tiene sus pruebas en `tests/`. Los drivers conservan la licencia GPL-2 del kernel/morrownr.
