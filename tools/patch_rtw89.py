"""Parche del driver rtw89 (TP-Link 8852AU, fork morrownr) para usarlo como AP del GamePad.

Expone /sys/class/net/<if>/tsf (8 bytes crudos, igual que el parche de brcmfmac) leyendo el TSF
del puerto del vif: libdrc lo busca por la IP de la interfaz y no necesita cambios.
"""
import sys
from pathlib import Path

TSF_SHOW = r"""
static ssize_t tsf_show(struct device *d, struct device_attribute *attr, char *buf)
{
	struct net_device *ndev = to_net_dev(d);
	struct wireless_dev *wdev = ndev->ieee80211_ptr;
	struct rtw89_vif_link *rtwvif_link;
	struct ieee80211_hw *hw;
	struct ieee80211_vif *vif;
	struct rtw89_dev *rtwdev;
	u64 tsf = 0;
	int ret;

	if (!wdev)
		return -ENODEV;
	vif = wdev_to_ieee80211_vif(wdev);
	if (!vif)
		return -ENODEV;
	hw = wiphy_to_ieee80211_hw(wdev->wiphy);
	rtwdev = hw->priv;

	/* Nunca esperar el bloqueo: remove_interface borra este atributo con el bloqueo tomado y
	 * device_remove_file espera a los lectores (interbloqueo). */
	if (!mutex_trylock(&hw->wiphy->mtx))
		return -EAGAIN;
	rtwvif_link = rtw89_get_designated_link(vif_to_rtwvif(vif));
	ret = rtwvif_link ? rtw89_mac_port_get_tsf(rtwdev, rtwvif_link, &tsf) : -ENODEV;
	mutex_unlock(&hw->wiphy->mtx);
	if (ret)
		return ret;

	memcpy(buf, &tsf, sizeof(tsf));
	return sizeof(tsf);
}
static DEVICE_ATTR_RO(tsf);

"""

ADD_IFACE = "static int rtw89_ops_add_interface(struct ieee80211_hw *hw,"
ADD_END = "\trtw89_recalc_lps(rtwdev);\n\treturn 0;\n\nunset_link:"
REMOVE_IFACE = "\twiphy_delayed_work_cancel(hw->wiphy, &rtwvif->roc.roc_work);\n\n\trtwvif_link = rtwvif->links[RTW89_VIF_IDLE_LINK_ID];"

CREATE = (
    "\trtw89_recalc_lps(rtwdev);\n"
    "\t{\n"
    "\t\tstruct wireless_dev *wdev = ieee80211_vif_to_wdev(vif);\n\n"
    "\t\tif (wdev && wdev->netdev)\n"
    "\t\t\tdevice_create_file(&wdev->netdev->dev, &dev_attr_tsf);\n"
    "\t}\n"
    "\treturn 0;\n\nunset_link:"
)
REMOVE = (
    "\t{\n"
    "\t\tstruct wireless_dev *wdev = ieee80211_vif_to_wdev(vif);\n\n"
    "\t\tif (wdev && wdev->netdev)\n"
    "\t\t\tdevice_remove_file(&wdev->netdev->dev, &dev_attr_tsf);\n"
    "\t}\n"
    + REMOVE_IFACE
)


def patch_mac80211(content: str) -> str:
    """Añade el atributo sysfs tsf al crear/quitar la interfaz. Idempotente."""
    if "tsf_show" in content:
        return content
    for marker in (ADD_IFACE, ADD_END, REMOVE_IFACE):
        if content.count(marker) != 1:
            raise ValueError(f"No se encontró (una vez) en mac80211.c: {marker.strip()[:50]}")
    content = content.replace(ADD_IFACE, TSF_SHOW.lstrip("\n") + "\n" + ADD_IFACE, 1)
    content = content.replace(ADD_END, CREATE, 1)
    return content.replace(REMOVE_IFACE, REMOVE, 1)


def apply_all(src_dir: Path) -> None:
    f = Path(src_dir) / "mac80211.c"
    f.write_text(patch_mac80211(f.read_text()))


if __name__ == "__main__":
    apply_all(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "src" / "rtw89")
