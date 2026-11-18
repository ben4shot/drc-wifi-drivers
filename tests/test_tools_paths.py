"""Los scripts de tools/ deben funcionar desde un clon del repo: sin módulos ni carpetas del árbol antiguo (rpi4/)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def test_los_modulos_que_importan_los_scripts_estan_en_tools():
    for sh in TOOLS.glob("*.sh"):
        for mod in re.findall(r"^\s*from ([\w.]+) import", sh.read_text(), re.M):
            if mod in ("pathlib",):
                continue
            assert (TOOLS / (mod.replace(".", "/") + ".py")).exists(), f"{sh.name} importa {mod}, que no está en tools/"


def test_las_fuentes_de_brcmfmac_por_defecto_existen():
    for sh in ("install_brcmfmac.sh", "prepare_brcmfmac_src.sh"):
        texto = (TOOLS / sh).read_text()
        assert "${SCRIPT_DIR}/../brcmfmac-src" in texto, f"{sh} no usa brcmfmac-src/ por defecto"
    assert (ROOT / "brcmfmac-src" / "brcmfmac" / "cfg80211.c").exists()
