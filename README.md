# nasferry

Copy one NAS onto another over your LAN. You run **one command** on the NAS you copy from (TrueNAS SCALE). Then you open a webpage, pick folders, and watch it go.

Your laptop only talks to that page. **Files never pass through the PC.** Close the lid whenever you want.

It adds and updates files. It **never deletes** extras on the destination. If a file is already there and the **size matches**, it is skipped — so you can stop, reboot, and resume.

## Install (TrueNAS SCALE)

1. Clone or copy this folder onto the NAS you copy **from**. Any writable dataset is fine (not `ix-applications`):

   ```sh
   git clone https://github.com/oscarfrank/nasferry.git /mnt/<pool>/nasferry
   ```

2. In **System → Shell** or SSH — do **not** `apt install` anything:

   ```sh
   sudo python3 /mnt/<pool>/nasferry/nas.py
   ```

3. Open `http://<nas-ip>:8080`. Pick a folder on this NAS, then a share on the other NAS. Hit **Start / resume copy**.

That’s it. After the first run:

```sh
sudo /root/nasferry          # start or restart
sudo /root/nasferry stop
sudo /root/nasferry status
sudo /root/nasferry log
```

TrueNAS reboot? Run `sudo /root/nasferry` again, then **Start / resume copy**. Finished files are skipped.

## Using the page

The gear (top right) is Settings: change source/destination, and skip folder names anywhere in the tree (`CacheClip` is already skipped).

**Scan sizes** is optional and can take hours. A long **checking** phase at 0 B/s is normal — matching sizes are skipped.

## What you need

- Two NAS boxes on the same LAN; SMB (port 445) on the destination
- An SMB user that can **write** the backup share (TrueNAS UI `admin` is often **not** an SMB user)
- About 500 MB free under `/root` (rclone + a Python venv)
- Port 8080 free, or set `PUBLIC_PORT` in `.env`

Skip TrueNAS Apps / Docker. If the pool root is already an SMB share, Apps will refuse that pool.

## Notes

- Owners, groups, and ACLs are not copied. Destination files belong to the SMB writer.
- The page is open on your LAN. To lock it, copy `.env.example` to `.env`, set `DASHBOARD_USER` / `DASHBOARD_PASS`, restart.
- Dest passwords live on the NAS at `/root/nasferry-runtime/data/setup.json`, not in this repo.

## If something fails

| What you see | What to do |
|---|---|
| `chmod: Operation not permitted` | Files sit on an SMB dataset. Use `sudo python3 .../nas.py` (don’t chmod them). |
| `Permission denied` on `/root` | Use `sudo`. |
| `ensurepip` / `apt install ...venv` | Do not apt on TrueNAS. `nas.py` installs pip itself. |
| `admin is not allowed to run sudo` | Local Users → admin → allow sudo. |
| Dest connection fails | Wrong share/path, or that SMB user cannot write. |
| Looks idle / 0 B/s | Still checking. Watch the checked count. |
| Port already in use / page dead after restart | Copy the new `nas.py` and run it again. Restart now kills leftover uvicorn/rclone instead of abandoning the port. |
