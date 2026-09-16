# copy-server

Copy one NAS onto another over the LAN, with a browser page for progress. Run it on the NAS you copy **from** (usually TrueNAS SCALE). Do not run it on a PC — that would pull every file through the PC.

It **adds and updates** files. It never deletes extras on the destination. Files that already match by **size** are skipped, so you can stop and resume.

## Install on TrueNAS SCALE

1. Put this folder anywhere writable on the NAS you copy from, for example `/mnt/<pool>/copy-server`. Avoid `ix-applications`.

2. **System → Shell** or SSH (do **not** `apt install` anything):

   ```sh
   sudo python3 /mnt/<pool>/copy-server/nas.py
   ```

   Use the real path you chose. After the first run, `sudo /root/copy-server` remembers it.

3. Open `http://<nas-ip>:8080`. Pick the local folder, then the other NAS share. **Start / resume copy**.

That is the whole install. Folders and passwords are saved on the NAS (`/root/copy-server-runtime`), not in this repo.

After the first run:

```sh
sudo /root/copy-server          # start or restart
sudo /root/copy-server stop
sudo /root/copy-server status
sudo /root/copy-server log
```

After a TrueNAS reboot, run `sudo /root/copy-server` again, then **Start / resume copy** in the page. Finished files are skipped.

## Using the page

- **Skip folders** — names match anywhere in the tree (`CacheClip` is skipped by default).
- **Scan sizes** — optional, and can take hours. You can copy without it.
- A long **checking** phase with 0 B/s is normal. Matching sizes are skipped.

## What you need

- Two NAS boxes on the same LAN; SMB (port 445) on the destination.
- An SMB user that can **write** the backup share (TrueNAS UI `admin` is often not an SMB user).
- About 500 MB free under `/root` on TrueNAS (rclone + Python venv).
- Port **8080** free on that NAS.

Skip TrueNAS Apps / Docker. If the pool root is already an SMB share, Apps will refuse that pool.

## Notes

- Owners, groups, and ACLs are not copied. Destination files belong to the SMB writer.
- The dashboard is open on your LAN. To lock it, copy `.env.example` to `.env` on the NAS and set `DASHBOARD_USER` / `DASHBOARD_PASS`, then restart.
- Anyone with sudo on the TrueNAS can see dest credentials in `/root/copy-server-runtime/data/setup.json`.

## If something fails

| What you see | What to do |
|---|---|
| `chmod: Operation not permitted` | You are on an SMB dataset. Use `sudo python3 .../nas.py` (do not chmod the files). |
| `Permission denied` on `/root` | Use `sudo`. |
| `ensurepip` / `apt install ...venv` | Do not apt on TrueNAS. `nas.py` installs pip itself. |
| `admin is not allowed to run sudo` | Local Users → admin → allow sudo. |
| Dest connection fails | Wrong share/path, or the SMB user cannot write. |
| Looks idle / 0 B/s | Still checking. Watch the checked count. |
