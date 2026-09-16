#!/usr/bin/env python3
"""Start, restart, or stop nasferry on TrueNAS.

One command from System → Shell or SSH, from wherever you put this folder:

    sudo python3 /mnt/<pool>/nasferry/nas.py

After the first run you can also use:

    sudo /root/nasferry
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path


def _runtime_dir() -> Path:
    env = os.environ.get("NASFERRY_RUNTIME") or os.environ.get("COPY_SERVER_RUNTIME")
    if env:
        return Path(env)
    old = Path("/root/copy-server-runtime")
    new = Path("/root/nasferry-runtime")
    if old.is_dir() and not new.is_dir():
        return old
    return new


RUNTIME = _runtime_dir()
LAUNCHER = Path("/root/nasferry")
OLD_LAUNCHER = Path("/root/copy-server")
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
    env = os.environ.get("NASFERRY_ROOT") or os.environ.get("COPY_SERVER_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    if (here / "app" / "main.py").is_file():
        return here
    die(f"Cannot find nasferry (looked at {here}). Run nas.py from the folder that contains app/.")


def load_env_file(root: Path) -> None:
    path = root / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


RC_PORT = 5572


def listen_port() -> int:
    raw = os.environ.get("COPY_SERVER_PORT") or os.environ.get("PUBLIC_PORT") or "8080"
    try:
        port = int(raw)
    except ValueError:
        die(f"Invalid PUBLIC_PORT: {raw}")
    if not 1 <= port <= 65535:
        die(f"PUBLIC_PORT must be 1–65535, got {port}")
    return port


def port_listening(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")


def pids_listening(port: int) -> list[int]:
    found: list[int] = []
    try:
        out = subprocess.check_output(
            ["ss", "-ltnp", f"sport = :{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        found.extend(int(match) for match in re.findall(r"pid=(\d+)", out))
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        try:
            out = subprocess.check_output(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            found.extend(int(line) for line in out.split() if line.isdigit())
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
            pass
    return list(dict.fromkeys(found))


def matching_pids(*needles: str) -> list[int]:
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    found: list[int] = []
    me = os.getpid()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        cmd = _cmdline(pid)
        if any(needle in cmd for needle in needles):
            found.append(pid)
    return found


def kill_tree(pid: int) -> None:
    if pid <= 1 or pid == os.getpid():
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                return
        for _ in range(20):
            if not pid_alive(pid):
                return
            time.sleep(0.15)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def reap_leftovers(port: int) -> None:
    """Kill this app's previous uvicorn/rclone even if the pid file is stale."""
    victims: list[int] = []
    pid = current_pid()
    if pid:
        victims.append(pid)
    for holder in pids_listening(port):
        if "uvicorn app.main:app" in _cmdline(holder) or holder == pid:
            victims.append(holder)
    for holder in pids_listening(RC_PORT):
        cmd = _cmdline(holder)
        if "rclone" in cmd:
            victims.append(holder)
    victims.extend(
        matching_pids(
            "uvicorn app.main:app",
            "--rc-addr 127.0.0.1:5572",
            "--rc-addr=127.0.0.1:5572",
        )
    )
    seen: set[int] = set()
    for victim in victims:
        if victim in seen or victim == os.getpid():
            continue
        seen.add(victim)
        print(f"Stopping leftover PID {victim}")
        kill_tree(victim)
    PID_FILE.unlink(missing_ok=True)
    for _ in range(25):
        if not port_listening(port) and not port_listening(RC_PORT):
            return
        time.sleep(0.2)


def dashboard_url(port: int | None = None) -> str:
    port = port or listen_port()
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "<nas-ip>"
    if ip.startswith("127."):
        ip = "<nas-ip>"
    return f"http://{ip}:{port}"


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
    root = app_root()
    load_env_file(root)
    reap_leftovers(listen_port())
    print("Stopped")


def status() -> None:
    root = app_root()
    load_env_file(root)
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
    marker = VENV / ".deps"
    req = (root / "requirements.txt").read_text(encoding="utf-8")
    if marker.is_file() and marker.read_text(encoding="utf-8") == req:
        return VENV / "bin" / "uvicorn"
    print("Installing Python packages ...")
    run([str(pip), "install", "-r", str(root / "requirements.txt")])
    marker.write_text(req, encoding="utf-8")
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
        f'export NASFERRY_ROOT="{root}"\n'
        f'export COPY_SERVER_ROOT="{root}"\n'
        f'exec {sys.executable} "{root / "nas.py"}" "$@"\n'
    )
    LAUNCHER.write_text(body)
    os.chmod(LAUNCHER, 0o755)
    OLD_LAUNCHER.write_text("#!/bin/sh\nexec /root/nasferry \"$@\"\n")
    os.chmod(OLD_LAUNCHER, 0o755)


def start() -> None:
    root = app_root()
    load_env_file(root)
    port = listen_port()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    install_launcher(root)
    install_rclone()
    uvicorn = install_python(root)
    clear_stale_errors()
    print("Restarting ..." if current_pid() or port_listening(port) else "Starting ...")
    reap_leftovers(port)
    strangers = pids_listening(port)
    if strangers:
        die(
            f"Port {port} is still in use by PID {', '.join(map(str, strangers))} "
            f"(not nasferry). Set PUBLIC_PORT in {root / '.env'} to a free port."
        )
    env = os.environ.copy()
    env["DATA_DIR"] = str(DATA_DIR)
    env["RCLONE_BIN"] = str(RCLONE_BIN)
    env["PUBLIC_PORT"] = str(port)
    env["COPY_SERVER_PORT"] = str(port)
    env["PATH"] = f"{RUNTIME}:{env.get('PATH', '')}"
    log = WEB_LOG.open("ab")
    proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(root),
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    time.sleep(1.2)
    if proc.poll() is not None:
        tail = ""
        if WEB_LOG.is_file():
            tail = "\n".join(WEB_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        die(f"Dashboard exited immediately. Last log lines:\n{tail}")
    if not port_listening(port):
        print("Warning: process started but the dashboard port is not accepting connections yet.")
    print(f"Started PID {proc.pid}")
    print(f"Dashboard {dashboard_url(port)}")
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
