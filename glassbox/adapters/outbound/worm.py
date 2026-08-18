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
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from glassbox.domain.errors import EvidenceIntegrityError, EvidenceWriteError
from glassbox.domain.evidence import WormAnchor

__all__ = [
    "InMemoryWormAnchorStore",
    "FilesystemWormAnchorStore",
    "anchor_to_json",
    "anchor_from_json",
]


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
