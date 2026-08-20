"""WORM anchor storage adapters (GB-007).

Two implementations of :class:`~glassbox.ports.worm.WormAnchorStore`.

:class:`InMemoryWormAnchorStore` is the development reference and the conformance
baseline. It provides no durability at all, which is honest: the ``dev`` profile
claims no assurance.

:class:`FilesystemWormAnchorStore` is the smallest implementation that is
genuinely write-once. It creates each anchor with ``O_EXCL`` -- so a concurrent
writer loses rather than overwrites -- fsyncs the file *and its directory*, and
then removes write permission. It is suitable for a mounted appliance volume or a
retention-locked filesystem.

Neither is a substitute for object-lock storage in production. The real guarantee
should come from the storage system (S3 Object Lock in compliance mode, an
immutable blob container, a WORM appliance) for the same reason the evidence
table has a database trigger and not merely a code path: a guarantee enforced
only by the process that benefits from breaking it is not a guarantee.

:class:`S3WormAnchorStore` is that production-grade adapter: it leans on S3
Object Lock in compliance mode, which even the AWS account root user cannot
shorten or remove for the retention period. Importing this module never
requires ``boto3``; only constructing :class:`S3WormAnchorStore` does, so the
core keeps its zero-mandatory-dependency install.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from glassbox.domain.errors import EvidenceIntegrityError, EvidenceWriteError
from glassbox.domain.evidence import WormAnchor

__all__ = [
    "InMemoryWormAnchorStore",
    "FilesystemWormAnchorStore",
    "S3WormAnchorStore",
    "WormStoreUnavailableError",
    "anchor_to_json",
    "anchor_from_json",
]


class WormStoreUnavailableError(EvidenceWriteError):
    """The S3 driver is not installed, or the bucket cannot be reached.

    A subclass of :class:`~glassbox.domain.errors.EvidenceWriteError`, so a
    caller that already fails closed on evidence-write problems needs no
    change to handle this adapter.
    """

    code = "worm_store_unavailable"


def anchor_to_json(anchor: WormAnchor) -> str:
    """Serialise an anchor to canonical JSON."""
    return json.dumps(
        dict(anchor.as_evidence()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def anchor_from_json(payload: str) -> WormAnchor:
    """Rebuild an anchor from its canonical JSON.

    Raises:
        EvidenceIntegrityError: If the payload is malformed. A corrupt anchor is
            a different finding from a missing one and must not be reported as
            absence.
    """
    try:
        data = json.loads(payload)
        return WormAnchor(
            anchor_id=data["anchor_id"],
            segment_id=data["segment_id"],
            tenant_id=data["tenant_id"],
            merkle_root=bytes.fromhex(data["merkle_root"]),
            tree_size=int(data["tree_size"]),
            first_seq=int(data["first_seq"]),
            last_seq=int(data["last_seq"]),
            sealed_at=float(data["sealed_at"]),
            root_signature=bytes.fromhex(data["root_signature"]),
            signer_key_id=data["signer_key_id"],
        )
    except EvidenceIntegrityError:
        raise
    except Exception as exc:
        raise EvidenceIntegrityError(
            "stored anchor is malformed",
            cause=type(exc).__name__,
            detail=str(exc),
        ) from exc


class InMemoryWormAnchorStore:
    """Write-once anchors held in process memory. **Development only.**"""

    __slots__ = ("_lock", "_anchors")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._anchors: Dict[str, WormAnchor] = {}

    def put(self, anchor: WormAnchor) -> str:
        """Store an anchor, refusing to replace a different one.

        Raises:
            EvidenceWriteError: If a *different* anchor already exists under this
                id. An identical re-store is a retried seal, not an attack.
        """
        if not isinstance(anchor, WormAnchor):
            raise EvidenceWriteError(
                "put requires a WormAnchor", offending_type=type(anchor).__name__
            )
        with self._lock:
            existing = self._anchors.get(anchor.anchor_id)
            if existing is not None and existing != anchor:
                raise EvidenceWriteError(
                    "an anchor already exists under this id and differs; storage is write-once",
                    anchor_id=anchor.anchor_id,
                )
            self._anchors[anchor.anchor_id] = anchor
        return f"memory://{anchor.anchor_id}"

    def get(self, anchor_id: str) -> Optional[WormAnchor]:
        """Return a stored anchor, or ``None``."""
        with self._lock:
            return self._anchors.get(anchor_id)

    def list_for_segment(self, segment_id: str) -> Sequence[WormAnchor]:
        """Return every anchor covering a segment, oldest first."""
        with self._lock:
            matches = [
                anchor for anchor in self._anchors.values() if anchor.segment_id == segment_id
            ]
        return sorted(matches, key=lambda anchor: (anchor.first_seq, anchor.sealed_at))


class FilesystemWormAnchorStore:
    """Anchors stored as immutable files under a directory.

    Args:
        root: Directory to store anchors in. Created if absent.
        make_read_only: Remove write permission after writing. Disable only on
            filesystems that reject ``chmod``.

    Raises:
        EvidenceWriteError: If the directory cannot be created.
    """

    __slots__ = ("_root", "_make_read_only", "_lock")

    def __init__(self, root: Path, *, make_read_only: bool = True) -> None:
        self._root = Path(root)
        self._make_read_only = make_read_only
        self._lock = threading.RLock()
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EvidenceWriteError(
                "could not create the anchor directory",
                root=str(root),
                cause=type(exc).__name__,
            ) from exc

    def _path_for(self, anchor_id: str) -> Path:
        """Return the file path for an anchor id.

        The id is encoded rather than used directly: it contains ``:`` and would
        otherwise be both invalid on Windows and a path-traversal opportunity.
        """
        safe = anchor_id.replace(":", "__").replace("/", "_")
        candidate = (self._root / f"{safe}.json").resolve()
        root = self._root.resolve()
        if root not in candidate.parents:
            raise EvidenceWriteError(
                "anchor id resolves outside the anchor directory", anchor_id=anchor_id
            )
        return candidate

    def put(self, anchor: WormAnchor) -> str:
        """Write the anchor once, durably, then make it read-only.

        Raises:
            EvidenceWriteError: If a different anchor already occupies the path,
                or the write cannot be made durable.
        """
        if not isinstance(anchor, WormAnchor):
            raise EvidenceWriteError(
                "put requires a WormAnchor", offending_type=type(anchor).__name__
            )
        path = self._path_for(anchor.anchor_id)
        payload = anchor_to_json(anchor)

        with self._lock:
            if path.exists():
                existing = self.get(anchor.anchor_id)
                if existing != anchor:
                    raise EvidenceWriteError(
                        "an anchor already exists at this path and differs; storage is write-once",
                        anchor_id=anchor.anchor_id,
                        path=str(path),
                    )
                return path.as_uri()

            try:
                # O_EXCL makes creation atomic: a concurrent writer loses the race
                # rather than silently overwriting an existing attestation.
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR
                )
                try:
                    os.write(descriptor, payload.encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._fsync_directory()
                if self._make_read_only:
                    os.chmod(path, stat.S_IRUSR)
            except FileExistsError as exc:
                raise EvidenceWriteError(
                    "anchor already exists; storage is write-once",
                    anchor_id=anchor.anchor_id,
                ) from exc
            except OSError as exc:
                raise EvidenceWriteError(
                    "anchor could not be made durable",
                    anchor_id=anchor.anchor_id,
                    cause=type(exc).__name__,
                    detail=str(exc),
                ) from exc
        return path.as_uri()

    def get(self, anchor_id: str) -> Optional[WormAnchor]:
        """Return a stored anchor, or ``None`` when the file is absent."""
        path = self._path_for(anchor_id)
        if not path.exists():
            return None
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidenceIntegrityError(
                "anchor exists but could not be read",
                anchor_id=anchor_id,
                cause=type(exc).__name__,
            ) from exc
        return anchor_from_json(payload)

    def list_for_segment(self, segment_id: str) -> Sequence[WormAnchor]:
        """Return every anchor covering a segment, oldest first."""
        anchors: List[WormAnchor] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                anchor = anchor_from_json(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise EvidenceIntegrityError(
                    "anchor directory could not be listed",
                    path=str(path),
                    cause=type(exc).__name__,
                ) from exc
            if anchor.segment_id == segment_id:
                anchors.append(anchor)
        return sorted(anchors, key=lambda anchor: (anchor.first_seq, anchor.sealed_at))

    def _fsync_directory(self) -> None:
        """Fsync the containing directory so the new entry survives a crash.

        Writing and fsyncing the file is not enough: the directory entry itself
        can still be lost, leaving a durable file that nothing points to.
        """
        if not hasattr(os, "O_DIRECTORY"):  # Windows has no directory fsync
            return
        descriptor = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _error_code(exc: Exception) -> str:
    """Return the S3/botocore error code for ``exc``, however it was raised.

    Modeled S3 errors (e.g. a missing key) arrive as a dynamically generated
    exception class named after the code (``NoSuchKey``); others arrive as a
    generic ``ClientError`` carrying the code in its response body. Checking
    both means this adapter does not need to import ``botocore`` types at all.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404"})
_CONFLICT_CODES = frozenset({"PreconditionFailed", "412"})


class S3WormAnchorStore:
    """Anchors stored in Amazon S3 with Object Lock in compliance mode.

    This is the production-grade implementation the module docstring points
    to: the write-once guarantee is enforced by the storage system itself
    (S3 Object Lock, compliance mode -- not even the bucket owner's root
    account can shorten or remove the retention period before it expires),
    not by application code that could be bypassed by whatever compromises
    this process.

    The bucket must have both versioning and Object Lock enabled -- Object
    Lock cannot be turned on after bucket creation, so this is a
    deployment-time prerequisite this adapter cannot arrange on your behalf.

    Args:
        bucket: The S3 bucket name.
        prefix: Key prefix under which anchors are stored (e.g.
            ``"anchors/"``).
        retention_days: How long each anchor is locked for after being
            written, counted from the anchor's own ``sealed_at``. Chosen
            independently of the evidence retention period: an anchor must
            outlive the segment it attests to. Defaults to 2555 days (~7
            years), a common regulatory floor.
        retention_mode: ``"COMPLIANCE"`` (default) cannot be shortened or
            removed by anyone, including the AWS account root user, for the
            retention period. ``"GOVERNANCE"`` allows a separately-permissioned
            principal to override it and is deliberately not the default.
        region_name: AWS region. Defaults to the ambient configuration.
        client: A pre-built boto3 S3 client, mainly for injecting a
            configured session or a fake in tests.
        connect_timeout_s: Connection timeout. Bounded, because an unbounded
            wait on the sealing path stalls retention purge behind it.
        read_timeout_s: Read timeout.
        max_attempts: SDK-level retry budget for transient errors.

    Raises:
        WormStoreUnavailableError: If ``boto3`` (the ``worm`` extra) is not
            installed, ``retention_mode`` is invalid, or the client cannot be
            constructed.
    """

    __slots__ = ("_client", "_bucket", "_prefix", "_retention_days", "_retention_mode")

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        retention_days: int = 2555,
        retention_mode: str = "COMPLIANCE",
        region_name: Optional[str] = None,
        client: Optional[Any] = None,
        connect_timeout_s: float = 3.0,
        read_timeout_s: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        if retention_mode not in ("COMPLIANCE", "GOVERNANCE"):
            raise WormStoreUnavailableError(
                "retention_mode must be COMPLIANCE or GOVERNANCE",
                retention_mode=retention_mode,
            )
        self._bucket = bucket
        self._prefix = prefix
        self._retention_days = retention_days
        self._retention_mode = retention_mode
        if client is not None:
            self._client = client
            return
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise WormStoreUnavailableError(
                "the S3 WORM anchor store requires the 'worm' extra",
                remedy="pip install 'glassbox-governance[worm]'",
            ) from exc
        try:
            self._client = boto3.client(
                "s3",
                region_name=region_name,
                config=Config(
                    connect_timeout=connect_timeout_s,
                    read_timeout=read_timeout_s,
                    retries={"max_attempts": max_attempts, "mode": "standard"},
                ),
            )
        except Exception as exc:
            raise WormStoreUnavailableError(
                "could not construct the S3 client",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def _key_for(self, anchor_id: str) -> str:
        """Return the object key for an anchor id, encoded like the filesystem adapter."""
        safe = anchor_id.replace(":", "__").replace("/", "_")
        return f"{self._prefix}{safe}.json"

    def put(self, anchor: WormAnchor) -> str:
        """Store ``anchor``, locked under Object Lock for ``retention_days``.

        Storing the identical anchor twice succeeds idempotently, matching the
        other adapters' contract; storing a *different* anchor under an
        existing id raises.

        Raises:
            EvidenceWriteError: If a different anchor already exists under
                this id, or the write could not be made durable.
        """
        if not isinstance(anchor, WormAnchor):
            raise EvidenceWriteError(
                "put requires a WormAnchor", offending_type=type(anchor).__name__
            )
        key = self._key_for(anchor.anchor_id)
        existing = self.get(anchor.anchor_id)
        if existing is not None:
            if existing != anchor:
                raise EvidenceWriteError(
                    "an anchor already exists under this id and differs; storage is write-once",
                    anchor_id=anchor.anchor_id,
                )
            return f"s3://{self._bucket}/{key}"

        payload = anchor_to_json(anchor)
        retain_until = datetime.fromtimestamp(anchor.sealed_at, tz=timezone.utc) + timedelta(
            days=self._retention_days
        )
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload.encode("utf-8"),
                ContentType="application/json",
                ObjectLockMode=self._retention_mode,
                ObjectLockRetainUntilDate=retain_until,
                # A conditional write: S3 refuses if the key already exists, so
                # a racing writer loses the race rather than silently
                # overwriting an existing attestation -- the same guarantee
                # the filesystem adapter gets from O_EXCL.
                IfNoneMatch="*",
            )
        except Exception as exc:
            if _error_code(exc) in _CONFLICT_CODES:
                existing = self.get(anchor.anchor_id)
                if existing is not None and existing != anchor:
                    raise EvidenceWriteError(
                        "an anchor already exists under this id and differs; "
                        "storage is write-once",
                        anchor_id=anchor.anchor_id,
                    ) from exc
                return f"s3://{self._bucket}/{key}"
            raise EvidenceWriteError(
                "anchor could not be made durable",
                anchor_id=anchor.anchor_id,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc
        return f"s3://{self._bucket}/{key}"

    def get(self, anchor_id: str) -> Optional[WormAnchor]:
        """Return a stored anchor, or ``None`` when the object is absent.

        Raises:
            EvidenceIntegrityError: If the object exists but cannot be read or
                parsed. Absence and corruption are different findings and must
                not be collapsed.
        """
        key = self._key_for(anchor_id)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return None
            raise EvidenceIntegrityError(
                "anchor exists but could not be read",
                anchor_id=anchor_id,
                cause=type(exc).__name__,
            ) from exc
        body = response["Body"].read()
        return anchor_from_json(body.decode("utf-8"))

    def list_for_segment(self, segment_id: str) -> Sequence[WormAnchor]:
        """Return every anchor covering a segment, oldest first.

        Raises:
            EvidenceIntegrityError: If the bucket cannot be listed, or any
                anchor found in it cannot be read or parsed.
        """
        anchors: List[WormAnchor] = []
        continuation: Dict[str, str] = {}
        try:
            while True:
                response = self._client.list_objects_v2(
                    Bucket=self._bucket, Prefix=self._prefix, **continuation
                )
                for item in response.get("Contents", []):
                    obj = self._client.get_object(Bucket=self._bucket, Key=item["Key"])
                    anchor = anchor_from_json(obj["Body"].read().decode("utf-8"))
                    if anchor.segment_id == segment_id:
                        anchors.append(anchor)
                if not response.get("IsTruncated"):
                    break
                continuation = {"ContinuationToken": response["NextContinuationToken"]}
        except EvidenceIntegrityError:
            raise
        except Exception as exc:
            raise EvidenceIntegrityError(
                "anchor bucket could not be listed",
                bucket=self._bucket,
                cause=type(exc).__name__,
            ) from exc
        return sorted(anchors, key=lambda anchor: (anchor.first_seq, anchor.sealed_at))
