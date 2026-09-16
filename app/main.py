from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings
from app.rclone import RcloneManager
from app import discover, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("copy-server")

settings = Settings()
manager = RcloneManager(settings)
security = HTTPBasic(auto_error=False)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.name == "nt":
        log.warning(
            "Running on Windows. File traffic will pass through this PC. "
            "Deploy the container on TrueNAS (or TerraMaster) for a NAS-to-NAS copy."
        )
    await manager.start_daemon()
    if settings.auto_start:
        try:
            await manager.start_copy(dry_run=False)
            log.info("AUTO_START launched a copy job")
        except Exception as exc:  # noqa: BLE001
            log.error("AUTO_START failed: %s", exc)
    yield
    if manager._monitor_task:
        manager._monitor_task.cancel()
    await manager.stop_daemon()


app = FastAPI(title="Zeus copy-server", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.dashboard_user:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Login required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.dashboard_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


class CopyRequest(BaseModel):
    dry_run: bool = False


class SmbAuth(BaseModel):
    host: str
    user: str = ""
    password: str = ""
    domain: str = "WORKGROUP"
    share: str = ""
    path: str = ""


class SetupRequest(BaseModel):
    source_path: str
    dest_smb_host: str
    dest_smb_share: str
    dest_smb_path: str = ""
    dest_smb_user: str = ""
    dest_smb_pass: str = ""
    dest_smb_domain: str = "WORKGROUP"
    exclude_system: bool = True
    extra_excludes: list[str] | str | None = None
    source_label: str = "This NAS"
    dest_label: str = "Backup NAS"


class ExcludeRequest(BaseModel):
    extra_excludes: list[str] | str = Field(default_factory=list)
    exclude_system: bool | None = None


@app.get("/")
async def index(_: None = Depends(require_auth)) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status(_: None = Depends(require_auth)) -> dict:
    return await manager.snapshot()


@app.get("/api/setup")
async def get_setup(_: None = Depends(require_auth)) -> dict:
    return store.public_setup(settings)


@app.post("/api/setup")
async def save_setup(body: SetupRequest, _: None = Depends(require_auth)) -> dict:
    payload = body.model_dump()
    if not payload.get("dest_smb_pass"):
        payload["dest_smb_pass"] = settings.dest_smb_pass
    payload["source_kind"] = "local"
    payload["dest_kind"] = "smb"
    try:
        return await manager.apply_setup(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/excludes")
async def save_excludes(body: ExcludeRequest, _: None = Depends(require_auth)) -> dict:
    payload: dict = {"extra_excludes": body.extra_excludes}
    if body.exclude_system is not None:
        payload["exclude_system"] = body.exclude_system
    store.save_setup(payload)
    store.apply_to_settings(settings, payload)
    return store.public_setup(settings)


@app.get("/api/disks")
async def disks(_: None = Depends(require_auth)) -> dict:
    return {"disks": discover.local_disks()}


@app.get("/api/folders")
async def folders(path: str, _: None = Depends(require_auth)) -> dict:
    try:
        return discover.list_folders(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Folder not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/lan/smb")
async def lan_smb(_: None = Depends(require_auth)) -> dict:
    return {"hosts": discover.scan_smb_hosts(), "this_nas": discover.local_ipv4s()}


@app.post("/api/smb/shares")
async def smb_shares(body: SmbAuth, _: None = Depends(require_auth)) -> dict:
    password = body.password or settings.dest_smb_pass
    try:
        shares = await manager.list_smb_shares(body.host, body.user, password, body.domain)
        return {"shares": shares}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/smb/folders")
async def smb_folders(body: SmbAuth, _: None = Depends(require_auth)) -> dict:
    password = body.password or settings.dest_smb_pass
    try:
        folders = await manager.list_smb_folders(
            body.host, body.user, password, body.domain, body.share, body.path
        )
        return {"folders": folders, "share": body.share, "path": body.path}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/copy/start")
async def copy_start(body: CopyRequest | None = None, _: None = Depends(require_auth)) -> dict:
    dry_run = body.dry_run if body else False
    try:
        return await manager.start_copy(dry_run=dry_run)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/copy/stop")
async def copy_stop(_: None = Depends(require_auth)) -> dict:
    try:
        return await manager.stop_copy()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/scan/start")
async def scan_start(_: None = Depends(require_auth)) -> dict:
    try:
        return await manager.start_scan()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/test")
async def test_connection(_: None = Depends(require_auth)) -> dict:
    return await manager.test_connection()


@app.get("/api/logs")
async def logs(lines: int = Query(default=200, ge=20, le=2000), _: None = Depends(require_auth)) -> dict:
    return {"text": manager.tail_log(lines)}
