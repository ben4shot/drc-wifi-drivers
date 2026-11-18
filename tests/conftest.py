"""Configuración de pytest.

Las pruebas cuyo nombre termina en `_on_rpi4` son de hardware: se conectan por ssh a una Raspberry Pi
(DRC_TEST_HOST=usuario@host) y se saltan si no está definida. El resto corre en cualquier equipo.
"""
import os
import subprocess

import pytest

TARGET_HOST = os.environ.get("DRC_TEST_HOST", "")


def pytest_collection_modifyitems(config, items):
    if TARGET_HOST:
        return
    skip = pytest.mark.skip(reason="prueba de hardware: define DRC_TEST_HOST=usuario@host de una Raspberry Pi")
    for item in items:
        if item.name.endswith("_on_rpi4") or "on_rpi4" in item.name:
            item.add_marker(skip)


def _ssh(cmd: str) -> str:
    res = subprocess.run(["ssh", "-o", "BatchMode=yes", TARGET_HOST, cmd], capture_output=True, text=True, timeout=60)
    return res.stdout.strip()


@pytest.fixture
def drc_service_paused():
    """Para el servicio `drc` de la Pi mientras corre una prueba de desarrollo y lo devuelve a su estado anterior."""
    was_active = _ssh("systemctl is-active drc") == "active"
    _ssh("sudo systemctl stop drc")
    try:
        yield
    finally:
        if was_active:
            _ssh("sudo systemctl start drc")
