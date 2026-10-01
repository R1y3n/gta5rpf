"""AES-ECB and GTA 'NG' block decryption (vectorised with numpy)."""
import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_decrypt(data: bytes, key: bytes) -> bytes:
    n = len(data) - len(data) % 16
    if n == 0:
        return bytes(data)
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(bytes(data[:n])) + dec.finalize() + bytes(data[n:])


def aes_encrypt(data: bytes, key: bytes) -> bytes:
    n = len(data) - len(data) % 16
    if n == 0:
        return bytes(data)
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(bytes(data[:n])) + enc.finalize() + bytes(data[n:])


# Column layout of the 16 input bytes that feed each output word.
_ROUND_A = [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)]
_ROUND_B = [(0, 7, 10, 13), (1, 4, 11, 14), (2, 5, 8, 15), (3, 6, 9, 12)]


def _round(d, key4, tbl, layout):
    out = np.empty((d.shape[0], 4), dtype="<u4")
    for j, (a, b, c, e) in enumerate(layout):
        out[:, j] = tbl[a][d[:, a]] ^ tbl[b][d[:, b]] ^ tbl[c][d[:, c]] ^ tbl[e][d[:, e]] ^ key4[j]
    return out.view(np.uint8).reshape(-1, 16)


def ng_decrypt(data: bytes, key: bytes, tables: np.ndarray) -> bytes:
    """tables: uint32 array shaped (17, 16, 256); key: 272 bytes (17 x 4 uint32)."""
    n = len(data) // 16
    if n == 0:
        return bytes(data)
    d = np.frombuffer(data, dtype=np.uint8, count=n * 16).reshape(n, 16)
    sk = np.frombuffer(key, dtype="<u4").reshape(17, 4)
    d = _round(d, sk[0], tables[0], _ROUND_A)
    d = _round(d, sk[1], tables[1], _ROUND_A)
    for k in range(2, 16):
        d = _round(d, sk[k], tables[k], _ROUND_B)
    d = _round(d, sk[16], tables[16], _ROUND_A)
    return d.tobytes() + bytes(data[n * 16:])
