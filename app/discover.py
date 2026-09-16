from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


SKIP_DIR_NAMES = {".zfs", "#recycle", ".recycle", ".windows", "@eaDir"}


def _usage(path: Path) -> dict[str, Any]:
    try:
        disk = os.statvfs(path)
        total = disk.f_frsize * disk.f_blocks
        free = disk.f_frsize * disk.f_bavail
        used = total - free
    except OSError:
        total = used = free = 0
    return {
        "path": str(path),
        "name": path.name or str(path),
        "total": total,
        "used": used,
        "free": free,
        "percent": round((used / total) * 100, 1) if total else 0,
    }


def local_disks() -> list[dict[str, Any]]:
    roots: list[Path] = []
    mnt = Path("/mnt")
    if mnt.is_dir():
        roots.extend(sorted(p for p in mnt.iterdir() if p.is_dir() and not p.name.startswith(".")))
    if not roots:
        for candidate in (Path("/"), Path.home()):
            if candidate.exists():
                roots.append(candidate)
    return [_usage(path) for path in roots]


def list_folders(path: str) -> dict[str, Any]:
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(path)
    entries = []
    try:
        children = sorted(folder.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise PermissionError(str(exc)) from exc
    for child in children:
        if not child.is_dir() or child.name in SKIP_DIR_NAMES:
            continue
        entries.append({"name": child.name, "path": str(child)})
    parent = str(folder.parent) if folder.parent != folder else None
    return {"path": str(folder), "parent": parent, **_usage(folder), "folders": entries}


def local_ipv4s() -> list[str]:
    addresses: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            addresses.append(sock.getsockname()[0])
    except OSError:
        pass
    hostname = socket.gethostname()
    try:
        addresses.extend(socket.gethostbyname_ex(hostname)[2])
    except OSError:
        pass
    unique = []
    for ip in addresses:
        if ip and not ip.startswith("127.") and ip not in unique:
            unique.append(ip)
    return unique


def _port_open(host: str, port: int = 445, timeout: float = 0.25) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def scan_smb_hosts(limit: int = 80) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    targets: list[str] = []
    for ip in local_ipv4s():
        prefix = ".".join(ip.split(".")[:3])
        for last in range(1, 255):
            candidate = f"{prefix}.{last}"
            if candidate != ip and candidate not in seen:
                seen.add(candidate)
                targets.append(candidate)
    with ThreadPoolExecutor(max_workers=64) as pool:
        futures = {pool.submit(_port_open, host): host for host in targets}
        for future in as_completed(futures):
            host = futures[future]
            try:
                if future.result() and len(found) < limit:
                    found.append({"host": host})
            except Exception:
                continue
    found.sort(key=lambda item: tuple(int(part) for part in item["host"].split(".")))
    return found
