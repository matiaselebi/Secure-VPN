import base64
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.keys import (  # noqa: E402
    derive_public_key,
    generate_preshared_key,
    generate_private_key,
    is_valid_key,
    validate_public_key,
)


def test_private_key_is_32_bytes_base64():
    key = generate_private_key()
    assert len(base64.b64decode(key)) == 32


def test_public_key_derivation_is_deterministic():
    private = generate_private_key()
    assert derive_public_key(private) == derive_public_key(private)


def test_different_private_keys_give_different_publics():
    assert derive_public_key(generate_private_key()) != derive_public_key(
        generate_private_key()
    )


def test_preshared_keys_are_random_32_bytes():
    a, b = generate_preshared_key(), generate_preshared_key()
    assert a != b
    assert len(base64.b64decode(a)) == 32


def test_is_valid_key_rejects_garbage():
    assert not is_valid_key("hola")
    assert not is_valid_key("")
    assert not is_valid_key(base64.b64encode(b"cortito").decode())
    assert is_valid_key(generate_private_key())


def test_validate_public_key_raises_on_invalid():
    with pytest.raises(ValueError):
        validate_public_key("no-es-una-clave")


@pytest.mark.skipif(
    shutil.which("wg") is None,
    reason="requiere wireguard-tools para la validación cruzada",
)
def test_public_key_matches_official_wg_pubkey():
    """La prueba de compatibilidad clave del proyecto: nuestra derivación
    (vía la librería `cryptography`) tiene que dar EXACTAMENTE la misma
    clave pública que calcula el `wg pubkey` oficial. Si esto pasa, las
    claves generadas por SecureVPN son claves WireGuard de verdad."""
    private = generate_private_key()
    official = subprocess.run(
        ["wg", "pubkey"], input=private, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert derive_public_key(private) == official
