#!/usr/bin/env python3
"""Start, restart, or stop copy-server on TrueNAS.

One command from System → Shell or SSH, from wherever you put this folder:

    sudo python3 /mnt/<pool>/copy-server/nas.py

After the first run you can also use:

    sudo /root/copy-server
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

RUNTIME = Path(os.environ.get("COPY_SERVER_RUNTIME", "/root/copy-server-runtime"))
LAUNCHER = Path("/root/copy-server")
PORT = int(os.environ.get("COPY_SERVER_PORT", "8080"))
PID_FILE = RUNTIME / "uvicorn.pid"
DATA_DIR = RUNTIME / "data"
STATE_FILE = DATA_DIR / "state.json"
WEB_LOG = DATA_DIR / "web.log"
RCLONE_BIN = RUNTIME / "rclone"
VENV = RUNTIME / "venv"


def die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ensure_root() -> None:
    if os.geteuid() == 0:
        return
    os.execvp("sudo", ["sudo", "-E", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def app_root() -> Path:
    env = os.environ.get("COPY_SERVER_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    if (here / "app" / "main.py").is_file():
        return here
    die(f"Cannot find copy-server (looked at {here}). Run nas.py from the folder that contains app/.")


def dashboard_url() -> str:
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "<nas-ip>"
    if ip.startswith("127."):
        ip = "<nas-ip>"
    return f"http://{ip}:{PORT}"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def current_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return None
    return pid if pid_alive(pid) else None


def stop() -> None:
    pid = current_pid()
    if not pid:
        print("copy-server is not running")
        PID_FILE.unlink(missing_ok=True)
        return
    os.kill(pid, 15)
    for _ in range(20):
        if not pid_alive(pid):
            break
        time.sleep(0.2)
    if pid_alive(pid):
        os.kill(pid, 9)
    PID_FILE.unlink(missing_ok=True)
    print(f"Stopped PID {pid}")


def status() -> None:
    pid = current_pid()
    if pid:
        print(f"Running PID {pid}")
        print(f"Dashboard {dashboard_url()}")
        print(f"Log {WEB_LOG}")
    else:
        print("Not running")


def show_log() -> None:
    if not WEB_LOG.is_file():
        die(f"No log yet at {WEB_LOG}")
    os.execvp("tail", ["tail", "-n", "80", "-f", str(WEB_LOG)])


def run(cmd: list[str], **kwargs) -> None:
    subprocess.check_call(cmd, **kwargs)


def install_rclone() -> None:
    if RCLONE_BIN.is_file() and os.access(RCLONE_BIN, os.X_OK):
        return
    print("Downloading rclone ...")
    zip_path = RUNTIME / "rclone.zip"
    urllib.request.urlretrieve(
        "https://downloads.rclone.org/rclone-current-linux-amd64.zip",
        zip_path,
    )
    extract = RUNTIME / "rclone-extract"
    if extract.exists():
        shutil.rmtree(extract)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract)
    matches = list(extract.glob("rclone-*-linux-amd64/rclone"))
    if not matches:
        die("rclone binary not found in the zip")
    shutil.copy(matches[0], RCLONE_BIN)
    os.chmod(RCLONE_BIN, 0o755)
    zip_path.unlink(missing_ok=True)
    shutil.rmtree(extract, ignore_errors=True)


def install_python(root: Path) -> Path:
    py = VENV / "bin" / "python3"
    pip = VENV / "bin" / "pip"
    if not py.is_file():
        print("Creating Python virtualenv (no apt) ...")
        if VENV.exists():
            shutil.rmtree(VENV)
        try:
            run([sys.executable, "-m", "venv", "--without-pip", str(VENV)])
        except subprocess.CalledProcessError:
            die("python3 -m venv failed. Do not apt-install packages on TrueNAS.")
    if not pip.is_file():
        get_pip = RUNTIME / "get-pip.py"
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
        run([str(py), str(get_pip)])
    print("Installing Python packages ...")
    run([str(pip), "install", "-r", str(root / "requirements.txt")])
    return VENV / "bin" / "uvicorn"


def clear_stale_errors() -> None:
    if not STATE_FILE.is_file():
        return
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return
    scan = state.setdefault("scan", {})
    copy = state.setdefault("copy", {})
    err = f"{scan.get('error') or ''} {copy.get('error') or ''}".lower()
    if "job not found" in err or "dropped the job" in err:
        scan["error"] = None
        scan["running"] = False
        copy["error"] = None
        copy["jobid"] = None
        copy["status"] = "idle"
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print("Cleared leftover 'job not found' status")


def install_launcher(root: Path) -> None:
    body = (
        "#!/bin/sh\n"
        f'export COPY_SERVER_ROOT="{root}"\n'
        f'exec {sys.executable} "{root / "nas.py"}" "$@"\n'
    )
    LAUNCHER.write_text(body)
    os.chmod(LAUNCHER, 0o755)


def start() -> None:
    root = app_root()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    install_launcher(root)
    install_rclone()
    uvicorn = install_python(root)
    clear_stale_errors()
    if current_pid():
        print("Restarting ...")
        stop()
    env = os.environ.copy()
    env["DATA_DIR"] = str(DATA_DIR)
    env["RCLONE_BIN"] = str(RCLONE_BIN)
    env["PATH"] = f"{RUNTIME}:{env.get('PATH', '')}"
    log = WEB_LOG.open("ab")
    proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=str(root),
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    time.sleep(1)
    if proc.poll() is not None:
        die(f"Dashboard exited immediately. See {WEB_LOG}")
    print(f"Started PID {proc.pid}")
    print(f"Dashboard {dashboard_url()}")
    print("Open that page. If folders are not set yet, the wizard will ask for source and destination.")


def main() -> None:
    ensure_root()
    action = (sys.argv[1] if len(sys.argv) > 1 else "restart").lstrip("-")
    if action in {"start", "restart", "run"}:
        start()
    elif action == "stop":
        stop()
    elif action == "status":
        status()
    elif action == "log":
        show_log()
    else:
        die("Usage: nas.py [restart|stop|status|log]")


if __name__ == "__main__":
    main()
