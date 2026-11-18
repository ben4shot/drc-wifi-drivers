import hashlib
from pathlib import Path
from typing import Union

EXPECTED_SIZE = 609309
EXPECTED_SHA256 = "d608f866582519c0a28d86db43040f4f1b98dd1d153e72e9752586546b4a36c3"
EXPECTED_VERSION = "7.45.265"

WPA_OUI = bytes([0x00, 0x0F, 0xAC])
NINTENDO_OUI = bytes([0xA4, 0xC0, 0xE1])
RSN_OUI_OFFSETS = (0x5A1E0, 0x5A259)

TSF_IOVAR_OFFSET = 0x5E00C
TSF_BRANCH_OFFSET = 0x18C4C
TSF_BRANCH_BYTES = bytes([0x00, 0xF0, 0xD9, 0x85])  # beq.w 0x1b1802
TSF_HOOK_OFFSET = 0x19802
TSF_HOOK_BYTES = bytes([
    # r3 = wlc + 0x718: con la base de r3 + 0x180/0x184 da [wlc+0x898/0x89c], la copia del
    # último beacon (lo que leía el hook antes de B07).
    0x04, 0xF2, 0x18, 0x73,  # addw  r3, r4, #0x718
    0x62, 0x7E,              # ldrb  r2, [r4, #0x19]  (wlc->clk: ¿la radio tiene reloj?)
    0x02, 0xB1,              # cbz   r2, +0           (sin reloj: se queda con la copia)
    0xE3, 0x68,              # ldr   r3, [r4, #0xc]   (con reloj: wlc->regs, d11regs)
    0xD3, 0xF8, 0x80, 0x21,  # ldr.w r2, [r3, #0x180] (tsf_timerlow)
    0xD3, 0xF8, 0x84, 0x31,  # ldr.w r3, [r3, #0x184] (tsf_timerhigh)
    0x2A, 0x60,              # str   r2, [r5, #0]
    0x6B, 0x60,              # str   r3, [r5, #4]
    0x4F, 0xF0, 0x00, 0x08,  # mov.w r8, #0           (success status)
    0x8A, 0xE2,              # b     0x1b1d34
])  # 28 bytes: justo después (0x1b181e) empieza otro fragmento de código vivo


def patch_tsf_iovar(buf: bytearray) -> None:
    """Modifica la entrada wlc_iovars de bcn_proc_deferred (varid=283) a IOVT_BUFFER (len=8),

    redirige IOV_GVAL(283) en 0x18c4c al hook en 0x19802, e inyecta la lectura del TSF: los
    registros D11 tsf_timerlow/high vivos (wlc->regs + 0x180/0x184) si la radio tiene reloj
    (wlc->clk), y si no, la copia del último beacon ([wlc+0x898/0x89c]) sin tocar los registros.
    """
    import struct

    if len(buf) < max(TSF_IOVAR_OFFSET + 12, TSF_HOOK_OFFSET + len(TSF_HOOK_BYTES)):
        raise ValueError("Buffer demasiado pequeño para aplicar el parche TSF")

    # Verificar descriptor wlc_iovar en 0x5e00c
    name_ptr, varid = struct.unpack_from("<IH", buf, TSF_IOVAR_OFFSET)
    if varid != 283:
        raise ValueError(f"Descriptor en {hex(TSF_IOVAR_OFFSET)} esperado varid=283, encontrado {varid}")

    # type=8 (IOVT_BUFFER), minlen=8
    struct.pack_into("<HH", buf, TSF_IOVAR_OFFSET + 8, 8, 8)

    # Redirigir salto IOV_GVAL(283) a 0x1b1802
    buf[TSF_BRANCH_OFFSET : TSF_BRANCH_OFFSET + len(TSF_BRANCH_BYTES)] = TSF_BRANCH_BYTES

    # Inyectar código máquina del hook Thumb-2
    buf[TSF_HOOK_OFFSET : TSF_HOOK_OFFSET + len(TSF_HOOK_BYTES)] = TSF_HOOK_BYTES


def apply_all_patches(buf: bytearray) -> bytearray:
    """Aplica todos los parches (OUI RSN y TSF IOVAR) sobre el buffer del firmware."""
    patch_rsn_oui(buf)
    patch_tsf_iovar(buf)
    return buf


DEFAULT_TARGET_PATH = Path("/usr/lib/firmware/cypress/cyfmac43455-sdio-standard.bin")


def install_firmware(
    target_path: Union[str, Path] = DEFAULT_TARGET_PATH,
    source_path: Union[str, Path, None] = None,
) -> None:
    """Respalda el firmware original en .orig (si no existe) y escribe el firmware parcheado."""
    import shutil

    target = Path(target_path)
    orig = target.with_name(target.name + ".orig")

    if not orig.exists():
        if target.exists():
            shutil.copy2(target, orig)
        elif source_path:
            shutil.copy2(source_path, orig)
        else:
            raise FileNotFoundError(f"No se encontró firmware objetivo ni fuente para respaldar en {orig}")

    base_src = orig if orig.exists() else (Path(source_path) if source_path else target)
    data = base_src.read_bytes()
    buf = bytearray(data)
    apply_all_patches(buf)

    target.write_bytes(buf)


def restore_firmware(target_path: Union[str, Path] = DEFAULT_TARGET_PATH) -> None:
    """Restaura el firmware original desde el archivo de respaldo .orig."""
    import shutil

    target = Path(target_path)
    orig = target.with_name(target.name + ".orig")
    if not orig.exists():
        raise FileNotFoundError(f"No existe copia de seguridad para restaurar en {orig}")

    shutil.copy2(orig, target)


def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Parcheador de firmware Wi-Fi CYW43455 para Wii U DRC")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="Instalar firmware parcheado con respaldo .orig")
    group.add_argument("--restore", action="store_true", help="Restaurar firmware original desde .orig")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET_PATH, help="Ruta del firmware en el sistema")
    parser.add_argument("--source", type=Path, default=None, help="Ruta alternativa de firmware base")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.install:
        install_firmware(target_path=args.target, source_path=args.source)
        print(f"Firmware parcheado instalado en {args.target}")
    elif args.restore:
        restore_firmware(target_path=args.target)
        print(f"Firmware restaurado en {args.target}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())




def patch_rsn_oui(buf: bytearray) -> None:
    """Parchea el OUI estándar de WPA (00:0f:ac) con el OUI de Nintendo (a4:c0:e1)

    en las tablas RSN del firmware (offsets 0x5a1e0 y 0x5a259).
    """
    if len(buf) < max(RSN_OUI_OFFSETS) + 3:
        raise ValueError("Buffer demasiado pequeño para contener las tablas RSN")

    for offset in RSN_OUI_OFFSETS:
        current = bytes(buf[offset : offset + 3])
        if current == WPA_OUI:
            buf[offset : offset + 3] = NINTENDO_OUI
        elif current == NINTENDO_OUI:
            pass  # Ya está parcheado
        else:
            raise ValueError(
                f"Offset {hex(offset)} no contiene OUI esperado (encontrado: {current.hex()})"
            )



def load_and_verify_firmware(path: Union[str, Path]) -> bytearray:
    """Lee el binario del firmware cyfmac43455-sdio-standard.bin y valida su integridad."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Archivo de firmware no encontrado: {path}")

    data = p.read_bytes()
    if len(data) != EXPECTED_SIZE:
        raise ValueError(
            f"Tamaño de firmware inválido: se esperaban {EXPECTED_SIZE} bytes, obtenidos {len(data)}"
        )

    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != EXPECTED_SHA256:
        raise ValueError(
            f"SHA-256 inválido: se esperaba {EXPECTED_SHA256}, obtenido {sha256}"
        )

    # El trailer de Cypress contiene la versión como cadena de texto
    if EXPECTED_VERSION.encode("latin1") not in data[-2048:]:
        raise ValueError(
            f"Versión de firmware no encontrada en el trailer: {EXPECTED_VERSION}"
        )

    return bytearray(data)
