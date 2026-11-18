import struct

from fw_tsf_probe import analyze, build_dcmd_get_var, parse_vendor_reply_u64


def test_build_dcmd_get_var_matches_iw_payload():
    # Mismo payload que funcionó con `iw vendor recv` para cur_etheraddr
    payload = build_dcmd_get_var("tsf", 16)
    assert payload[:20] == struct.pack("<IiIII", 262, 16, 20, 0, 0)
    assert payload[20:] == b"tsf\0"


def test_parse_vendor_reply_u64_from_real_response():
    # Respuesta real de la Pi (cur_etheraddr): NLATTR_DATA(len 0x14) + NLATTR_LEN
    raw = bytes.fromhex("14000200d83add12340068657261646472000000" "060001001000" "0000")
    assert parse_vendor_reply_u64(raw) == struct.unpack("<Q", bytes.fromhex("d83add1234006865"))[0]


def test_analyze_detects_linear_clock():
    samples = [(t, 5_000_000 + t) for t in range(0, 1_000_000, 10_000)]
    r = analyze(samples)
    assert abs(r["ppm"]) < 1e-6 and r["jumps"] == [] and r["stalls"] == 0


def test_analyze_detects_sawtooth():
    # Se atrasa 1 % y salta +100 ms cada 10 s
    samples = []
    for t in range(0, 30_000_000, 10_000):
        samples.append((t, int((t % 10_000_000) * 0.99) + (t // 10_000_000) * 10_000_000))
    r = analyze(samples)
    assert len(r["jumps"]) == 2
    assert r["resid_max_us"] - r["resid_min_us"] > 50_000


def test_build_dcmd_get_var_buffer_fits_long_name_and_u64():
    from fw_tsf_probe import dcmd_ret_len
    name = "bcn_proc_deferred"
    assert dcmd_ret_len(name) >= len(name) + 1 + 8
