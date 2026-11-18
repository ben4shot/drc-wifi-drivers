#!/usr/bin/env python3
"""Lee y escribe iovars/ioctls del firmware Broadcom (CYW43455, brcmfmac) por el comando vendor DCMD de
nl80211, como haría `wl` (que no está en la Pi). Reutiliza el canal de fw_tsf_probe.py.

Uso (root, en la Pi):
  wliov.py get <iovar> [bytes]            -> hex + entero
  wliov.py set <iovar> <entero|hex:..>
  wliov.py ioctl <num> [bytes] [hex:..]   -> GET de un ioctl WLC_*
  wliov.py ioctlset <num> <entero|hex:..>
  wliov.py resumen [mac_del_mando]        -> lo útil para el enlace del GamePad
  -i <iface> (por defecto wlan0)
"""
import os
import socket
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fw_tsf_probe import (BRCM_OUI, BRCMF_NLATTR_DATA, BRCMF_VNDR_CMDS_DCMD, DCMD_HDR, NL80211_ATTR_IFINDEX,  # noqa: E402
                          NL80211_ATTR_VENDOR_DATA, NL80211_ATTR_VENDOR_ID, NL80211_ATTR_VENDOR_SUBCMD,
                          NL80211_CMD_VENDOR, NLM_F_ACK, NLM_F_REQUEST, Nl80211, nla, parse_nlas)

WLC_GET_VAR, WLC_SET_VAR = 262, 263
WLC_GET_RATE, WLC_GET_RSSI, WLC_GET_PHY_NOISE, WLC_GET_FAKEFRAG = 12, 127, 135, 218


def build_dcmd(cmd, payload, ret_len, is_set):
    """Cabecera brcmf_vndr_dcmd_hdr (cmd, len, offset, set, magic) + datos."""
    return DCMD_HDR.pack(cmd, max(ret_len, len(payload)), DCMD_HDR.size, 1 if is_set else 0, 0) + payload


def parse_value(text):
    """'hex:0a0b' -> bytes; entero (decimal o 0x) -> u32/i32 little-endian."""
    if text.startswith("hex:"):
        return bytes.fromhex(text[4:])
    v = int(text, 0)
    return struct.pack("<i" if v < 0 else "<I", v)


class Wl:
    def __init__(self, iface="wlan0"):
        self.nl = Nl80211()
        self.ifindex = socket.if_nametoindex(iface)

    def dcmd(self, cmd, payload=b"", ret_len=0, is_set=False):
        attrs = (nla(NL80211_ATTR_IFINDEX, struct.pack("<I", self.ifindex))
                 + nla(NL80211_ATTR_VENDOR_ID, struct.pack("<I", BRCM_OUI))
                 + nla(NL80211_ATTR_VENDOR_SUBCMD, struct.pack("<I", BRCMF_VNDR_CMDS_DCMD))
                 + nla(NL80211_ATTR_VENDOR_DATA, build_dcmd(cmd, payload, ret_len, is_set)))
        self.nl._send(self.nl.family, NLM_F_REQUEST | NLM_F_ACK, NL80211_CMD_VENDOR, attrs)
        reply = self.nl._recv()
        if is_set or not reply:
            return b""
        return parse_nlas(reply[NL80211_ATTR_VENDOR_DATA]).get(BRCMF_NLATTR_DATA, b"")

    def get(self, name, n=4):
        return self.dcmd(WLC_GET_VAR, name.encode() + b"\0", len(name) + 1 + n)[:n]

    def set(self, name, value):
        self.dcmd(WLC_SET_VAR, name.encode() + b"\0" + value, is_set=True)

    def get_int(self, name):
        return struct.unpack("<i", self.get(name, 4))[0]


def show(data):
    out = data.hex()
    if len(data) >= 4:
        out += f"  (i32={struct.unpack_from('<i', data)[0]})"
    return out


def resumen(wl, mac=None):
    def intento(etq, fn):
        try:
            print(f"{etq}: {fn()}")
        except (OSError, struct.error, KeyError) as e:
            print(f"{etq}: -- ({e})")

    intento("tasa actual (Mbit/s)", lambda: struct.unpack("<i", wl.dcmd(WLC_GET_RATE, b"\0" * 4, 4)[:4])[0] / 2)
    if mac:
        ea = bytes.fromhex(mac.replace(":", ""))
        intento("RSSI del mando (dBm)",
                lambda: struct.unpack("<i", wl.dcmd(WLC_GET_RSSI, struct.pack("<i", 0) + ea + b"\0\0", 12)[:4])[0])
    intento("ruido (dBm)", lambda: struct.unpack("<i", wl.dcmd(WLC_GET_PHY_NOISE, b"\0" * 4, 4)[:4])[0])
    intento("frameburst", lambda: struct.unpack("<i", wl.dcmd(WLC_GET_FAKEFRAG, b"\0" * 4, 4)[:4])[0])
    intento("país", lambda: wl.get("country", 12))
    for name in ("qtxpower", "txchain", "rxchain", "stbc_tx", "stbc_rx", "ampdu", "ampdu_mpdu", "ampdu_ba_wsize",
                 "ampdu_tx_ba_wsize", "ampdu_rx_ba_wsize", "amsdu", "nrate", "5g_rate", "bw_cap", "mpc", "btc_mode",
                 "sgi_tx", "ldpc_tx", "ldpc_cap", "txbf", "wme", "wme_apsd", "srl", "lrl", "rtsthresh", "obss_coex",
                 "interference", "interference_override", "tx_ampdu_mpdu", "scb_probe", "maxassoc", "bus:txglom",
                 "ccx_enable", "pspretend_threshold", "pspretend_retry_limit", "ampdu_retry_limit",
                 "ampdu_rr_retry_limit", "ampdu_frameburst_override", "rate_override"):
        intento(name, lambda n=name: show(wl.get(n, 4)))


def main(argv):
    iface = "wlan0"
    if "-i" in argv:
        i = argv.index("-i")
        iface = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        print(__doc__)
        return 1
    wl = Wl(iface)
    op = argv[1]
    if op == "get":
        print(show(wl.get(argv[2], int(argv[3]) if len(argv) > 3 else 4)))
    elif op == "set":
        wl.set(argv[2], parse_value(argv[3]))
    elif op == "ioctl":
        n = int(argv[3]) if len(argv) > 3 else 4
        payload = parse_value(argv[4]) if len(argv) > 4 else b"\0" * n
        print(show(wl.dcmd(int(argv[2], 0), payload, n)[:n]))
    elif op == "ioctlset":
        wl.dcmd(int(argv[2], 0), parse_value(argv[3]), is_set=True)
    elif op == "resumen":
        resumen(wl, argv[2] if len(argv) > 2 else None)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
