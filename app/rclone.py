from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import (
    DATA_DIR,
    JOBS_FILE,
    RCLONE_CONF,
    RCLONE_LOG,
    STATE_FILE,
    Settings,
)
from app import discover, store

log = logging.getLogger("nasferry")

RC_WAIT_SECONDS = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_smb(share: str, subpath: str) -> str:
    share = share.strip().strip("/")
    subpath = subpath.strip().strip("/")
    if share and subpath:
        return f"{share}/{subpath}"
    return share or subpath


def rclone_bin() -> str:
    override = os.environ.get("RCLONE_BIN")
    if override:
        return override
    local = Path(__file__).resolve().parents[1] / "rclone"
    if local.is_file():
        return str(local)
    return "rclone"


class RcloneManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None
        self.client = httpx.AsyncClient(
            base_url=settings.rclone_rc,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self.state: dict[str, Any] = self._load_state()
        self._monitor_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._dest_space_cache: dict[str, Any] | None = None
        self._dest_space_at = 0.0
        store.apply_to_settings(self.settings)

    def _load_state(self) -> dict[str, Any]:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Could not parse %s, starting fresh", STATE_FILE)
        return {
            "scan": {
                "source": None,
                "dest": None,
                "running": False,
                "error": None,
            },
            "copy": {
                "jobid": None,
                "group": "copy",
                "status": "idle",
                "started_at": None,
                "finished_at": None,
                "error": None,
                "dry_run": False,
            },
        }

    def _save_state(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _append_job(self, record: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with JOBS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not JOBS_FILE.exists():
            return []
        lines = JOBS_FILE.read_text(encoding="utf-8").splitlines()
        jobs = []
        for line in lines[-limit:]:
            try:
                jobs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        jobs.reverse()
        return jobs

    async def _obscure(self, password: str) -> str:
        if not password:
            return ""
        proc = await asyncio.create_subprocess_exec(
            rclone_bin(),
            "obscure",
            password,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"rclone obscure failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    def _smb_remote_block(
        self,
        name: str,
        host: str,
        user: str,
        password: str,
        domain: str,
    ) -> str:
        lines = [
            f"[{name}]",
            "type = smb",
            f"host = {host}",
            f"user = {user}",
            f"domain = {domain or 'WORKGROUP'}",
            "case_insensitive = true",
        ]
        if password:
            lines.append(f"pass = {password}")
        return "\n".join(lines) + "\n\n"

    async def write_rclone_config(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        chunks: list[str] = []
        settings = self.settings

        if settings.source_kind == "smb" and settings.source_smb_host and settings.source_smb_share:
            chunks.append(
                self._smb_remote_block(
                    "source",
                    settings.source_smb_host,
                    settings.source_smb_user,
                    await self._obscure(settings.source_smb_pass),
                    settings.source_smb_domain,
                )
            )

        if settings.dest_kind == "smb":
            if settings.dest_smb_host and settings.dest_smb_share:
                chunks.append(
                    self._smb_remote_block(
                        "dest",
                        settings.dest_smb_host,
                        settings.dest_smb_user,
                        await self._obscure(settings.dest_smb_pass),
                        settings.dest_smb_domain,
                    )
                )

        RCLONE_CONF.write_text("".join(chunks) or "# waiting for setup\n", encoding="utf-8")

    def source_fs(self) -> str:
        settings = self.settings
        if settings.source_kind == "local":
            return settings.source_path.rstrip("/") or "/"
        path = _join_smb(settings.source_smb_share, settings.source_smb_path)
        return f"source:{path}"

    def dest_fs(self) -> str:
        settings = self.settings
        if settings.dest_kind == "local":
            return settings.dest_path.rstrip("/") or "/"
        path = _join_smb(settings.dest_smb_share, settings.dest_smb_path)
        return f"dest:{path}"

    async def start_daemon(self) -> None:
        await self.write_rclone_config()
        if RCLONE_LOG.exists() and RCLONE_LOG.stat().st_size > 50 * 1024 * 1024:
            RCLONE_LOG.rename(RCLONE_LOG.with_suffix(".log.old"))

        args = [
            rclone_bin(),
            "rcd",
            "--rc-addr",
            "127.0.0.1:5572",
            "--rc-no-auth",
            "--config",
            str(RCLONE_CONF),
            "--log-file",
            str(RCLONE_LOG),
            "--log-level",
            "INFO",
            "--transfers",
            str(self.settings.transfers),
            "--checkers",
            str(self.settings.checkers),
            "--retries",
            "5",
            "--low-level-retries",
            "20",
            "--timeout",
            "10m",
            "--contimeout",
            "1m",
            "--skip-links",
            "--rc-job-expire-duration",
            "168h",
            "--rc-job-expire-interval",
            "1m",
        ]
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._wait_ready()
        self._clear_stale_jobs(save=True, rclone_restarted=True)

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + RC_WAIT_SECONDS
        last_error = "rclone did not start"
        while time.monotonic() < deadline:
            try:
                response = await self.client.post("/core/version")
                if response.status_code < 400:
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            if self.process and self.process.returncode is not None:
                raise RuntimeError(
                    f"rclone exited early with code {self.process.returncode}: {last_error}"
                )
            await asyncio.sleep(0.4)
        raise RuntimeError(last_error)

    async def stop_daemon(self) -> None:
        await self.client.aclose()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=8)
            except asyncio.TimeoutError:
                self.process.kill()

    async def rc(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.post(f"/{method}", json=payload or {})
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw": response.text}
        if response.status_code >= 400:
            error = data.get("error") or data.get("raw") or response.text
            raise RuntimeError(f"rclone {method} failed: {error}")
        return data

    def _copy_config(self, dry_run: bool) -> dict[str, Any]:
        return {
            "SizeOnly": True,
            "IgnoreTimes": False,
            "CreateEmptySrcDirs": True,
            "DryRun": dry_run,
            "Transfers": self.settings.transfers,
            "Checkers": self.settings.checkers,
            "Retries": 5,
            "LowLevelRetries": 20,
            "SkipLinks": True,
        }

    def _filter(self) -> dict[str, Any]:
        return {"ExcludeRule": self.settings.excludes()}

    async def test_connection(self) -> dict[str, Any]:
        result: dict[str, Any] = {"source": None, "dest": None}
        for label, fs in (("source", self.source_fs()), ("dest", self.dest_fs())):
            try:
                info = await self.rc("operations/stat", {"fs": fs, "remote": ""})
                result[label] = {"ok": True, "fs": fs, "item": info.get("item")}
            except Exception:
                try:
                    listing = await self.rc(
                        "operations/list",
                        {"fs": fs, "remote": "", "opt": {"recurse": False}},
                    )
                    count = len(listing.get("list") or [])
                    result[label] = {"ok": True, "fs": fs, "entries": count}
                except Exception as exc:  # noqa: BLE001
                    result[label] = {"ok": False, "fs": fs, "error": str(exc)}
        return result

    async def apply_setup(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.state["copy"]["status"] in {"running", "starting"}:
            raise RuntimeError("Stop the copy before changing source or destination")
        store.save_setup(data)
        store.apply_to_settings(self.settings, data)
        await self.write_rclone_config()
        return store.public_setup(self.settings)

    async def list_smb_shares(self, host: str, user: str, password: str, domain: str) -> list[str]:
        obscured = await self._obscure(password) if password else ""
        spec = f":smb,host={host},user={user},domain={domain or 'WORKGROUP'}"
        if obscured:
            spec += f",pass={obscured}"
        spec += ":"
        proc = await asyncio.create_subprocess_exec(
            rclone_bin(),
            "lsf",
            "--dirs-only",
            spec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "Could not list shares")
        shares = [line.strip().rstrip("/") for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
        return shares

    async def list_smb_folders(
        self,
        host: str,
        user: str,
        password: str,
        domain: str,
        share: str,
        subpath: str = "",
    ) -> list[str]:
        obscured = await self._obscure(password) if password else ""
        rel = "/".join(part for part in (share.strip("/"), subpath.strip("/")) if part)
        spec = f":smb,host={host},user={user},domain={domain or 'WORKGROUP'}"
        if obscured:
            spec += f",pass={obscured}"
        spec += f":{rel}"
        proc = await asyncio.create_subprocess_exec(
            rclone_bin(),
            "lsf",
            "--dirs-only",
            spec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "Could not list folders")
        return [line.strip().rstrip("/") for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]

    async def browse_smb(self, share: str, subpath: str = "") -> list[dict[str, str]]:
        fs = f"dest:{_join_smb(share, '')}"
        remote = subpath.strip().strip("/")
        listing = await self.rc(
            "operations/list",
            {"fs": fs, "remote": remote, "opt": {"recurse": False, "dirsOnly": True}},
        )
        items = []
        for entry in listing.get("list") or []:
            items.append({"name": entry.get("Name") or entry.get("Path"), "path": entry.get("Path") or entry.get("Name")})
        return items

    def _is_missing_job_error(self, message: str | None) -> bool:
        text = (message or "").lower()
        return "job not found" in text or "dropped the job" in text

    def _is_stop_error(self, message: str | None) -> bool:
        text = (message or "").lower()
        return any(
            needle in text
            for needle in (
                "context canceled",
                "context cancelled",
                "job was aborted",
                "job stopped",
                "received abort",
            )
        )

    def _clear_stale_jobs(self, save: bool = False, *, rclone_restarted: bool = False) -> None:
        changed = False
        scan_err = self.state["scan"].get("error")
        copy_err = self.state["copy"].get("error")
        if self._is_missing_job_error(scan_err) or self._is_stop_error(scan_err):
            self.state["scan"]["error"] = None
            self.state["scan"]["running"] = False
            changed = True
        if self._is_missing_job_error(copy_err) or self._is_stop_error(copy_err):
            self.state["copy"]["error"] = None
            if self.state["copy"]["status"] == "error":
                self.state["copy"]["status"] = "idle"
            changed = True
        if rclone_restarted and self.state["copy"]["status"] in {"running", "starting", "stopping", "error"}:
            self.state["copy"]["status"] = "idle"
            self.state["copy"]["jobid"] = None
            changed = True
        if rclone_restarted:
            self.state["scan"]["running"] = False
            changed = True
        if changed and save:
            self._save_state()

    def _refuse_if_through_this_host(self) -> None:
        allow = os.environ.get("ALLOW_THROUGH_HOST", "").lower() in {"1", "true", "yes"}
        if allow:
            return
        if os.name == "nt":
            raise RuntimeError(
                "Refusing to copy from Windows. File data would pass through this PC. "
                "Install nasferry on TrueNAS SCALE so it reads the pool locally and writes to the other NAS over the LAN."
            )

    async def start_copy(self, dry_run: bool = False) -> dict[str, Any]:
        self._refuse_if_through_this_host()
        if not self.settings.setup_complete():
            raise RuntimeError("Pick a source folder and destination share in Setup first.")
        async with self._lock:
            if self.state["copy"]["status"] in {"running", "starting"}:
                raise RuntimeError("A copy is already running")
            self.state["copy"]["status"] = "starting"
            self.state["copy"]["error"] = None
            self.state["copy"]["dry_run"] = dry_run
            self.state["copy"]["started_at"] = _now()
            self.state["copy"]["finished_at"] = None
            self._save_state()

        try:
            await self.rc("core/stats-reset", {"group": "copy"})
        except Exception:  # noqa: BLE001 — first run has no group yet
            pass

        payload = {
            "srcFs": self.source_fs(),
            "dstFs": self.dest_fs(),
            "_async": True,
            "_group": "copy",
            "_config": self._copy_config(dry_run),
            "_filter": self._filter(),
        }
        job = await self.rc("sync/copy", payload)
        jobid = job.get("jobid")
        async with self._lock:
            self.state["copy"]["jobid"] = jobid
            self.state["copy"]["status"] = "running"
            self._save_state()
        self._ensure_monitor()
        return {"jobid": jobid, "dry_run": dry_run}

    async def stop_copy(self) -> dict[str, Any]:
        jobid = self.state["copy"].get("jobid")
        if not jobid:
            return {"stopped": False, "reason": "no active job"}
        try:
            await self.rc("job/stop", {"jobid": jobid})
        except Exception as exc:  # noqa: BLE001
            try:
                await self.rc("job/stopgroup", {"group": "copy"})
            except Exception:
                raise RuntimeError(str(exc)) from exc
        async with self._lock:
            self.state["copy"]["status"] = "stopping"
            self._save_state()
        return {"stopped": True, "jobid": jobid}

    async def start_scan(self) -> dict[str, Any]:
        async with self._lock:
            if self.state["scan"]["running"]:
                raise RuntimeError("A size scan is already running")
            self.state["scan"]["running"] = True
            self.state["scan"]["error"] = None
            self._save_state()
        asyncio.create_task(self._run_scan(), name="size-scan")
        return {"started": True}

    async def _run_scan(self) -> None:
        try:
            source_job = await self.rc(
                "operations/size",
                {
                    "fs": self.source_fs(),
                    "_async": True,
                    "_group": "scan-source",
                    "_filter": self._filter(),
                    "_config": {"SkipLinks": True},
                },
            )
            dest_job = await self.rc(
                "operations/size",
                {
                    "fs": self.dest_fs(),
                    "_async": True,
                    "_group": "scan-dest",
                    "_filter": self._filter(),
                    "_config": {"SkipLinks": True},
                },
            )
            source, dest = await asyncio.gather(
                self._wait_job(source_job["jobid"]),
                self._wait_job(dest_job["jobid"]),
            )
            async with self._lock:
                self.state["scan"]["source"] = {
                    "bytes": source.get("bytes", 0),
                    "count": source.get("count", 0),
                    "at": _now(),
                }
                self.state["scan"]["dest"] = {
                    "bytes": dest.get("bytes", 0),
                    "count": dest.get("count", 0),
                    "at": _now(),
                }
                self.state["scan"]["running"] = False
                self._save_state()
        except Exception as exc:  # noqa: BLE001
            log.exception("Size scan failed")
            async with self._lock:
                self.state["scan"]["running"] = False
                self.state["scan"]["error"] = str(exc)
                self._save_state()

    async def _wait_job(self, jobid: int, poll: float = 2.0) -> dict[str, Any]:
        while True:
            try:
                status = await self.rc("job/status", {"jobid": jobid})
            except RuntimeError as exc:
                if "job not found" in str(exc).lower():
                    raise RuntimeError(
                        "rclone dropped the job before we could read the result. "
                        "Restart nasferry and run Scan again (ix-applications is now skipped)."
                    ) from exc
                raise
            if status.get("finished"):
                if not status.get("success"):
                    raise RuntimeError(status.get("error") or f"job {jobid} failed")
                return status.get("output") or {}
            await asyncio.sleep(poll)

    def _ensure_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_copy(), name="copy-monitor")

    async def _monitor_copy(self) -> None:
        jobid = self.state["copy"].get("jobid")
        if not jobid:
            return
        try:
            while True:
                status = await self.rc("job/status", {"jobid": jobid})
                if status.get("finished"):
                    success = bool(status.get("success"))
                    error = status.get("error") or None
                    stats = await self._stats()
                    record = {
                        "jobid": jobid,
                        "dry_run": self.state["copy"].get("dry_run"),
                        "started_at": self.state["copy"].get("started_at"),
                        "finished_at": _now(),
                        "success": success,
                        "error": error,
                        "bytes": stats.get("bytes"),
                        "checks": stats.get("checks"),
                        "transfers": stats.get("transfers"),
                        "errors": stats.get("errors"),
                    }
                    self._append_job(record)
                    stopped = self._is_stop_error(error)
                    async with self._lock:
                        self.state["copy"]["status"] = "idle" if success or stopped else "error"
                        self.state["copy"]["finished_at"] = record["finished_at"]
                        self.state["copy"]["error"] = None if success or stopped else error
                        self.state["copy"]["jobid"] = None
                        self._save_state()
                    return
                await asyncio.sleep(2)
        except Exception as exc:  # noqa: BLE001
            log.exception("Copy monitor failed")
            async with self._lock:
                self.state["copy"]["status"] = "error"
                self.state["copy"]["error"] = str(exc)
                self._save_state()

    async def _stats(self) -> dict[str, Any]:
        try:
            return await self.rc("core/stats", {"group": "copy"})
        except Exception:  # noqa: BLE001
            return {}

    async def dest_space(self) -> dict[str, Any] | None:
        if self.settings.dest_kind != "smb" or not self.settings.dest_smb_share:
            if self.settings.dest_kind == "local" and self.settings.dest_path:
                try:
                    return discover._usage(Path(self.settings.dest_path))
                except OSError:
                    return None
            return None
        now = time.monotonic()
        if self._dest_space_cache and now - self._dest_space_at < 45:
            return self._dest_space_cache
        try:
            about = await self.rc("operations/about", {"fs": self.dest_fs()})
        except Exception:
            return self._dest_space_cache
        total = int(about.get("total") or 0)
        used = int(about.get("used") or 0)
        free = about.get("free")
        free = int(free) if free is not None else max(total - used, 0)
        if not total and (used or free):
            total = used + free
        if not used and total and free:
            used = max(total - free, 0)
        if not total and not used and not free:
            return self._dest_space_cache
        info = {
            "name": f"{self.settings.dest_smb_host}/{self.settings.dest_smb_share}",
            "path": self.dest_fs(),
            "total": total,
            "used": used,
            "free": free,
            "percent": round((used / total) * 100, 1) if total else 0,
        }
        self._dest_space_cache = info
        self._dest_space_at = now
        return info

    async def snapshot(self) -> dict[str, Any]:
        stats = {}
        job = None
        self._clear_stale_jobs(save=True)
        jobid = self.state["copy"].get("jobid")
        if jobid and self.state["copy"]["status"] in {"running", "stopping", "starting"}:
            try:
                stats = await self._stats()
                job = await self.rc("job/status", {"jobid": jobid})
            except Exception as exc:  # noqa: BLE001
                if self._is_missing_job_error(str(exc)):
                    async with self._lock:
                        self.state["copy"]["jobid"] = None
                        self.state["copy"]["status"] = "idle"
                        self.state["copy"]["error"] = None
                        self._save_state()
                    stats = {}
                    job = None
                else:
                    stats = {"error": str(exc)}
            if job and job.get("finished") and self.state["copy"]["status"] == "running":
                self._ensure_monitor()

        source_scan = self.state["scan"].get("source") or {}
        dest_scan = self.state["scan"].get("dest") or {}
        session_bytes = int(stats.get("bytes") or 0)
        source_bytes = int(source_scan.get("bytes") or 0)
        dest_bytes = int(dest_scan.get("bytes") or 0)
        have_dest_scan = bool(dest_scan)
        scan_at = dest_scan.get("at")
        copy_started = self.state["copy"].get("started_at")
        scan_includes_session = bool(
            have_dest_scan and copy_started and scan_at and scan_at >= copy_started
        )
        if have_dest_scan:
            dest_estimate = dest_bytes if scan_includes_session else dest_bytes + session_bytes
        else:
            dest_estimate = session_bytes
        remaining = (
            max(source_bytes - dest_estimate, 0)
            if source_bytes and have_dest_scan
            else None
        )

        transferring = stats.get("transferring") or []
        checking = stats.get("checking") or []
        copy_status = self.state["copy"]["status"]
        if copy_status == "running" and transferring:
            activity = "copying"
        elif copy_status == "running" and (checking or (stats.get("checks") or 0) > 0):
            activity = "checking"
        elif self.state["scan"]["running"]:
            activity = "scanning"
        else:
            activity = copy_status

        source_disk = None
        if self.settings.source_kind == "local" and self.settings.source_path:
            try:
                source_disk = discover._usage(Path(self.settings.source_path))
            except (OSError, FileNotFoundError):
                source_disk = None
        dest_disk = await self.dest_space()

        return {
            "source_fs": self.source_fs(),
            "dest_fs": self.dest_fs(),
            "source_label": self.settings.source_label,
            "dest_label": self.settings.dest_label,
            "windows_host": os.name == "nt",
            "traffic_path": (
                "this Windows PC (blocked)"
                if os.name == "nt"
                else "This NAS reads local disks and writes to the backup NAS over the LAN"
            ),
            "excludes": self.settings.excludes(),
            "transfers": self.settings.transfers,
            "checkers": self.settings.checkers,
            "copy": self.state["copy"],
            "scan": self.state["scan"],
            "activity": activity,
            "stats": stats,
            "job": job,
            "totals": {
                "source_bytes": source_bytes or None,
                "source_files": source_scan.get("count"),
                "dest_bytes": dest_bytes or None,
                "dest_files": dest_scan.get("count"),
                "dest_bytes_estimate": dest_estimate if have_dest_scan else None,
                "remaining_bytes": remaining,
                "session_bytes": session_bytes,
            },
            "history": self.recent_jobs(),
            "setup": store.public_setup(self.settings),
            "source_disk": source_disk,
            "dest_disk": dest_disk,
        }

    def tail_log(self, lines: int = 200) -> str:
        path = Path(RCLONE_LOG)
        if not path.exists():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(size - 256_000, 0))
            text = handle.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
