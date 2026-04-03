"""
GlassBox — Policy Hot-Reload Watcher  (v1.0.0)
===============================================
Monitors a rules directory for YAML/JSON file changes and reloads policies
into the PolicyEngine at runtime — no pipeline restart required.

Design:
  A background thread polls the rules directory every `poll_interval_s` seconds.
  When a file's modification timestamp changes, or a new file appears, or a file
  is deleted, the watcher reloads the affected policies.

  This is deliberately a polling-based design rather than inotify/FSEvents:
    - Works identically on Linux, macOS, Windows, Docker, Databricks, Fabric
    - No platform-specific dependencies
    - Predictable behaviour in all deployment environments
    - For sub-second reload latency, reduce poll_interval_s (default 5s)

Thread-safety:
  PolicyEngine.register() is protected by threading.RLock (snapshot pattern).
  The watcher thread can safely update policies while the pipeline is serving
  concurrent governance requests.

Usage:
    from glassbox.rules.hot_reload import PolicyHotReloader

    pipeline = GovernancePipeline()

    # Start watching a directory — reloads policies when files change
    watcher = PolicyHotReloader(
        rules_dir       = "rules/",
        policy_engine   = pipeline.policy_engine,
        poll_interval_s = 5,
    )
    watcher.start()

    # Update a YAML file — policies reload within poll_interval_s seconds
    # No pipeline restart. No downtime.

    watcher.stop()

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import os
import threading
import time
import logging
from pathlib import Path
from typing import Callable, Dict, Optional, Set

log = logging.getLogger("glassbox.hot_reload")


class PolicyHotReloader:
    """
    Watches a rules directory and hot-reloads policies when files change.

    Supported file formats: .yaml, .yml, .json

    Behaviour on file events:
      File modified:  reload policies from that file (replaces prior registrations)
      File added:     load new policies from the file
      File deleted:   disable policies that came from that file
      Directory:      scanned recursively (one level deep by default)

    Error handling:
      Syntax errors in YAML/JSON files are logged and the file is skipped —
      existing policies from a prior successful load remain active.
      Parse errors never crash the watcher thread or affect the pipeline.
    """

    def __init__(
        self,
        rules_dir:         str,
        policy_engine,            # glassbox.governance.policy_engine.PolicyEngine
        poll_interval_s:   float = 5.0,
        recursive:         bool  = False,
        on_reload:         Optional[Callable[[str, int], None]] = None,
        on_error:          Optional[Callable[[str, Exception], None]] = None,
    ):
        """
        Args:
            rules_dir:       Directory to watch for .yaml/.yml/.json rule files
            policy_engine:   GovernancePipeline.policy_engine to update
            poll_interval_s: Seconds between directory polls (default 5)
            recursive:       Whether to scan sub-directories (default False)
            on_reload:       Optional callback(file_path, policies_loaded) on success
            on_error:        Optional callback(file_path, exception) on parse error
        """
        self.rules_dir       = Path(rules_dir)
        self.policy_engine   = policy_engine
        self.poll_interval_s = poll_interval_s
        self.recursive       = recursive
        self.on_reload       = on_reload
        self.on_error        = on_error

        self._mtimes: Dict[str, float] = {}     # file_path → last mtime
        self._file_policy_ids: Dict[str, Set[str]] = {}  # file_path → {policy_ids}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self, do_initial_load: bool = True) -> "PolicyHotReloader":
        """
        Start the background watcher thread.

        Args:
            do_initial_load: If True, load all existing rules files immediately
                             before starting the watch loop. Default True.

        Returns self for chaining.
        """
        if do_initial_load:
            self._scan_and_load(initial=True)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="glassbox-hot-reload",
            daemon=True,
        )
        self._thread.start()
        log.info("PolicyHotReloader started: watching %s (poll=%.1fs)",
                 self.rules_dir, self.poll_interval_s)
        return self

    def stop(self, timeout_s: float = 10.0) -> None:
        """Stop the watcher thread gracefully."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)
        log.info("PolicyHotReloader stopped")

    def reload_now(self) -> int:
        """Force an immediate reload scan. Returns number of files reloaded."""
        return self._scan_and_load(initial=False)

    def watched_files(self) -> Dict[str, int]:
        """Return {file_path: policy_count} for all currently tracked files."""
        with self._lock:
            return {
                fp: len(ids)
                for fp, ids in self._file_policy_ids.items()
            }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._scan_and_load(initial=False)
            except Exception as exc:
                log.exception("Hot-reload scan error: %s", exc)
            self._stop_event.wait(timeout=self.poll_interval_s)

    def _scan_and_load(self, initial: bool) -> int:
        """
        Scan the rules directory, detect changes, and reload affected files.
        Returns the number of files reloaded.
        """
        if not self.rules_dir.is_dir():
            return 0

        pattern = "**/*" if self.recursive else "*"
        suffixes = {".yaml", ".yml", ".json"}
        reloaded = 0

        current_files: Set[str] = set()

        for fp in self.rules_dir.glob(pattern):
            if not fp.is_file() or fp.suffix.lower() not in suffixes:
                continue
            str_fp = str(fp.resolve())
            current_files.add(str_fp)

            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue

            with self._lock:
                known_mtime = self._mtimes.get(str_fp)

            if initial or known_mtime is None or mtime != known_mtime:
                n = self._load_file(fp)
                reloaded += 1
                with self._lock:
                    self._mtimes[str_fp] = mtime
                if self.on_reload:
                    try: self.on_reload(str_fp, n)
                    except Exception: pass

        # Handle deleted files
        with self._lock:
            deleted = set(self._file_policy_ids.keys()) - current_files
        for str_fp in deleted:
            self._unload_file(str_fp)
            with self._lock:
                self._mtimes.pop(str_fp, None)
                self._file_policy_ids.pop(str_fp, None)
            log.info("Hot-reload: unloaded deleted file %s", str_fp)

        return reloaded

    def _load_file(self, fp: Path) -> int:
        """Load policies from a single file. Returns number of policies loaded."""
        try:
            from glassbox.rules.rules_engine import RulesLoader
            loader  = RulesLoader()
            policies = loader.load(str(fp))
            loaded_ids: Set[str] = set()

            for policy in policies:
                self.policy_engine.register(policy)
                loaded_ids.add(policy.policy_id)

            with self._lock:
                # Disable policies from prior load that are no longer in file
                prior_ids = self._file_policy_ids.get(str(fp.resolve()), set())
                removed   = prior_ids - loaded_ids
                for pid in removed:
                    try: self.policy_engine.disable(pid)
                    except Exception: pass
                self._file_policy_ids[str(fp.resolve())] = loaded_ids

            log.info("Hot-reload: loaded %d policies from %s", len(policies), fp.name)
            return len(policies)

        except Exception as exc:
            log.warning("Hot-reload: failed to load %s: %s", fp, exc)
            if self.on_error:
                try: self.on_error(str(fp), exc)
                except Exception: pass
            return 0

    def _unload_file(self, str_fp: str) -> None:
        """Disable all policies that were loaded from a deleted file."""
        with self._lock:
            policy_ids = self._file_policy_ids.get(str_fp, set())
        for pid in policy_ids:
            try:
                self.policy_engine.disable(pid)
                log.info("Hot-reload: disabled policy %s (file deleted)", pid)
            except Exception:
                pass
