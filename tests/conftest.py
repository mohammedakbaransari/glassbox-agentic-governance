"""Pytest-local temp helpers.

Some Windows sandboxed shells create stdlib tempfile directories with ACLs that
the same Python process cannot write into. Tests need writable temp paths more
than they need OS-global temp placement, so keep them under the repository.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid


_TEMP_ROOT = os.path.join(os.getcwd(), ".pytest-tmp")


def _ensure_root() -> str:
    os.makedirs(_TEMP_ROOT, exist_ok=True)
    return _TEMP_ROOT


def _temp_path(suffix: str = "", prefix: str = "tmp", dir: str | None = None) -> str:
    root = dir or _ensure_root()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, f"{prefix}{uuid.uuid4().hex}{suffix}")


class _RepoTemporaryDirectory:
    def __init__(self, suffix: str | None = None, prefix: str | None = None, dir: str | None = None):
        self.name = _temp_path(suffix or "", prefix or "tmp", dir)
        os.makedirs(self.name, exist_ok=False)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)


class _RepoNamedTemporaryFile:
    def __init__(
        self,
        mode: str = "w+b",
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | None = None,
        delete: bool = True,
        **kwargs,
    ):
        self.name = _temp_path(suffix or "", prefix or "tmp", dir)
        self._delete = delete
        self._file = open(self.name, mode, **kwargs)

    def __getattr__(self, name):
        return getattr(self._file, name)

    def __enter__(self):
        self._file.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
        if self._delete:
            try:
                os.unlink(self.name)
            except OSError:
                pass


def _repo_mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> str:
    path = _temp_path(suffix or "", prefix or "tmp", dir)
    os.makedirs(path, exist_ok=False)
    return path


def pytest_configure(config):
    tempfile.TemporaryDirectory = _RepoTemporaryDirectory
    tempfile.NamedTemporaryFile = _RepoNamedTemporaryFile
    tempfile.mkdtemp = _repo_mkdtemp
