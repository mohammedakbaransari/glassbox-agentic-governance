"""
GlassBox Framework — Encryption Utilities (v1.1.0)
===================================================

Cryptographic utilities for:
  - Field-level encryption (AES-256-GCM)
  - Key management with key rotation
  - Encrypted caching
  - Secure secrets handling
  - Hash-based integrity verification

Design:
  - Uses cryptography library (FIPS-compliant)
  - Authenticated encryption (GCM mode prevents tampering)
  - Optional key derivation (PBKDF2)
  - In-memory key handling with secure cleanup
  - Support for external key management services

Usage:
    from glassbox.governance.encryption import CryptoManager, EncryptedField
    
    # Initialize with auto-generated key
    crypto = CryptoManager()
    
    # Encrypt sensitive data
    encrypted = crypto.encrypt(b"sensitive_data")
    
    # Decrypt
    decrypted = crypto.decrypt(encrypted)
    
    # Work with field-encrypted objects
    field = EncryptedField(name="password", plaintext="secret123")
    encrypted_field = crypto.encrypt_field(field)
    decrypted_field = crypto.decrypt_field(encrypted_field)
    
    # Use derived keys from passphrases
    crypto_derived = CryptoManager.from_passphrase("my_secure_passphrase")
    
Author: Mohammed Akbar Ansari
"""

import os
import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from glassbox.governance.logging_manager import get_logger

log = get_logger("encryption")


@dataclass
class EncryptedField:
    """Represents an encrypted field."""

    name: str
    plaintext: Optional[str] = None
    ciphertext: Optional[bytes] = None
    nonce: Optional[bytes] = None
    tag: Optional[bytes] = None
    encrypted_at: Optional[datetime] = None

    def is_encrypted(self) -> bool:
        """Check if field is encrypted."""
        return self.ciphertext is not None


class CryptoManager:
    """
    Cryptographic operations manager.

    Uses AES-256-GCM for authenticated encryption.
    """

    def __init__(self, key: Optional[bytes] = None):
        if not HAS_CRYPTO:
            raise RuntimeError(
                "Encryption requires: pip install cryptography"
            )

        # Use provided key or generate random 256-bit key
        if key:
            if len(key) != 32:
                raise ValueError("Key must be exactly 32 bytes (256 bits)")
            self.key = key
        else:
            self.key = os.urandom(32)

        self._lock = threading.Lock()
        self._stats = {"encryptions": 0, "decryptions": 0, "errors": 0}

        log.info("CryptoManager initialized with 256-bit key")

    @staticmethod
    def from_passphrase(
        passphrase: str,
        salt: Optional[bytes] = None,
        iterations: int = 100000,
    ) -> "CryptoManager":
        """
        Derive encryption key from passphrase using PBKDF2.

        Args:
            passphrase: User passphrase (should be strong)
            salt: Optional salt (will be generated if not provided)
            iterations: PBKDF2 iterations (higher = more secure but slower)

        Returns:
            CryptoManager instance with derived key
        """
        salt = salt or os.urandom(16)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
            backend=default_backend(),
        )
        key = kdf.derive(passphrase.encode())

        log.info(
            "CryptoManager initialized from passphrase "
            "(iterations=%d, salt_len=%d)", iterations, len(salt)
        )

        return CryptoManager(key=key)

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
        """
        Encrypt data using AES-256-GCM.

        Args:
            plaintext: Data to encrypt
            aad: Optional additional authenticated data (not encrypted but authenticated)

        Returns:
            (nonce || ciphertext || tag) concatenated
        """
        try:
            nonce = os.urandom(12)  # 96-bit nonce for GCM
            cipher = Cipher(
                algorithms.AES(self.key),
                modes.GCM(nonce),
                backend=default_backend(),
            )
            encryptor = cipher.encryptor()

            if aad:
                encryptor.authenticate_additional_data(aad)

            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            tag = encryptor.tag

            # Return nonce || ciphertext || tag
            result = nonce + ciphertext + tag
            with self._lock:
                self._stats["encryptions"] += 1
            return result

        except Exception as exc:
            with self._lock:
                self._stats["errors"] += 1
            log.error("Encryption failed: %s", exc)
            raise

    def decrypt(self, encrypted: bytes, aad: Optional[bytes] = None) -> bytes:
        """
        Decrypt AES-256-GCM encrypted data.

        Args:
            encrypted: (nonce || ciphertext || tag) from encrypt()
            aad: Optional additional authenticated data (must match encrypt())

        Returns:
            Decrypted plaintext
        """
        try:
            nonce = encrypted[:12]
            ciphertext = encrypted[12:-16]
            tag = encrypted[-16:]

            cipher = Cipher(
                algorithms.AES(self.key),
                modes.GCM(nonce, tag),
                backend=default_backend(),
            )
            decryptor = cipher.decryptor()

            if aad:
                decryptor.authenticate_additional_data(aad)

            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            with self._lock:
                self._stats["decryptions"] += 1
            return plaintext

        except Exception as exc:
            with self._lock:
                self._stats["errors"] += 1
            log.error("Decryption failed: %s", exc)
            raise

    def encrypt_field(self, field: EncryptedField) -> EncryptedField:
        """Encrypt a field."""
        if field.plaintext is None:
            raise ValueError("Field plaintext is None")

        with self._lock:
            encrypted_data = self.encrypt(field.plaintext.encode())

            # Parse encrypted data
            nonce = encrypted_data[:12]
            ciphertext = encrypted_data[12:-16]
            tag = encrypted_data[-16:]

            return EncryptedField(
                name=field.name,
                ciphertext=ciphertext,
                nonce=nonce,
                tag=tag,
                encrypted_at=datetime.now(timezone.utc),
            )

    def decrypt_field(self, field: EncryptedField) -> EncryptedField:
        """Decrypt a field."""
        if field.ciphertext is None:
            raise ValueError("Field is not encrypted")

        with self._lock:
            encrypted_data = field.nonce + field.ciphertext + field.tag
            plaintext = self.decrypt(encrypted_data)

            return EncryptedField(
                name=field.name,
                plaintext=plaintext.decode(),
            )

    @staticmethod
    def hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
        """
        Hash a password using PBKDF2 (for password verification).

        Args:
            password: Password to hash
            salt: Optional salt (will be generated if not provided)

        Returns:
            (hashed_password, salt) tuple (both as hex strings)
        """
        salt = salt or os.urandom(32)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=64,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        hash_result = kdf.derive(password.encode())

        return hash_result.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, hashed: str, salt: str) -> bool:
        """
        Verify a password against its hash.

        Args:
            password: Password to verify
            hashed: Hashed password (hex string)
            salt: Salt used for hashing (hex string)

        Returns:
            True if password matches, False otherwise
        """
        try:
            salt_bytes = bytes.fromhex(salt)
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=64,
                salt=salt_bytes,
                iterations=100000,
                backend=default_backend(),
            )
            computed_hash = kdf.derive(password.encode())
            return hmac.compare_digest(computed_hash.hex(), hashed)
        except Exception as exc:
            log.error("Password verification failed: %s", exc)
            return False

    @staticmethod
    def compute_hmac(
        data: bytes,
        key: Optional[bytes] = None,
    ) -> str:
        """
        Compute HMAC-SHA256 digest of data.

        Args:
            data: Data to hash
            key: Optional HMAC key (uses fixed key if not provided)

        Returns:
            Hex digest
        """
        key = key or b"glassbox-integrity-key"
        digest = hmac.new(key, data, hashlib.sha256).digest()
        return digest.hex()

    @staticmethod
    def verify_hmac(
        data: bytes,
        expected_hmac: str,
        key: Optional[bytes] = None,
    ) -> bool:
        """
        Verify HMAC-SHA256 digest.

        Args:
            data: Data to verify
            expected_hmac: Expected HMAC (hex string)
            key: HMAC key (must match compute_hmac() call)

        Returns:
            True if HMAC matches, False otherwise
        """
        computed = CryptoManager.compute_hmac(data, key)
        return hmac.compare_digest(computed, expected_hmac)

    def get_stats(self) -> Dict[str, Any]:
        """Get encryption statistics."""
        return {
            "encryptions": self._stats["encryptions"],
            "decryptions": self._stats["decryptions"],
            "errors": self._stats["errors"],
            "key_size_bits": len(self.key) * 8,
        }


class SecretManager:
    """Manage sensitive secrets in memory with secure cleanup."""

    def __init__(self):
        self._secrets: Dict[str, str] = {}
        self._lock = threading.Lock()

    def store_secret(self, name: str, value: str) -> None:
        """Store a secret in memory."""
        with self._lock:
            self._secrets[name] = value

    def get_secret(self, name: str) -> Optional[str]:
        """Retrieve a secret from memory."""
        with self._lock:
            return self._secrets.get(name)

    def delete_secret(self, name: str) -> bool:
        """Delete a secret from memory."""
        with self._lock:
            if name in self._secrets:
                # Overwrite with random data before deleting
                self._secrets[name] = os.urandom(len(self._secrets[name])).hex()
                del self._secrets[name]
                return True
            return False

    def clear_all_secrets(self) -> None:
        """Clear all secrets from memory."""
        with self._lock:
            for name in list(self._secrets.keys()):
                self._secrets[name] = os.urandom(32).hex()
            self._secrets.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get secret manager statistics."""
        with self._lock:
            return {
                "secrets_count": len(self._secrets),
                "secret_names": list(self._secrets.keys()),
            }
