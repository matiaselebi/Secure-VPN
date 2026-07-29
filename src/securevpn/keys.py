"""Generación de claves WireGuard.

Decisión de diseño central del proyecto: acá NO se implementa criptografía.
Una clave WireGuard es una clave X25519 (curva elíptica Curve25519) de 32
bytes, codificada en base64. Este módulo le pide esas claves a la librería
`cryptography` (la implementación de referencia del ecosistema Python,
auditada y mantenida por profesionales), y lo único propio es la codificación
base64 y el manejo de archivos.

¿Por qué no llamar al binario `wg genkey`? Porque así el proyecto genera
claves idéntico en Windows, Linux, y en los tests de CI, sin depender de
que WireGuard esté instalado en la máquina que las genera. Los tests
verifican la compatibilidad: si `wg` está disponible, se comprueba que
nuestra clave pública derivada coincide byte a byte con la que calcula
`wg pubkey`.
"""

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)


def generate_private_key() -> str:
    """Devuelve una clave privada WireGuard nueva (base64 de 32 bytes)."""
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


def derive_public_key(private_key_b64: str) -> str:
    """Deriva la clave pública que corresponde a una privada.

    Es la misma operación que hace `wg pubkey`: multiplicación de punto en
    Curve25519 - hecha por la librería `cryptography`, no por nosotros.
    """
    raw_private = base64.b64decode(private_key_b64)
    private = X25519PrivateKey.from_private_bytes(raw_private)
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_public).decode("ascii")


def generate_preshared_key() -> str:
    """Clave presimétrica opcional de WireGuard (PresharedKey): 32 bytes
    aleatorios en base64. WireGuard la mezcla en su handshake como capa
    extra de defensa (incluso frente a un hipotético quiebre futuro de la
    criptografía de curvas elípticas, p. ej. por computación cuántica:
    quien no tenga este secreto compartido sigue sin poder descifrar).
    Generarla es literalmente pedirle 32 bytes al generador criptográfico
    del sistema operativo - nada que inventar."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def is_valid_key(key_b64: str) -> bool:
    """Chequea que un string tenga la pinta exacta de una clave WireGuard:
    base64 válido que decodifica a exactamente 32 bytes."""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (ValueError, TypeError):
        return False
    return len(raw) == 32


def validate_public_key(public_key_b64: str) -> None:
    """Levanta ValueError si el string no es una clave pública X25519 usable."""
    if not is_valid_key(public_key_b64):
        raise ValueError(f"clave pública inválida: {public_key_b64!r}")
    X25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
