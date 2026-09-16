# copy-server

NAS-to-NAS file mirror with a small web dashboard. It copies one server’s dataset tree onto another (for example TrueNAS → TerraMaster), resumes an unfinished backup by **file size**, and lets you check progress from any browser on the LAN.

**Run the copier on a NAS, not on a Windows/Mac PC.** The PC is only for editing config and opening the dashboard. If file data passes through a workstation, the copy is slower and that machine has to stay on.

```
TrueNAS disks  →  LAN  →  TerraMaster share
     ↑
 dashboard at http://<nas-ip>:8080
 (your laptop only talks to this page)
```

---

## What it does

| Behavior | Detail |
|---|---|
| Copy mode | Adds and updates files. **Never deletes** extras on the destination. |
| Resume | If a dest file already exists and the **size matches**, it is skipped. |
| Incomplete files | Smaller leftover files from a stopped copy are treated as incomplete and recopied. |
| Permissions | Unix owners, groups, and ACLs are **not** copied. Dest files belong to the SMB user that writes them. |
| Timestamps | rclone tries to keep modification times if the dest SMB server allows it. |
| Dashboard | Live speed, ETA, current files, checked vs copied counts, optional size scan. |

Default excludes (not top-level folders you chose to copy):

- `.zfs` snapshots
- recycle bins (`#recycle`, `.recycle`)
- `.DS_Store`, `Thumbs.db`, `._*`
- If `EXCLUDE_SYSTEM=true`: `ix-applications`, `ix-apps`, `.system`, `ix-virt`

Copying from Windows is **blocked on purpose** so this project cannot accidentally pull the whole NAS through a PC.

---

## What you need

- Two NAS boxes on the same LAN, SMB open (port 445) on the destination.
- SSH or **System → Shell** on the NAS that will **run** the job (usually the source TrueNAS).
- A dest SMB user that can write the backup share.
- About 500 MB free on the source NAS under `/root` for rclone + a Python venv.
- Port **8080** free on that NAS (TrueNAS UI uses 80/443; 8080 is normally unused).

You do **not** need TrueNAS Apps / Docker if the pool root is already an SMB share. TrueNAS will refuse Apps on a pool whose **root dataset** is exported over SMB (`Shares should be configured so that they export data contained in child datasets`). That is a TrueNAS rule. This guide’s default method avoids Apps.

---

## 1. Put it on the NAS you copy **from**

Copy this folder onto that NAS (any writable dataset, for example `/mnt/<pool>/Dump/copy-server`). You do **not** have to fill in `.env` first.

## 2. Start the web UI (one command)

On that NAS, **System → Shell** or SSH:

```sh
sudo python3 /mnt/<pool>/Dump/copy-server/nas.py
```

Open `http://<nas-ip>:8080`. After the first success, start/restart with:

```sh
sudo /root/copy-server
```

| Command | What it does |
|---|---|
| `sudo python3 /mnt/<pool>/Dump/copy-server/nas.py` | Start or restart |
| `sudo /root/copy-server` | Same, after the first run |
| `sudo /root/copy-server stop` | Stop the dashboard |
| `sudo /root/copy-server status` | Show if it is running |
| `sudo /root/copy-server log` | Follow the log |

Do **not** run `apt install` on TrueNAS.

## 3. Pick folders in the browser

The first visit opens a wizard:

1. **Source** — local disks/datasets on this NAS, with used/free space. Click into a folder.
2. **Destination** — find other NAS boxes on the LAN (SMB), or type an IP. Enter username/password, list shares, pick a share (and optional subfolder).
3. **Save** — skip `ix-applications` if you want, then open the live copy dashboard.

After that, **Start / resume copy**. Change folders later with **Change folders**.

Credentials are stored on the NAS in `setup.json` under `/root/copy-server-runtime/data/` (or `DATA_DIR`), not typed into `.env`. `.env` is still optional if you prefer files.

---

## 4. Use the dashboard

1. **Test connection** — source path and dest share must both be OK.
2. **Scan sizes** — optional. Walks both trees; can take hours. Gives overall % and remaining. You can skip this and still copy.
3. **Dry run** — optional. Compares only, does not write.
4. **Start / resume copy** — the real job.

A long **checking** phase is expected. Matching sizes are skipped; speed can stay at 0 while the checked-file count climbs.

Close the laptop whenever you want. The job keeps running on the NAS until you click **Stop** on the page or the TrueNAS reboots.

### After a TrueNAS reboot

```sh
sudo /root/copy-server
```

Then open the dashboard and click **Start / resume copy** again. Completed files are skipped by size.

---

## 5. Other install methods

### TrueNAS SCALE Apps / Docker

Use this only if Apps can use a pool whose **root dataset is not** an SMB share.

1. **Apps → Settings → Choose Pool** on a suitable pool.
2. Copy this project to a host path the app can read.
3. **Apps → Discover Apps → ⋮ → Install via YAML**.
4. Point compose at your paths (see `truenas-compose.yml`). In Docker, `SOURCE_KIND=local` and `SOURCE_PATH=/source`, with `/mnt/<pool>` bind-mounted read-only onto `/source`.

Building from a `Dockerfile` needs internet on the NAS once.

### Destination NAS (TerraMaster TOS, etc.)

Traffic is still NAS-to-NAS (pull instead of push):

```env
SOURCE_KIND=smb
SOURCE_SMB_HOST=<truenas-ip>
SOURCE_SMB_SHARE=<share>
SOURCE_SMB_USER=<smb-user>
SOURCE_SMB_PASS=<smb-pass>
DEST_KIND=local
DEST_PATH=/path/on/that/nas/to/the/backup
```

You need an SMB account that can **read** the TrueNAS share. Admin/root on the TrueNAS UI often cannot.

### TrueNAS CORE jail

Install rclone and Python in a jail, mount the pool, `pip install -r requirements.txt`, then:

```sh
cd /path/to/copy-server
DATA_DIR=/var/db/copy-server RCLONE_BIN=$(which rclone) \
  uvicorn app.main:app --host 0.0.0.0 --port 8080
```

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `chmod: Operation not permitted` | File is on an SMB dataset. Use `sudo python3 .../nas.py` (no chmod). Binaries live under `/root`. |
| `Permission denied` on `/root` | `sudo` was not used. Run `sudo python3 /mnt/<pool>/Dump/copy-server/nas.py`. |
| `ensurepip is not available` / `apt install python3.11-venv` | Do **not** apt on TrueNAS. `nas.py` bootstraps pip without apt. |
| `admin is not allowed to run sudo` | **Credentials → Local Users → admin** → allow sudo. |
| Apps: pool root is used by SMB | Don’t use Apps on that pool. Use section 3. |
| TrueNAS SMB login failed | UI admin ≠ SMB user. Run on TrueNAS with `SOURCE_KIND=local`. |
| Dashboard on Windows says copy is blocked | Correct. Use `http://<nas-ip>:8080`. |
| `rclone job/status failed: job not found` on an idle page | Leftover failed scan. `sudo /root/copy-server` clears it and restarts. |
| Connection test dest fails | Wrong `DEST_SMB_SHARE` vs `DEST_SMB_PATH`, bad password, or user cannot write the share. |
| Looks idle / 0 B/s | Still **checking** existing files. Watch the checked count. |

Logs:

```sh
sudo /root/copy-server log
```

---

## 7. Security

- `.env` holds SMB passwords. Do not commit it. It is gitignored.
- The rclone remote-control API binds to `127.0.0.1` only. The dashboard binds to `0.0.0.0:8080` on the NAS.
- On an untrusted LAN, set `DASHBOARD_USER` and `DASHBOARD_PASS`.
- Source is read-only in the Docker compose file (`:ro`). The host method reads `/mnt/<pool>` as root; it does not write back to source.

---

## Layout

```
copy-server/
  app/                 dashboard + rclone controller
  nas.py               one-command start/restart/stop on TrueNAS
  .env.example         copy to .env and edit
  requirements.txt
  Dockerfile           optional Docker image
  docker-compose.yml   optional compose for a friendly pool
  truenas-compose.yml  example SCALE YAML (paths are examples)
```

Runtime on TrueNAS (host method):

- App + `.env`: `/mnt/<pool>/Dump/copy-server`
- rclone, venv, logs, job state: `/root/copy-server-runtime`
