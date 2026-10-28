"""Ed25519 signature verification (RFC 8032, section 5.1.7), pure Python.

The updater needs exactly one operation, checking a release manifest's
signature against the public key built into the app, and doing it here keeps
the app free of a compiled crypto dependency. Verification handles only public
data, so the timing side channels that make hand-written signing code risky do
not apply. Signing happens on the maintainer's machine and in CI, with the
`cryptography` package (scripts/sign_release.py).

Strict where the RFC is: a non-canonical S (>= L) or a point that does not
decode is a failed verification, not an exception.
"""

import hashlib

__all__ = ["verify"]

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


# Points in extended homogeneous coordinates (X, Y, Z, T), x = X/Z, y = Y/Z,
# x*y = T/Z.

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)                     # neutral element
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q):
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0
            and (p[1] * q[2] - q[1] * p[2]) % _P == 0)


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def verify(public_key, message, signature):
    """True if `signature` is a valid Ed25519 signature of `message`."""
    if not (isinstance(public_key, (bytes, bytearray)) and len(public_key) == 32):
        return False
    if not (isinstance(signature, (bytes, bytearray)) and len(signature) == 64):
        return False
    a = _decompress(bytes(public_key))
    r_bytes = bytes(signature[:32])
    r = _decompress(r_bytes)
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(
        hashlib.sha512(r_bytes + bytes(public_key) + bytes(message)).digest(),
        "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
