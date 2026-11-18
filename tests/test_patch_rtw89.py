"""Parche del driver rtw89 (TP-Link 8852AU) para que sea AP del GamePad: /sys/class/net/<if>/tsf."""
import subprocess
from pathlib import Path

import patch_rtw89

FUENTE = Path(__file__).resolve().parent.parent / "third_party" / "rtw89" / "mac80211.c"
import pytest
pytestmark = pytest.mark.skipif(not FUENTE.exists(), reason="falta third_party/rtw89 (lo descarga tools/install_rtw89.sh)")


def test_patch_agrega_tsf_sysfs_y_es_idempotente():
    """Prueba 82: tsf_show lee el TSF del puerto del vif (8 bytes crudos como brcmfmac); se crea al
    añadir la interfaz, se borra al quitarla, y aplicar el parche dos veces no cambia nada."""
    original = FUENTE.read_text()
    p1 = patch_rtw89.patch_mac80211(original)
    assert "static DEVICE_ATTR_RO(tsf);" in p1
    assert "rtw89_mac_port_get_tsf(" in p1
    assert p1.count("device_create_file(&wdev->netdev->dev, &dev_attr_tsf)") == 1
    assert p1.count("device_remove_file(&wdev->netdev->dev, &dev_attr_tsf)") == 1
    assert patch_rtw89.patch_mac80211(p1) == p1


def test_tsf_show_no_espera_el_bloqueo_del_wiphy():
    """Prueba 83: remove_interface borra el atributo con el bloqueo del wiphy tomado y device_remove_file
    espera a los lectores; si tsf_show esperase ese bloqueo, interbloqueo (colgó la Pi el 2026-11-02).
    Debe usar mutex_trylock y devolver -EAGAIN, nunca wiphy_lock."""
    p1 = patch_rtw89.patch_mac80211(FUENTE.read_text())
    cuerpo = p1.split("static ssize_t tsf_show", 1)[1].split("static DEVICE_ATTR_RO(tsf);", 1)[0]
    assert "mutex_trylock(&hw->wiphy->mtx)" in cuerpo
    assert "-EAGAIN" in cuerpo
    assert "wiphy_lock(" not in cuerpo
