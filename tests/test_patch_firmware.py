import os as _os
import pytest as _pytest
from pathlib import Path as _P


def _fw_bin():
    """Firmware original del CYW43455 (DRC_FW_BIN o /lib/firmware/cypress); si falta, se salta la prueba."""
    p = _P(_os.environ.get("DRC_FW_BIN", "/lib/firmware/cypress/cyfmac43455-sdio-standard.bin"))
    if not p.exists():
        _pytest.skip("falta el firmware original (DRC_FW_BIN)")
    return p


import pytest
from pathlib import Path
from patch_firmware import (
    load_and_verify_firmware,
    EXPECTED_SIZE,
    EXPECTED_SHA256,
    EXPECTED_VERSION,
)


def test_verify_rejects_invalid_size_or_hash(tmp_path: Path):
    truncated_file = tmp_path / "truncated.bin"
    truncated_file.write_bytes(b"short data")

    with pytest.raises(ValueError, match="Tamaño de firmware inválido|Invalid firmware size"):
        load_and_verify_firmware(truncated_file)

    corrupted_file = tmp_path / "corrupted.bin"
    # Create file with correct size but invalid content/hash/version
    corrupted_data = b"\x00" * EXPECTED_SIZE
    corrupted_file.write_bytes(corrupted_data)

    with pytest.raises(ValueError, match="Hash o versión inválidos|Invalid hash or version|SHA-256"):
        load_and_verify_firmware(corrupted_file)


def test_verify_accepts_stock_265_firmware():
    firmware_path = _fw_bin()
    assert firmware_path.exists(), f"El firmware original debe estar en {firmware_path}"

    buf = load_and_verify_firmware(firmware_path)
    assert isinstance(buf, bytearray)
    assert len(buf) == EXPECTED_SIZE


def test_patch_rsn_oui_replaces_both_tables():
    from patch_firmware import patch_rsn_oui, NINTENDO_OUI, WPA_OUI

    firmware_path = _fw_bin()
    buf = load_and_verify_firmware(firmware_path)

    # Initial state must be standard WPA OUI (00:0f:ac)
    assert buf[0x5A1E0:0x5A1E3] == WPA_OUI
    assert buf[0x5A259:0x5A25C] == WPA_OUI

    patch_rsn_oui(buf)

    assert len(buf) == EXPECTED_SIZE
    assert buf[0x5A1E0:0x5A1E3] == NINTENDO_OUI
    assert buf[0x5A259:0x5A25C] == NINTENDO_OUI

    # Idempotence: applying again should succeed without changes
    patch_rsn_oui(buf)
    assert buf[0x5A1E0:0x5A1E3] == NINTENDO_OUI
    assert buf[0x5A259:0x5A25C] == NINTENDO_OUI



def test_patch_rsn_oui_fails_if_unexpected_bytes():
    from patch_firmware import patch_rsn_oui

    # Buffer with wrong bytes at offset 0x5a1e0
    corrupted = bytearray(b"\x00" * EXPECTED_SIZE)
    with pytest.raises(ValueError, match="Offset .* no contiene OUI esperado"):
        patch_rsn_oui(corrupted)


def test_patch_tsf_iovar_updates_descriptor():
    from patch_firmware import patch_tsf_iovar
    import struct

    firmware_path = _fw_bin()
    buf = load_and_verify_firmware(firmware_path)

    patch_tsf_iovar(buf)

    # wlc_iovar descriptor at 0x5e00c: name(4), varid(2), flags(2), type(2), minlen(2)
    name_ptr, varid, flags, iov_type, minlen = struct.unpack_from("<IH2sHH", buf, 0x5E00C)
    assert varid == 283
    assert iov_type == 8  # IOVT_BUFFER
    assert minlen == 8


def test_patch_tsf_iovar_machine_code(tmp_path: Path):
    import subprocess
    from patch_firmware import (
        patch_tsf_iovar,
        TSF_BRANCH_OFFSET,
        TSF_BRANCH_BYTES,
        TSF_HOOK_OFFSET,
        TSF_HOOK_BYTES,
    )

    firmware_path = _fw_bin()
    buf = load_and_verify_firmware(firmware_path)

    patch_tsf_iovar(buf)

    assert buf[TSF_BRANCH_OFFSET : TSF_BRANCH_OFFSET + len(TSF_BRANCH_BYTES)] == TSF_BRANCH_BYTES
    assert buf[TSF_HOOK_OFFSET : TSF_HOOK_OFFSET + len(TSF_HOOK_BYTES)] == TSF_HOOK_BYTES

    # Verify disassembly with llvm-objdump
    hook_bin = tmp_path / "hook.bin"
    hook_bin.write_bytes(TSF_HOOK_BYTES)
    asm_src = (
        f".syntax unified\n.thumb\n.org 0x1b1802\n.incbin \"{hook_bin}\"\n"
    )
    asm_file = tmp_path / "hook.s"
    asm_file.write_text(asm_src)
    obj_file = tmp_path / "hook.o"

    subprocess.run(
        ["clang", "-target", "arm-none-eabi", "-march=armv7-r", "-c", str(asm_file), "-o", str(obj_file)],
        check=True,
    )
    dump_out = subprocess.check_output(
        ["llvm-objdump", "-d", "--triple=thumbv7-none-eabi", str(obj_file)],
        text=True,
    )
    # TSF vivo: regs = wlc->regs ([wlc+0xc]); tsf_timerlow/high en regs+0x180/0x184
    # (como el handler de TBTT en 0x1ba2d0). [wlc+0x898] es la copia del último beacon
    # (se actualiza cada 102,4 ms): solo se lee sin reloj (prueba 71) y 0x1c7e54 es el DPC de
    # interrupciones, que no se usa.
    assert "ldr\tr3, [r4, #0xc]" in dump_out
    assert "ldr.w\tr2, [r3, #0x180]" in dump_out
    assert "ldr.w\tr3, [r3, #0x184]" in dump_out
    assert dump_out.index("[r3, #0x180]") < dump_out.index("[r3, #0x184]")  # low antes que high
    assert "bl\t" not in dump_out
    assert "[r4, #0x898]" not in dump_out  # la copia se lee vía r4+0x718, solo sin reloj
    assert "str\tr2, [r5]" in dump_out
    assert "str\tr3, [r5, #0x4]" in dump_out or "str\tr3, [r5, #4]" in dump_out
    assert "mov.w\tr8, #0x0" in dump_out or "mov.w\tr8, #0" in dump_out
    assert "b\t0x1b1d34" in dump_out


def test_install_and_restore_cycle(tmp_path: Path):
    import hashlib
    from patch_firmware import (
        install_firmware,
        restore_firmware,
        NINTENDO_OUI,
        EXPECTED_SHA256,
        main,
    )

    stock_path = _fw_bin()
    stock_bytes = stock_path.read_bytes()

    target_fw = tmp_path / "cyfmac43455-sdio-standard.bin"
    target_fw.write_bytes(stock_bytes)
    orig_fw = tmp_path / "cyfmac43455-sdio-standard.bin.orig"

    # Install
    install_firmware(target_fw)
    assert orig_fw.exists()
    assert hashlib.sha256(orig_fw.read_bytes()).hexdigest() == EXPECTED_SHA256

    patched_bytes = target_fw.read_bytes()
    assert patched_bytes[0x5A1E0:0x5A1E3] == NINTENDO_OUI
    assert hashlib.sha256(patched_bytes).hexdigest() != EXPECTED_SHA256

    # Restore
    restore_firmware(target_fw)
    assert hashlib.sha256(target_fw.read_bytes()).hexdigest() == EXPECTED_SHA256

    # Verify via CLI interface
    main(["--install", "--target", str(target_fw)])
    assert target_fw.read_bytes()[0x5A1E0:0x5A1E3] == NINTENDO_OUI
    main(["--restore", "--target", str(target_fw)])
    assert hashlib.sha256(target_fw.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_live_reload_on_rpi4():
    import subprocess

    target_host = _os.environ.get("DRC_TEST_HOST", "user@host")
    script = Path(__file__).resolve().parent.parent / "tools" / "install_firmware.sh"

    # Ejecutar script de instalación y recarga
    res = subprocess.run([str(script), target_host], capture_output=True, text=True)
    assert res.returncode == 0, f"Error en instalación remota: {res.stderr}\n{res.stdout}"

    # Verificar que wlan0 está operativa (con reintentos por reconexión Wi-Fi)
    import time
    wlan_up = False
    wlan_out = ""
    for _ in range(15):
        try:
            wlan_out = subprocess.check_output(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", target_host, "ip link show dev wlan0"],
                text=True,
            )
            if "UP" in wlan_out:
                wlan_up = True
                break
        except Exception:
            pass
        time.sleep(1)
    assert wlan_up, f"wlan0 no levantó tras recargar brcmfmac: {wlan_out}"


    # Verificar que el firmware cargado es el 7.45.265
    dmesg_out = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", target_host, "sudo dmesg | grep -i 'Firmware: BCM4345' | tail -n 1"],
        text=True,
    )
    assert "7.45.265" in dmesg_out

    # Verificar respaldo .orig en la Pi
    sha_orig = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", target_host, "sha256sum /usr/lib/firmware/cypress/cyfmac43455-sdio-standard.bin.orig"],
        text=True,
    )
    assert EXPECTED_SHA256 in sha_orig







def test_service_stop_cycles_without_fw_trap_on_rpi4(drc_service_paused):
    """Prueba 70 (B18): 10 ciclos de arrancar la sesión sin GamePad, esperar 140 s y pararla con
    `systemctl stop` (unidad transitoria equivalente a drc.service) no provocan ningún
    `dongle trap info` / `epc 0x001b1806` nuevo en dmesg y wlan0 sigue existiendo al final.
    Exige el GamePad APAGADO. Unos 25 min: no se corre mientras el usuario hace pruebas de oído."""
    import subprocess
    import time

    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", _os.environ.get("DRC_TEST_HOST", "user@host"), "bash -s"]

    def remote(script: str, timeout: int) -> str:
        # Por stdin (`bash -s`): start_drc.sh hace `pkill -f` y mataría una shell de ssh cuyo
        # comando incluyera esos nombres.
        res = subprocess.run(ssh, input=script, capture_output=True, text=True, timeout=timeout)
        return res.stdout

    def traps() -> int:
        out = remote('sudo dmesg | grep -cE "dongle trap info|epc 0x001b1806"\n', 30)
        return int(out.strip() or 0)

    def wlan0_present() -> bool:
        return "WLAN0_YES" in remote('[ -e /sys/class/net/wlan0 ] && echo WLAN0_YES\n', 30)

    # Unidad transitoria con el mismo entorno que drc.service salvo DRC_TIMEOUT (con 60 s la
    # sesión se rinde sola sin GamePad) y parada con `systemctl stop`, como el servicio real.
    start = (
        'sudo systemctl reset-failed b18test 2>/dev/null\n'
        'sudo systemd-run --unit=b18test -p Environment=DRC_TIMEOUT=300'
        ' -p Environment=DRC_DESKTOP_USER=$USER /opt/drc/start_drc.sh --mode separate\n'
    )
    stop = (
        'sudo systemctl stop b18test\n'
        'systemctl is-active b18test | grep -qx active && echo STILL_RUNNING\n'
    )

    baseline = traps()
    for cycle in range(1, 11):
        remote(start, 30)
        time.sleep(140)
        out = remote(stop, 90)
        assert "STILL_RUNNING" not in out, f"ciclo {cycle}: la sesión no paró tras SIGTERM"
        nuevos = traps() - baseline
        if nuevos > 0 or not wlan0_present():
            # Esperar a que wlan0 vuelva antes de dar el fallo (o fallar si no vuelve).
            back = False
            for _ in range(60):
                if wlan0_present():
                    back = True
                    break
                time.sleep(2)
            assert False, (
                f"ciclo {cycle}: {nuevos} líneas de trap nuevas en dmesg (epc 0x001b1806 = hook del "
                f"TSF sin reloj); wlan0 {'volvió' if back else 'NO volvió'}"
            )
    assert wlan0_present()


def test_patch_tsf_hook_checks_clock(tmp_path: Path):
    """Prueba 71 (B18): el hook del TSF comprueba el indicador de reloj del wlc ([r4, #0x19],
    el mismo que mira el firmware antes de leer d11regs en 'clock is off failed to get time').
    Con reloj lee tsf_timerlow/high vivos ([regs+0x180/0x184], regs = [r4, #0xc]); sin reloj lee
    la copia del último beacon ([r4, #0x898/0x89c]) con la base r4+0x718, para caber en los 28
    bytes disponibles. Ambas ramas escriben en [r5]/[r5, #4], ponen r8 = 0 y acaban en b 0x1b1d34 sin bl."""
    import subprocess
    import re
    from patch_firmware import TSF_HOOK_BYTES

    assert len(TSF_HOOK_BYTES) == 28  # tras él (0x1b181e) empieza otro fragmento vivo
    hook_bin = tmp_path / "hook.bin"
    hook_bin.write_bytes(TSF_HOOK_BYTES)
    asm_file = tmp_path / "hook.s"
    asm_file.write_text(f".syntax unified\n.thumb\n.org 0x1b1802\n.incbin \"{hook_bin}\"\n")
    obj_file = tmp_path / "hook.o"
    subprocess.run(
        ["clang", "-target", "arm-none-eabi", "-march=armv7-r", "-c", str(asm_file), "-o", str(obj_file)],
        check=True,
    )
    dump = subprocess.check_output(
        ["llvm-objdump", "-d", "--triple=thumbv7-none-eabi", str(obj_file)], text=True
    )
    insns = [
        l.split("\t", 1)[1].strip()
        for l in dump.splitlines()
        if (m := re.match(r"\s+([0-9a-f]+):", l)) and int(m.group(1), 16) >= 0x1B1802
    ]
    text = "\n".join(insns)

    # Indicador de reloj y salto según su valor
    assert "ldrb\tr2, [r4, #0x19]" in text
    cbz = next(i for i, l in enumerate(insns) if l.startswith("cbz\tr2,"))
    assert insns.index("ldrb\tr2, [r4, #0x19]") < cbz
    # Con reloj: registros vivos
    assert "ldr\tr3, [r4, #0xc]" in text
    # Sin reloj: la copia del beacon, [r4, #0x898/0x89c] = (r4 + 0x718) + 0x180/0x184
    base = re.search(r"addw\tr3, r4, #(0x[0-9a-f]+)", text)
    assert base, text
    assert int(base.group(1), 16) + 0x180 == 0x898
    assert int(base.group(1), 16) + 0x184 == 0x89C
    # Ambas ramas convergen en la misma lectura, low antes que high
    assert "ldr.w\tr2, [r3, #0x180]" in text and "ldr.w\tr3, [r3, #0x184]" in text
    assert text.index("[r3, #0x180]") < text.index("[r3, #0x184]")
    assert "str\tr2, [r5]" in text
    assert "str\tr3, [r5, #0x4]" in text or "str\tr3, [r5, #4]" in text
    assert "mov.w\tr8, #0x0" in text or "mov.w\tr8, #0" in text
    assert insns[-1] == "b\t0x1b1d34" or insns[-1].startswith("b\t0x1b1d34"), insns[-1]
    assert text.count("b\t0x1b1d34") == 1
    assert "bl\t" not in text
