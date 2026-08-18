"""Canonical serialisation and shared validation primitives (GB-002).

Everything here is pure and deterministic. Two processes on different machines,
running different Python patch versions, must produce byte-identical output for
identical input -- the evidence hash chain depends on it.

Determinism rules enforced by :func:`canonical_bytes`:

* object keys are sorted;
* no insignificant whitespace;
* ``NaN`` / ``Infinity`` are rejected rather than emitted as non-standard JSON;
* enums serialise to their ``value``;
* tuples, lists and frozensets normalise to JSON arrays (frozensets are sorted);
* unsupported types raise instead of silently stringifying.

The last rule is the important one. ``str(obj)`` on an arbitrary object embeds a
memory address, which would make the hash chain non-reproducible.
"""

from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from glassbox.domain.errors import DomainValidationError

__all__ = [
    "canonical_json",
    "canonical_bytes",
    "freeze_mapping",
    "require_non_empty",
    "require_identifier",
    "require_finite",
    "require_non_negative",
    "require_timestamp",
    "require_sha256_hex",
    "IDENTIFIER_PATTERN",
    "MAX_IDENTIFIER_LENGTH",
    "SHA256_HEX_LENGTH",
]

#: Identifiers (tenant ids, agent refs, action names, resource ids) are
#: restricted to a conservative character set. This is a defence-in-depth
#: measure: these values end up in cache keys, Redis keys, file paths and log
#: lines, and a permissive charset invites injection and key-collision bugs.
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")

#: Upper bound on identifier length, to bound key sizes and log volume.
MAX_IDENTIFIER_LENGTH = 256

#: Length of a hex-encoded SHA-256 digest.
SHA256_HEX_LENGTH = 64

_HEX_ALPHABET = frozenset("0123456789abcdef")

#: Timestamps are POSIX epoch seconds as ``float``. Values outside this range
#: indicate a unit error (milliseconds mistaken for seconds, or a sentinel).
_MIN_TIMESTAMP = 0.0
_MAX_TIMESTAMP = 4_102_444_800.0  # 2100-01-01T00:00:00Z

_JSON_SCALARS = (str, int, float, bool)


# --------------------------------------------------------------------------- #
# Canonical serialisation
# --------------------------------------------------------------------------- #


def _normalise(value: Any, *, path: str = "$") -> Any:
    """Recursively convert ``value`` into JSON-canonical primitives.

    Args:
        value: The value to normalise.
        path: JSON-path-like breadcrumb used in error messages.

    Returns:
        A structure containing only ``dict``, ``list``, ``str``, ``int``,
        ``float``, ``bool`` and ``None``.

    Raises:
        DomainValidationError: If ``value`` contains a type that cannot be
            deterministically serialised, or a non-finite float.
    """
    if value is None:
        return None

    if isinstance(value, Enum):
        return _normalise(value.value, path=path)

    # bool must be checked before int: bool is a subclass of int.
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError(
                "non-finite float cannot be canonically serialised",
                field=path,
                value=repr(value),
            )
        return value

    if isinstance(value, str):
        return value

    if isinstance(value, (bytes, bytearray)):
        raise DomainValidationError(
            "raw bytes must be hex- or base64-encoded by the caller before serialisation",
            field=path,
        )

    if isinstance(value, Mapping):
        normalised: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainValidationError(
                    "mapping keys must be strings for canonical serialisation",
                    field=path,
                    key_type=type(key).__name__,
                )
            normalised[key] = _normalise(item, path=f"{path}.{key}")
        return normalised

    if isinstance(value, (frozenset, set)):
        items = [_normalise(item, path=f"{path}[]") for item in value]
        try:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        except TypeError as exc:  # pragma: no cover - guarded by _normalise above
            raise DomainValidationError(
                "set members are not canonically orderable", field=path
            ) from exc

    if isinstance(value, Sequence):
        return [_normalise(item, path=f"{path}[{index}]") for index, item in enumerate(value)]

    raise DomainValidationError(
        "type is not canonically serialisable; convert it explicitly",
        field=path,
        offending_type=type(value).__name__,
    )


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialise ``payload`` to a deterministic JSON string.

    Args:
        payload: A mapping of string keys to serialisable values.

    Returns:
        Canonical JSON: sorted keys, minimal separators, UTF-8 text.

    Raises:
        DomainValidationError: If the payload contains non-serialisable values.
    """
    if not isinstance(payload, Mapping):
        raise DomainValidationError(
            "canonical_json expects a mapping", offending_type=type(payload).__name__
        )
    return json.dumps(
        _normalise(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialise ``payload`` to deterministic UTF-8 bytes for hashing or MAC-ing."""
    return canonical_json(payload).encode("utf-8")


def freeze_mapping(
    value: Optional[Mapping[str, Any]], *, field: str
) -> Tuple[Tuple[str, Any], ...]:
    """Convert a mapping into a hashable, order-stable tuple of pairs.

    Frozen dataclasses that hold a ``dict`` are not hashable and are not truly
    immutable. Storing the sorted key/value pairs preserves both properties.

    Args:
        value: The mapping to freeze. ``None`` is treated as empty.
        field: Field name used in error messages.

    Returns:
        A tuple of ``(key, value)`` pairs sorted by key.

    Raises:
        DomainValidationError: If keys are not strings or values are not
            canonically serialisable.
    """
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise DomainValidationError(
            "expected a mapping", field=field, offending_type=type(value).__name__
        )
    normalised = _normalise(value, path=field)
    return tuple(sorted((key, _to_hashable(item)) for key, item in normalised.items()))


def _to_hashable(value: Any) -> Any:
    """Recursively convert normalised JSON primitives into hashable equivalents."""
    if isinstance(value, dict):
        return tuple(sorted((key, _to_hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_to_hashable(item) for item in value)
    return value


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def require_non_empty(value: Any, *, field: str) -> str:
    """Assert that ``value`` is a non-blank string.

    Raises:
        DomainValidationError: If the value is missing, not a string, or blank.
    """
    if not isinstance(value, str):
        raise DomainValidationError(
            "expected a string", field=field, offending_type=type(value).__name__
        )
    if not value.strip():
        raise DomainValidationError("must not be empty or blank", field=field)
    return value


def require_identifier(value: Any, *, field: str) -> str:
    """Assert that ``value`` is a safe, bounded identifier.

    Raises:
        DomainValidationError: If the value is blank, too long, or contains
            characters outside :data:`IDENTIFIER_PATTERN`.
    """
    text = require_non_empty(value, field=field)
    if len(text) > MAX_IDENTIFIER_LENGTH:
        raise DomainValidationError(
            "identifier exceeds the maximum length",
            field=field,
            length=len(text),
            maximum=MAX_IDENTIFIER_LENGTH,
        )
    if not IDENTIFIER_PATTERN.match(text):
        raise DomainValidationError(
            "identifier contains characters that are unsafe for keys and paths",
            field=field,
            pattern=IDENTIFIER_PATTERN.pattern,
        )
    return text


def require_finite(value: Any, *, field: str) -> float:
    """Assert that ``value`` is a finite real number and return it as ``float``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError(
            "expected a real number", field=field, offending_type=type(value).__name__
        )
    number = float(value)
    if not math.isfinite(number):
        raise DomainValidationError("must be finite", field=field, value=repr(value))
    return number


def require_non_negative(value: Any, *, field: str) -> float:
    """Assert that ``value`` is a finite, non-negative number."""
    number = require_finite(value, field=field)
    if number < 0:
        raise DomainValidationError("must not be negative", field=field, value=number)
    return number


def require_timestamp(value: Any, *, field: str) -> float:
    """Assert that ``value`` is a plausible POSIX epoch timestamp in seconds.

    Rejecting out-of-range values catches the common millisecond/second unit
    error before it reaches the evidence chain, where it would be permanent.
    """
    number = require_finite(value, field=field)
    if not _MIN_TIMESTAMP <= number <= _MAX_TIMESTAMP:
        raise DomainValidationError(
            "timestamp is outside the supported range; expected POSIX epoch seconds",
            field=field,
            value=number,
            minimum=_MIN_TIMESTAMP,
            maximum=_MAX_TIMESTAMP,
        )
    return number


def require_sha256_hex(value: Any, *, field: str) -> str:
    """Assert that ``value`` is a lower-case hex SHA-256 digest.

    Digests appear in policy bundle identities, tool definition pins, prompt
    provenance and result summaries. Validating them in one place keeps the
    accepted form identical everywhere, which matters because these values are
    compared for equality to decide whether a tool has been swapped underneath a
    grant.

    Args:
        value: The candidate digest. Case is normalised to lower.
        field: Field name used in error messages.

    Returns:
        The normalised digest.

    Raises:
        DomainValidationError: If the value is not 64 hex characters.
    """
    digest = require_non_empty(value, field=field).lower()
    if len(digest) != SHA256_HEX_LENGTH or any(
        character not in _HEX_ALPHABET for character in digest
    ):
        raise DomainValidationError(
            "expected a 64-character hex SHA-256 digest",
            field=field,
            length=len(digest),
        )
    return digest


def require_unique(values: Sequence[str], *, field: str) -> Tuple[str, ...]:
    """Assert that ``values`` contains no duplicates and return it as a tuple."""
    seen: Set[str] = set()
    duplicates: List[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise DomainValidationError(
            "contains duplicate entries", field=field, duplicates=sorted(set(duplicates))
        )
    return tuple(values)
