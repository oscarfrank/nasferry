"""Probe TrueNAS and TerraMaster SMB from this PC. Does not print passwords."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def tcp(host: str, port: int, timeout: float = 4.0) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def main() -> int:
    env = load_env(ENV_PATH)
    source_host = env.get("SOURCE_SMB_HOST", "")
    dest_host = env.get("DEST_SMB_HOST", "")
    print("Loaded .env (secrets not shown)")
    print(f"SOURCE_KIND={env.get('SOURCE_KIND')}")
    print(f"SOURCE_HOST_PATH={env.get('SOURCE_HOST_PATH')}")
    print(f"SOURCE_PATH={env.get('SOURCE_PATH')}")
    print(f"SOURCE_SMB_HOST={source_host}")
    print(f"SOURCE_SMB_SHARE={env.get('SOURCE_SMB_SHARE')!r}")
    print(f"SOURCE_SMB_PATH={env.get('SOURCE_SMB_PATH')!r}")
    print(f"SOURCE_SMB_USER={env.get('SOURCE_SMB_USER')}")
    print(f"DEST_KIND={env.get('DEST_KIND')}")
    print(f"DEST_SMB_HOST={dest_host}")
    print(f"DEST_SMB_SHARE={env.get('DEST_SMB_SHARE')!r}")
    print(f"DEST_SMB_PATH={env.get('DEST_SMB_PATH')!r}")
    print(f"DEST_SMB_USER={env.get('DEST_SMB_USER')}")
    print()

    for host, label in ((source_host, "TrueNAS"), (dest_host, "TerraMaster")):
        print(f"== ports on {label} {host} ==")
        for port in (445, 139, 22, 80, 443, 8080):
            print(f"  {port}: {'open' if tcp(host, port) else 'closed'}")
        print()

    try:
        import smbclient
        from smbprotocol.exceptions import SMBException
    except ImportError:
        print("smbprotocol is not installed yet")
        return 2

    def list_server(label: str, host: str, user: str, password: str, domain: str) -> list[str]:
        username = user if not domain else f"{domain}\\{user}"
        print(f"== SMB login {label} as {user}@{host} ==")
        try:
            smbclient.register_session(host, username=username, password=password)
            shares = []
            for entry in smbclient.list_shares(host):
                name = getattr(entry, "name", None) or str(entry)
                special = bool(getattr(entry, "special", False))
                if name.endswith("$") or special:
                    continue
                shares.append(name)
            print(f"  shares: {shares or '(none visible)'}")
            return shares
        except Exception as exc:  # noqa: BLE001
            print(f"  LOGIN/LIST FAILED: {type(exc).__name__}: {exc}")
            return []

    source_shares = list_server(
        "TrueNAS",
        source_host,
        env.get("SOURCE_SMB_USER", ""),
        env.get("SOURCE_SMB_PASS", ""),
        env.get("SOURCE_SMB_DOMAIN", "WORKGROUP"),
    )
    dest_shares = list_server(
        "TerraMaster",
        dest_host,
        env.get("DEST_SMB_USER", ""),
        env.get("DEST_SMB_PASS", ""),
        env.get("DEST_SMB_DOMAIN", "WORKGROUP"),
    )

    def peek(label: str, host: str, user: str, password: str, share: str, subpath: str = "") -> None:
        path = f"\\\\{host}\\{share}"
        if subpath:
            path = path + "\\" + subpath.replace("/", "\\")
        print(f"== listing {label}: {share}/{subpath or '.'} ==")
        try:
            names = smbclient.listdir(path)
        except Exception as exc:  # noqa: BLE001
            print(f"  LIST FAILED: {type(exc).__name__}: {exc}")
            return
        names = sorted(names)
        print(f"  {len(names)} entries (first 40):")
        for name in names[:40]:
            full = path + "\\" + name
            kind = "dir?"
            size = ""
            try:
                stat = smbclient.stat(full)
                is_dir = bool(stat.st_file_attributes & 0x10) if hasattr(stat, "st_file_attributes") else None
                if is_dir is None:
                    try:
                        smbclient.listdir(full)
                        is_dir = True
                    except Exception:
                        is_dir = False
                kind = "dir" if is_dir else "file"
                if not is_dir:
                    size = f"  {stat.st_size} bytes"
            except Exception:
                pass
            print(f"    [{kind}] {name}{size}")
        if len(names) > 40:
            print(f"    … {len(names) - 40} more")

    print()
    for share in source_shares:
        peek("TrueNAS", source_host, env["SOURCE_SMB_USER"], env["SOURCE_SMB_PASS"], share)
    print()
    dest_path = env.get("DEST_SMB_PATH", "").strip()
    for share in dest_shares:
        peek("TerraMaster", dest_host, env["DEST_SMB_USER"], env["DEST_SMB_PASS"], share)
        if dest_path and dest_path.lower() != share.lower():
            peek("TerraMaster", dest_host, env["DEST_SMB_USER"], env["DEST_SMB_PASS"], share, dest_path)
        # If a share is named zeus, also peek it (already covered by loop)

    return 0


if __name__ == "__main__":
    sys.exit(main())
