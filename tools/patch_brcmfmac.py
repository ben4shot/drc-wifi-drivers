"""
patch_brcmfmac.py - Herramientas para aplicar parches al driver de kernel brcmfmac.
Incluye soporte para el OUI de Nintendo (a4:c0:e1) y exposición de TSF en sysfs.
"""
import re
from pathlib import Path


def get_wpa_oui_c_snippet() -> str:
    """Retorna un programa en C autocontenido para probar la validación de OUI."""
    return r"""#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>

typedef uint8_t u8;

#define TLV_OUI_LEN     3
#define WPA_OUI         "\x00\x50\xF2"
#define RSN_OUI         "\x00\x0F\xAC"
#define NINTENDO_OUI    "\xA4\xC0\xE1"

static bool brcmf_valid_wpa_oui(const u8 *oui, bool is_rsn_ie)
{
    if (memcmp(oui, NINTENDO_OUI, TLV_OUI_LEN) == 0)
        return true;

    if (is_rsn_ie)
        return (memcmp(oui, RSN_OUI, TLV_OUI_LEN) == 0);

    return (memcmp(oui, WPA_OUI, TLV_OUI_LEN) == 0);
}

int main(void)
{
    assert(brcmf_valid_wpa_oui((const u8 *)RSN_OUI, true) == true);
    assert(brcmf_valid_wpa_oui((const u8 *)WPA_OUI, false) == true);
    assert(brcmf_valid_wpa_oui((const u8 *)NINTENDO_OUI, true) == true);
    assert(brcmf_valid_wpa_oui((const u8 *)NINTENDO_OUI, false) == true);
    assert(brcmf_valid_wpa_oui((const u8 *)"\x11\x22\x33", true) == false);
    assert(brcmf_valid_wpa_oui((const u8 *)"\x11\x22\x33", false) == false);
    assert(brcmf_valid_wpa_oui((const u8 *)"\x00\x00\x00", true) == false);

    printf("ALL_TESTS_PASSED\n");
    return 0;
}
"""


def patch_cfg80211_oui(content: str) -> str:
    """Modifica cfg80211.c para aceptar el OUI de Nintendo en brcmf_valid_wpa_oui."""
    if "#define NINTENDO_OUI" in content:
        return content

    # 1. Inyectar definición de NINTENDO_OUI tras RSN_OUI
    pattern_rsn = re.compile(r'(#define\s+RSN_OUI\s+"\\x00\\x0F\\xAC"[^\n]*)')
    if not pattern_rsn.search(content):
        raise ValueError("No se encontró definición de RSN_OUI en cfg80211.c")

    replacement_def = r'\1\n#define NINTENDO_OUI\t\t\t"\\xa4\\xc0\\xe1"\t/* Nintendo OUI */'
    content = pattern_rsn.sub(replacement_def, content, count=1)

    # 2. Modificar brcmf_valid_wpa_oui para validar primero NINTENDO_OUI
    pattern_fn = re.compile(
        r'(static\s+bool\s+brcmf_valid_wpa_oui\s*\([^)]*\)\s*\{)(\s*)(if\s*\(is_rsn_ie\))'
    )
    if not pattern_fn.search(content):
        raise ValueError("No se encontró brcmf_valid_wpa_oui() para parchear en cfg80211.c")

    replacement_fn = (
        r'\1\2if (memcmp(oui, NINTENDO_OUI, TLV_OUI_LEN) == 0)\n'
        r'\2\treturn true;\n\n'
        r'\2\3'
    )
    content = pattern_fn.sub(replacement_fn, content, count=1)

    return content


def apply_cfg80211_patch(file_path: Path) -> bool:
    """Aplica el parche de OUI directamente sobre el archivo cfg80211.c."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    if "#define NINTENDO_OUI" in content:
        return False
    patched = patch_cfg80211_oui(content)
    file_path.write_text(patched, encoding="utf-8")
    return True


def get_tsf_c_snippet() -> str:
    """Retorna un programa en C autocontenido para probar la lógica de caché, interpolación y formato binario del TSF."""
    return r"""#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>

typedef uint64_t u64;
typedef uint32_t u32;
typedef int32_t s32;
typedef long ssize_t;

struct mock_brcmf_if {
    u64 tsf_base_us;
    u64 tsf_sync_ns;
};

static int mock_iovar_calls = 0;
static u64 mock_fw_tsf = 1000000ULL;

static s32 mock_brcmf_fil_iovar_data_get(struct mock_brcmf_if *ifp, const char *name, void *data, u32 len)
{
    (void)ifp;
    (void)name;
    assert(len == sizeof(u64));
    mock_iovar_calls++;
    memcpy(data, &mock_fw_tsf, sizeof(u64));
    return 0;
}

static ssize_t mock_tsf_read(struct mock_brcmf_if *ifp, u64 now_ns, char *buf)
{
    u64 current_tsf;
    s32 err;

    if (ifp->tsf_sync_ns == 0 || (now_ns - ifp->tsf_sync_ns) >= 100000000ULL) {
        u64 fw_tsf = 0;
        err = mock_brcmf_fil_iovar_data_get(ifp, "bcn_proc_deferred", &fw_tsf, sizeof(fw_tsf));
        if (err == 0 && fw_tsf != 0) {
            ifp->tsf_base_us = fw_tsf;
            ifp->tsf_sync_ns = now_ns;
            current_tsf = fw_tsf;
        } else if (ifp->tsf_sync_ns != 0) {
            u64 elapsed_us = (now_ns - ifp->tsf_sync_ns) / 1000ULL;
            current_tsf = ifp->tsf_base_us + elapsed_us;
        } else if (err == 0) {
            /* Firmware returned 0 (e.g. idle/unassociated) -> seed from host clock */
            ifp->tsf_base_us = now_ns / 1000ULL;
            ifp->tsf_sync_ns = now_ns;
            current_tsf = ifp->tsf_base_us;
        } else {
            return err;
        }
    } else {
        u64 elapsed_us = (now_ns - ifp->tsf_sync_ns) / 1000ULL;
        current_tsf = ifp->tsf_base_us + elapsed_us;
    }

    memcpy(buf, &current_tsf, sizeof(u64));
    return sizeof(u64);
}

int main(void)
{
    struct mock_brcmf_if ifp = {0, 0};
    char buf[16];
    ssize_t ret;
    u64 val;

    /* 1. Primera lectura a t = 1,000,000,000 ns (1 s) -> consulta firmware */
    mock_fw_tsf = 5000000ULL;
    ret = mock_tsf_read(&ifp, 1000000000ULL, buf);
    assert(ret == 8);
    assert(mock_iovar_calls == 1);
    memcpy(&val, buf, 8);
    assert(val == 5000000ULL);

    /* 2. Segunda lectura a t = 1,020,000,000 ns (+20 ms) -> interpolar sin consultar firmware */
    ret = mock_tsf_read(&ifp, 1020000000ULL, buf);
    assert(ret == 8);
    assert(mock_iovar_calls == 1);
    memcpy(&val, buf, 8);
    assert(val == 5020000ULL);

    /* 3. Tercera lectura a t = 1,099,000,000 ns (+99 ms) -> interpolar sin consultar firmware */
    ret = mock_tsf_read(&ifp, 1099000000ULL, buf);
    assert(ret == 8);
    assert(mock_iovar_calls == 1);
    memcpy(&val, buf, 8);
    assert(val == 5099000ULL);

    /* 4. Cuarta lectura a t = 1,101,000,000 ns (+101 ms) -> resincronizar firmware */
    mock_fw_tsf = 5101075ULL;
    ret = mock_tsf_read(&ifp, 1101000000ULL, buf);
    assert(ret == 8);
    assert(mock_iovar_calls == 2);
    memcpy(&val, buf, 8);
    assert(val == 5101075ULL);

    /* 5. Lectura cuando el firmware retorna 0 (idle/unassociated) -> seed con reloj */
    struct mock_brcmf_if ifp_idle = {0, 0};
    mock_fw_tsf = 0;
    ret = mock_tsf_read(&ifp_idle, 2000000000ULL, buf);
    assert(ret == 8);
    memcpy(&val, buf, 8);
    assert(val == 2000000ULL);

    printf("TSF_TESTS_PASSED\n");
    return 0;
}
"""


def patch_core_h(content: str) -> str:
    """Añade estado de caché y cerrojo TSF a struct brcmf_if en core.h."""
    if "tsf_base_us" in content:
        return content

    # Incluir mutex.h si no está
    if "#include <linux/mutex.h>" not in content:
        content = content.replace("#include <net/cfg80211.h>", "#include <linux/mutex.h>\n#include <net/cfg80211.h>", 1)

    target = "bool fwil_fwerr;"
    replacement = (
        "/* TSF sysfs cache & sync */\n"
        "\tu64 tsf_base_us;\n"
        "\tu64 tsf_sync_ns;\n"
        "\tstruct mutex tsf_lock;\n"
        "\tbool fwil_fwerr;"
    )
    if target not in content:
        raise ValueError("No se encontró 'bool fwil_fwerr;' en core.h")
    return content.replace(target, replacement, 1)


def patch_core_c(content: str) -> str:
    """Modifica core.c para implementar tsf_show y registrar el atributo sysfs /sys/class/net/<iface>/tsf."""
    if "tsf_show" in content:
        return content

    # 1. Asegurar includes necesarios
    if "#include <linux/ktime.h>" not in content:
        content = content.replace("#include <linux/kernel.h>", "#include <linux/kernel.h>\n#include <linux/ktime.h>\n#include <linux/device.h>", 1)

    # 2. Inyectar tsf_show y DEVICE_ATTR_RO(tsf) antes de brcmf_net_attach
    tsf_show_code = r"""
static ssize_t tsf_show(struct device *d, struct device_attribute *attr, char *buf)
{
	struct net_device *ndev = to_net_dev(d);
	struct brcmf_if *ifp = netdev_priv(ndev);
	u64 now_ns;
	u64 current_tsf;
	s32 err;

	if (!ifp)
		return -ENODEV;

	mutex_lock(&ifp->tsf_lock);
	now_ns = ktime_get_raw_ns();

	if (ifp->tsf_sync_ns == 0 || (now_ns - ifp->tsf_sync_ns) >= 100000000ULL) {
		u64 fw_tsf = 0;
		err = brcmf_fil_iovar_data_get(ifp, "bcn_proc_deferred", &fw_tsf, sizeof(fw_tsf));
		if (err == 0 && fw_tsf != 0) {
			ifp->tsf_base_us = fw_tsf;
			ifp->tsf_sync_ns = now_ns;
			current_tsf = fw_tsf;
		} else if (ifp->tsf_sync_ns != 0) {
			u64 elapsed_us = (now_ns - ifp->tsf_sync_ns) / 1000ULL;
			current_tsf = ifp->tsf_base_us + elapsed_us;
		} else if (err == 0) {
			/* Firmware idle/unassociated returns 0: seed from host clock */
			ifp->tsf_base_us = now_ns / 1000ULL;
			ifp->tsf_sync_ns = now_ns;
			current_tsf = ifp->tsf_base_us;
		} else {
			mutex_unlock(&ifp->tsf_lock);
			return err;
		}
	} else {
		u64 elapsed_us = (now_ns - ifp->tsf_sync_ns) / 1000ULL;
		current_tsf = ifp->tsf_base_us + elapsed_us;
	}

	mutex_unlock(&ifp->tsf_lock);

	memcpy(buf, &current_tsf, sizeof(u64));
	return sizeof(u64);
}
static DEVICE_ATTR_RO(tsf);

"""
    target_attach = "int brcmf_net_attach(struct brcmf_if *ifp, bool locked)"
    if target_attach not in content:
        raise ValueError("No se encontró brcmf_net_attach en core.c")
    content = content.replace(target_attach, tsf_show_code + target_attach, 1)

    # 3. Registrar dev_attr_tsf en brcmf_net_attach
    target_destructor = "ndev->priv_destructor = brcmf_cfg80211_free_netdev;"
    create_sysfs = (
        "ndev->priv_destructor = brcmf_cfg80211_free_netdev;\n"
        "\tif (device_create_file(&ndev->dev, &dev_attr_tsf))\n"
        '\t\tbphy_err(drvr, "failed to create tsf sysfs file\\n");'
    )
    if target_destructor not in content:
        raise ValueError("No se encontró priv_destructor en core.c")
    content = content.replace(target_destructor, create_sysfs, 1)

    # 4. Eliminar dev_attr_tsf en brcmf_net_detach
    target_detach = "void brcmf_net_detach(struct net_device *ndev, bool locked)\n{"
    remove_sysfs = (
        "void brcmf_net_detach(struct net_device *ndev, bool locked)\n{\n"
        "\tdevice_remove_file(&ndev->dev, &dev_attr_tsf);"
    )
    if target_detach not in content:
        raise ValueError("No se encontró brcmf_net_detach en core.c")
    content = content.replace(target_detach, remove_sysfs, 1)

    # 5. Inicializar tsf_lock en brcmf_add_if
    target_lock = "spin_lock_init(&ifp->netif_stop_lock);"
    init_tsf = (
        "spin_lock_init(&ifp->netif_stop_lock);\n"
        "\tmutex_init(&ifp->tsf_lock);\n"
        "\tifp->tsf_base_us = 0;\n"
        "\tifp->tsf_sync_ns = 0;"
    )
    if target_lock not in content:
        raise ValueError("No se encontró spin_lock_init en core.c")
    content = content.replace(target_lock, init_tsf, 1)

    return content


def patch_core_c_mtu(content: str) -> str:
    """B17: permite MTU 1756 en las interfaces de brcmfmac (como el `mtu 1756` del pc2drc
    original) para que los vstrm de 1694 B salgan sin fragmentación IP. Idempotente."""
    line = "ndev->max_mtu = 1756;"
    if line in content:
        return content
    target = "\tndev->ethtool_ops = &brcmf_ethtool_ops;\n"
    if target not in content:
        raise ValueError("No se encontró ethtool_ops en brcmf_net_attach (core.c)")
    return content.replace(target, target + "\t/* B17: vstrm de 1694 B sin fragmentar (pc2drc: mtu 1756) */\n\t" + line + "\n", 1)


def apply_all_driver_patches(brcmfmac_dir: Path):
    """Aplica todos los parches (cfg80211.c, core.h, core.c) sobre el directorio de fuentes de brcmfmac."""
    cfg_file = brcmfmac_dir / "cfg80211.c"
    core_h = brcmfmac_dir / "core.h"
    core_c = brcmfmac_dir / "core.c"

    if cfg_file.exists():
        c = cfg_file.read_text(encoding="utf-8", errors="replace")
        cfg_file.write_text(patch_cfg80211_oui(c), encoding="utf-8")

    if core_h.exists():
        h = core_h.read_text(encoding="utf-8", errors="replace")
        core_h.write_text(patch_core_h(h), encoding="utf-8")

    if core_c.exists():
        c = core_c.read_text(encoding="utf-8", errors="replace")
        core_c.write_text(patch_core_c_mtu(patch_core_c(c)), encoding="utf-8")
