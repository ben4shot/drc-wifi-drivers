"""Mide el TSF que devuelve el firmware (iovar parcheado `bcn_proc_deferred`) sin pasar
por la caché/interpolación del driver, usando el comando vendor DCMD de brcmfmac por
nl80211 crudo, y lo compara con CLOCK_MONOTONIC de la Pi.

Uso en la Pi (con el AP levantado):  sudo python3 fw_tsf_probe.py [segundos] [hz] [iovar]
"""
import os
import socket
import struct
import sys
import time

NETLINK_GENERIC = 16
GENL_ID_CTRL = 0x10
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID = 1
CTRL_ATTR_FAMILY_NAME = 2
NLM_F_REQUEST = 1
NLM_F_ACK = 4
NLMSG_ERROR = 2
NLMSG_DONE = 3

NL80211_CMD_VENDOR = 103
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_VENDOR_ID = 195
NL80211_ATTR_VENDOR_SUBCMD = 196
NL80211_ATTR_VENDOR_DATA = 197

BRCM_OUI = 0x001018
BRCMF_VNDR_CMDS_DCMD = 1
BRCMF_NLATTR_DATA = 2
WLC_GET_VAR = 262
DCMD_HDR = struct.Struct("<IiIII")


def _align4(n):
    return (n + 3) & ~3


def nla(attr_type, payload):
    hdr = struct.pack("<HH", 4 + len(payload), attr_type)
    return hdr + payload + b"\0" * (_align4(len(payload)) - len(payload))


def parse_nlas(buf):
    attrs = {}
    off = 0
    while off + 4 <= len(buf):
        ln, typ = struct.unpack_from("<HH", buf, off)
        if ln < 4:
            break
        attrs[typ & 0x3FFF] = buf[off + 4 : off + ln]
        off += _align4(ln)
    return attrs


def build_dcmd_get_var(name, ret_len):
    """Cabecera brcmf_vndr_dcmd_hdr + nombre del iovar (GET)."""
    return DCMD_HDR.pack(WLC_GET_VAR, ret_len, DCMD_HDR.size, 0, 0) + name.encode() + b"\0"


def dcmd_ret_len(name):
    """El buffer GET_VAR lleva el nombre del iovar y luego recibe el resultado (u64)."""
    return len(name) + 1 + 8


def parse_vendor_reply_u64(vendor_data):
    """Extrae el u64 del BRCMF_NLATTR_DATA de la respuesta vendor."""
    data = parse_nlas(vendor_data).get(BRCMF_NLATTR_DATA)
    if data is None or len(data) < 8:
        raise ValueError("respuesta vendor sin BRCMF_NLATTR_DATA de 8 bytes")
    return struct.unpack_from("<Q", data)[0]


def analyze(samples):
    """samples: lista de (t_us_monotonic, tsf_us). Ajuste lineal y estadísticas."""
    n = len(samples)
    t0, f0 = samples[0]
    xs = [t - t0 for t, _ in samples]
    ys = [f - f0 for _, f in samples]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    steps = [ys[i + 1] - ys[i] for i in range(n - 1)]
    dts = [xs[i + 1] - xs[i] for i in range(n - 1)]
    jumps = [(i, s - d) for i, (s, d) in enumerate(zip(steps, dts)) if abs(s - d) > 1000]
    return {
        "n": n,
        "ppm": (slope - 1.0) * 1e6,
        "resid_min_us": min(resid),
        "resid_max_us": max(resid),
        "distinct": len(set(ys)),
        "stalls": sum(1 for s in steps if s == 0),
        "backwards": sum(1 for s in steps if s < 0),
        "jumps": jumps,
    }


class Nl80211:
    def __init__(self):
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.seq = 0
        self.family = self._resolve("nl80211")

    def _send(self, msg_type, flags, cmd, attrs):
        self.seq += 1
        payload = struct.pack("<BBH", cmd, 1, 0) + attrs
        hdr = struct.pack("<IHHII", 16 + len(payload), msg_type, flags, self.seq, 0)
        self.sock.send(hdr + payload)

    def _recv(self):
        """Devuelve los atributos genl de la primera respuesta; lanza OSError si hay error."""
        result = None
        while True:
            buf = self.sock.recv(65536)
            off = 0
            while off < len(buf):
                ln, typ, _flags, _seq, _pid = struct.unpack_from("<IHHII", buf, off)
                body = buf[off + 16 : off + ln]
                off += _align4(ln)
                if typ == NLMSG_ERROR:
                    err = struct.unpack_from("<i", body)[0]
                    if err:
                        raise OSError(-err, os.strerror(-err))
                    return result
                if typ == NLMSG_DONE:
                    return result
                result = parse_nlas(body[4:])

    def _resolve(self, name):
        self._send(GENL_ID_CTRL, NLM_F_REQUEST | NLM_F_ACK, CTRL_CMD_GETFAMILY,
                   nla(CTRL_ATTR_FAMILY_NAME, name.encode() + b"\0"))
        return struct.unpack("<H", self._recv()[CTRL_ATTR_FAMILY_ID])[0]

    def get_var_u64(self, ifindex, name):
        attrs = (nla(NL80211_ATTR_IFINDEX, struct.pack("<I", ifindex))
                 + nla(NL80211_ATTR_VENDOR_ID, struct.pack("<I", BRCM_OUI))
                 + nla(NL80211_ATTR_VENDOR_SUBCMD, struct.pack("<I", BRCMF_VNDR_CMDS_DCMD))
                 + nla(NL80211_ATTR_VENDOR_DATA, build_dcmd_get_var(name, dcmd_ret_len(name))))
        self._send(self.family, NLM_F_REQUEST | NLM_F_ACK, NL80211_CMD_VENDOR, attrs)
        return parse_vendor_reply_u64(self._recv()[NL80211_ATTR_VENDOR_DATA])


def read_sysfs_tsf(iface):
    with open(f"/sys/class/net/{iface}/tsf", "rb") as f:
        return struct.unpack("<Q", f.read(8))[0]


def main(argv):
    seconds = float(argv[1]) if len(argv) > 1 else 60
    hz = float(argv[2]) if len(argv) > 2 else 100
    iovar = argv[3] if len(argv) > 3 else "bcn_proc_deferred"
    iface = "wlan0"
    nl = Nl80211()
    ifindex = socket.if_nametoindex(iface)
    fw, sysfs, rtts = [], [], []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        a = time.monotonic_ns()
        v = nl.get_var_u64(ifindex, iovar)
        b = time.monotonic_ns()
        fw.append(((a + b) // 2000, v))
        rtts.append((b - a) / 1000)
        c = time.monotonic_ns()
        sysfs.append((c // 1000, read_sysfs_tsf(iface)))
        time.sleep(max(0.0, 1.0 / hz - (time.monotonic_ns() - a) / 1e9))
    rtts.sort()
    print(f"rtt iovar us: p50={rtts[len(rtts)//2]:.0f} p99={rtts[int(len(rtts)*0.99)]:.0f}")
    for label, s in (("firmware", fw), ("sysfs", sysfs)):
        r = analyze(s)
        jumps = r.pop("jumps")
        print(label, {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()},
              "saltos>1ms:", len(jumps), [(i, j) for i, j in jumps[:10]])
    if "--dump" in argv:
        with open("/tmp/fw_tsf_probe.csv", "w") as f:
            for (t, v), (ts, vs) in zip(fw, sysfs):
                f.write(f"{t},{v},{ts},{vs}\n")


if __name__ == "__main__":
    main(sys.argv)
