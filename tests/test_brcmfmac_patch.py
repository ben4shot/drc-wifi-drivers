import os as _os
import subprocess
from pathlib import Path
import pytest

TARGET_HOST = _os.environ.get("DRC_TEST_HOST", "user@host")
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "tools" / "prepare_brcmfmac_src.sh"
LOCAL_SRC_DIR = ROOT / "brcmfmac-src"


def test_prepare_extracts_brcmfmac_sources_on_rpi4(tmp_path):
    """Prueba 9: Verifica que el script deja cfg80211.c, core.c, core.h y Makefile listos en el directorio de compilación.
    Trabaja en una copia temporal: el script no debe pisar las fuentes parcheadas de brcmfmac-src/ (B18)."""
    assert SCRIPT_PATH.exists(), f"Script no encontrado: {SCRIPT_PATH}"
    import hashlib
    parcheados = [LOCAL_SRC_DIR / "brcmfmac" / f for f in ("cfg80211.c", "core.c", "core.h")]
    antes = [hashlib.sha256(f.read_bytes()).hexdigest() for f in parcheados]
    local_src = tmp_path / "src"

    # Ejecutar script de preparación
    res = subprocess.run([str(SCRIPT_PATH), TARGET_HOST, str(local_src)], capture_output=True, text=True)
    assert res.returncode == 0, f"prepare_brcmfmac_src.sh falló:\n{res.stderr}\n{res.stdout}"
    assert antes == [hashlib.sha256(f.read_bytes()).hexdigest() for f in parcheados], \
        "prepare_brcmfmac_src.sh pisó las fuentes parcheadas de brcmfmac-src/"

    # Verificar archivos en la copia local
    brcmfmac_dir = local_src / "brcmfmac"
    assert brcmfmac_dir.exists(), f"Directorio local no existe: {brcmfmac_dir}"
    for filename in ["cfg80211.c", "core.c", "core.h", "Makefile"]:
        file_path = brcmfmac_dir / filename
        assert file_path.exists(), f"Archivo local faltante: {file_path}"
        assert file_path.stat().st_size > 0, f"Archivo vacío: {file_path}"
    
    # Verificar que los archivos existen en la Raspberry Pi 4
    remote_check = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", TARGET_HOST, "ls -1 ~/brcmfmac-src/brcmfmac"],
        text=True
    )
    for filename in ["cfg80211.c", "core.c", "core.h", "Makefile"]:
        assert filename in remote_check, f"Archivo remoto faltante en RPi4: {filename}"


def test_unpatched_brcmfmac_builds_on_rpi4():
    """Prueba 10: Ejecuta make out-of-tree en la Raspberry Pi 4B y comprueba que genera brcmfmac.ko."""
    build_cmd = (
        "make -C /lib/modules/$(uname -r)/build M=$HOME/brcmfmac-src clean && "
        "make -C /lib/modules/$(uname -r)/build M=$HOME/brcmfmac-src modules -j4 && "
        "test -f $HOME/brcmfmac-src/brcmfmac/brcmfmac.ko && "
        "ls -l $HOME/brcmfmac-src/brcmfmac/brcmfmac.ko"
    )
    res = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", TARGET_HOST, build_cmd],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"Compilación remota de brcmfmac.ko falló:\n{res.stderr}\n{res.stdout}"
    assert "brcmfmac.ko" in res.stdout


def test_valid_wpa_oui_accepts_nintendo_and_rejects_invalid(tmp_path):
    """Prueba 11: Compila y ejecuta una prueba en C sobre brcmf_valid_wpa_oui()
    verificando que devuelve true para Nintendo OUI (\xa4\xc0\xe1), RSN_OUI y WPA_OUI,
    y false para OUIs arbitrarios."""
    from patch_brcmfmac import get_wpa_oui_c_snippet

    c_source = get_wpa_oui_c_snippet()
    test_c_file = tmp_path / "test_oui.c"
    test_bin = tmp_path / "test_oui"
    test_c_file.write_text(c_source)

    compile_res = subprocess.run(
        ["gcc", "-O2", str(test_c_file), "-o", str(test_bin)],
        capture_output=True,
        text=True
    )
    assert compile_res.returncode == 0, f"Error compilando test_oui.c: {compile_res.stderr}"

    run_res = subprocess.run([str(test_bin)], capture_output=True, text=True)
    assert run_res.returncode == 0, f"Falló ejecución de test_oui: {run_res.stderr}\n{run_res.stdout}"
    assert "ALL_TESTS_PASSED" in run_res.stdout


def test_oui_patch_applies_to_kernel_6_12_cfg80211():
    """Prueba 12: Verifica que la modificación aplica limpiamente sobre cfg80211.c
    del kernel 6.12 y contiene la definición y validación del OUI de Nintendo."""
    from patch_brcmfmac import patch_cfg80211_oui

    cfg_file = LOCAL_SRC_DIR / "brcmfmac" / "cfg80211.c"
    assert cfg_file.exists(), f"No se encontró {cfg_file}"
    original_code = cfg_file.read_text(encoding="utf-8", errors="replace")

    patched_code = patch_cfg80211_oui(original_code)
    assert "#define NINTENDO_OUI" in patched_code
    assert r'"\xa4\xc0\xe1"' in patched_code.lower() or r'"\xA4\xC0\xE1"' in patched_code
    assert "brcmf_valid_wpa_oui" in patched_code
    assert "NINTENDO_OUI" in patched_code


def test_tsf_cache_and_interpolation(tmp_path):
    """Prueba 13: Prueba en C que simula lecturas sucesivas verificando que dentro
    de la ventana de 100 ms no vuelve a invocar el iovar del firmware y suma
    (now_ns - sync_ns) / 1000, y que pasado 100 ms resincroniza con el firmware."""
    from patch_brcmfmac import get_tsf_c_snippet

    c_source = get_tsf_c_snippet()
    test_c_file = tmp_path / "test_tsf.c"
    test_bin = tmp_path / "test_tsf"
    test_c_file.write_text(c_source)

    compile_res = subprocess.run(
        ["gcc", "-O2", str(test_c_file), "-o", str(test_bin)],
        capture_output=True,
        text=True
    )
    assert compile_res.returncode == 0, f"Error compilando test_tsf.c: {compile_res.stderr}"

    run_res = subprocess.run([str(test_bin)], capture_output=True, text=True)
    assert run_res.returncode == 0, f"Falló ejecución de test_tsf: {run_res.stderr}\n{run_res.stdout}"
    assert "TSF_TESTS_PASSED" in run_res.stdout


def test_tsf_show_writes_8_bytes_little_endian():
    """Prueba 14: Verifica que las modificaciones aplican a core.h y core.c,
    que se registra el atributo sysfs /sys/class/net/<iface>/tsf y que la función
    tsf_show escribe exactamente 8 bytes binarios (u64) en el buffer."""
    from patch_brcmfmac import patch_core_h, patch_core_c

    core_h = LOCAL_SRC_DIR / "brcmfmac" / "core.h"
    core_c = LOCAL_SRC_DIR / "brcmfmac" / "core.c"
    assert core_h.exists() and core_c.exists()

    h_content = core_h.read_text(encoding="utf-8", errors="replace")
    c_content = core_c.read_text(encoding="utf-8", errors="replace")

    patched_h = patch_core_h(h_content)
    assert "tsf_base_us" in patched_h
    assert "tsf_sync_ns" in patched_h

    patched_c = patch_core_c(c_content)
    assert "tsf_show" in patched_c
    assert "bcn_proc_deferred" in patched_c
    assert "device_create_file" in patched_c
    assert "device_remove_file" in patched_c
    assert "sizeof(u64)" in patched_c


def test_core_c_allows_mtu_1756():
    """Prueba 75 (B17): core.c fija ndev->max_mtu = 1756 en brcmf_net_attach (como el
    `mtu 1756` del pc2drc original), para que los vstrm de 1694 B no se fragmenten en IP.
    El parche es idempotente y se aplica también sobre fuentes ya parcheadas."""
    from patch_brcmfmac import patch_core_c_mtu, apply_all_driver_patches  # noqa: F401

    c_content = (LOCAL_SRC_DIR / "brcmfmac" / "core.c").read_text(encoding="utf-8", errors="replace")
    patched = patch_core_c_mtu(c_content)
    attach = patched[patched.index("int brcmf_net_attach("):]
    attach = attach[:attach.index("\n}\n")]
    assert "ndev->max_mtu = 1756;" in attach
    assert patch_core_c_mtu(patched) == patched
    assert patched.count("ndev->max_mtu = 1756;") == 1


def test_patched_brcmfmac_builds_cleanly_on_rpi4():
    """Prueba 15: Aplica los parches en la Raspberry Pi 4B y compila el módulo brcmfmac.ko
    parcheado sin errores."""
    install_script = Path(__file__).parent / "install_brcmfmac.sh"
    assert install_script.exists(), f"No se encontró el script {install_script}"

    # Ejecutar con --build-only para solo compilar
    res = subprocess.run([str(install_script), "--build-only", TARGET_HOST], capture_output=True, text=True)
    assert res.returncode == 0, f"Compilación del módulo parcheado falló:\n{res.stderr}\n{res.stdout}"
    assert "BUILD_SUCCESSFUL" in res.stdout


def test_live_tsf_sysfs_on_rpi4():
    """Prueba 16: Instala el módulo brcmfmac parcheado en la Pi, recarga el driver,
    comprueba que wlan0 permanece operativa y realiza dos lecturas binarias de 8 bytes
    en /sys/class/net/wlan0/tsf separadas por 20 ms verificando que avanza ~20000 µs."""
    import time
    import struct

    install_script = Path(__file__).parent / "install_brcmfmac.sh"
    assert install_script.exists()

    # Instalar y recargar módulo
    res = subprocess.run([str(install_script), "--install", TARGET_HOST], capture_output=True, text=True)
    assert res.returncode == 0, f"Instalación remota de brcmfmac falló:\n{res.stderr}\n{res.stdout}"

    # Verificar que wlan0 está operativa
    wlan_up = False
    for _ in range(15):
        try:
            out = subprocess.check_output(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", TARGET_HOST, "ip link show dev wlan0"],
                text=True,
            )
            if "UP" in out:
                wlan_up = True
                break
        except Exception:
            pass
        time.sleep(1)
    assert wlan_up, "wlan0 no levantó tras instalar brcmfmac parcheado"

    # Verificar existencia de /sys/class/net/wlan0/tsf
    check_tsf = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", TARGET_HOST, "test -r /sys/class/net/wlan0/tsf"],
        capture_output=True
    )
    assert check_tsf.returncode == 0, "/sys/class/net/wlan0/tsf no existe o no se puede leer"

    # Leer dos muestras binarias de 8 bytes separadas por 20 ms (20,000 us)
    read_cmd = (
        "python3 -c '"
        "import time, sys\n"
        "with open(\"/sys/class/net/wlan0/tsf\", \"rb\") as f:\n"
        "    t1 = f.read(8)\n"
        "time.sleep(0.02)\n"
        "with open(\"/sys/class/net/wlan0/tsf\", \"rb\") as f:\n"
        "    t2 = f.read(8)\n"
        "sys.stdout.buffer.write(t1 + t2)\n"
        "'"
    )
    raw_data = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", TARGET_HOST, read_cmd]
    )
    assert len(raw_data) == 16, f"Se esperaban 16 bytes (dos lecturas de 8 bytes), se recibieron {len(raw_data)}"

    tsf1, tsf2 = struct.unpack("<QQ", raw_data)
    delta_us = tsf2 - tsf1
    # Con un sleep de 20 ms, el avance esperado es alrededor de 20,000 us (+ jitter del sistema ~15,000 a 45,000 us)
    assert tsf1 > 0, f"TSF inicial debe ser > 0, obtenido: {tsf1}"
    assert 15000 <= delta_us <= 50000, f"delta_us={delta_us} no está en rango esperado (~20000 µs tras sleep 20ms)"




