const CIRCUMFERENCE = 2 * Math.PI * 52;

function $(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function bytes(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units[i]}`;
}

function duration(seconds) {
  if (seconds == null || seconds < 0) return "—";
  const s = Math.round(Number(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${rest}s`;
  return `${rest}s`;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
      : detail || data.error || response.statusText;
    throw new Error(message);
  }
  return data;
}

function setRing(percent) {
  const ring = $("ring");
  const offset = CIRCUMFERENCE * (1 - Math.min(Math.max(percent, 0), 100) / 100);
  ring.style.strokeDasharray = `${CIRCUMFERENCE}`;
  ring.style.strokeDashoffset = `${offset}`;
  $("percent").textContent = Number.isFinite(percent) ? `${Math.round(percent)}%` : "—";
}

function renderFiles(stats) {
  const body = $("files").querySelector("tbody");
  const rows = stats.transferring || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="3" class="empty">No active transfers</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map((file) => {
      const pct = file.percentage == null ? "—" : `${file.percentage}%`;
      return `<tr>
        <td>${file.name || ""}</td>
        <td>${pct} · ${bytes(file.bytes)} / ${bytes(file.size)}</td>
        <td>${bytes(file.speedAvg || file.speed)}/s</td>
      </tr>`;
    })
    .join("");
}

function renderHistory(items) {
  const body = $("history").querySelector("tbody");
  if (!items || !items.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">No runs yet</td></tr>`;
    return;
  }
  body.innerHTML = items
    .map((job) => `<tr>
      <td>${fmtTime(job.started_at)}</td>
      <td>${job.success ? (job.dry_run ? "dry run" : "ok") : job.error || "failed"}</td>
      <td>${bytes(job.bytes)}</td>
      <td>${job.checks ?? "—"}</td>
      <td>${job.errors ?? "—"}</td>
    </tr>`)
    .join("");
}

function fillSpace(disk, ids) {
  const totalEl = $(ids.total);
  const usedEl = $(ids.used);
  const freeEl = $(ids.free);
  const barEl = $(ids.bar);
  const pctEl = $(ids.pct);
  if (!disk || !(disk.total || disk.used || disk.free)) {
    totalEl.textContent = "—";
    usedEl.textContent = "—";
    freeEl.textContent = "—";
    barEl.style.width = "0%";
    pctEl.textContent = "Not available yet";
    return;
  }
  const total = Number(disk.total) || (Number(disk.used) + Number(disk.free)) || 0;
  const used = Number(disk.used) || Math.max(total - Number(disk.free || 0), 0);
  const free = disk.free != null ? Number(disk.free) : Math.max(total - used, 0);
  const percent = total ? Math.min((used / total) * 100, 100) : 0;
  totalEl.textContent = bytes(total);
  usedEl.textContent = bytes(used);
  freeEl.textContent = bytes(free);
  barEl.style.width = `${percent}%`;
  pctEl.textContent = `${percent.toFixed(percent >= 10 ? 0 : 1)}% of the volume is used`;
}

function renderSpace(data) {
  fillSpace(data.source_disk, {
    total: "src-total",
    used: "src-used",
    free: "src-free",
    bar: "src-space-bar",
    pct: "src-space-pct",
  });
  fillSpace(data.dest_disk, {
    total: "dst-total",
    used: "dst-used",
    free: "dst-free",
    bar: "dst-space-bar",
    pct: "dst-space-pct",
  });
  if ($("src-space-name")) {
    $("src-space-name").textContent = data.source_fs || data.source_label || "This NAS";
  }
  if ($("dst-space-name")) {
    $("dst-space-name").textContent = data.dest_fs || data.dest_label || "Backup NAS";
  }
  const dst = data.dest_disk;
  const remaining = data.totals && data.totals.remaining_bytes;
  if (dst && dst.free && remaining != null) {
    $("space-fit").textContent =
      remaining <= dst.free
        ? `Backup NAS has ${bytes(dst.free)} free; about ${bytes(remaining)} still to copy.`
        : `Backup NAS has ${bytes(dst.free)} free, but about ${bytes(remaining)} is still to copy. It may not fit.`;
  } else if (dst && dst.free) {
    $("space-fit").textContent = `Backup NAS has ${bytes(dst.free)} free. Scan sizes to compare with what is left to copy.`;
  } else {
    $("space-fit").textContent = "Free space on the backup NAS is read from the destination share once it is connected.";
  }
}

function activityHint(data) {
  if (data.activity === "checking") {
    return "Comparing files already on the backup NAS. Matching sizes are skipped; this can look idle while checks climb.";
  }
  if (data.activity === "copying") {
    return "Copying missing or incomplete files over the LAN.";
  }
  if (data.activity === "scanning") {
    return "Walking both trees to measure source size vs what is already on the backup NAS. This can take a long time on a full NAS.";
  }
  if (data.activity === "stopping") {
    return "Stop requested. rclone will finish the current file, then halt.";
  }
  if (data.activity === "error") {
    if (/context cancel+ed/i.test(data.copy.error || "")) {
      return "Last copy was stopped. Incomplete files are recopied on the next start (size mismatch). Finished files are skipped.";
    }
    return data.copy.error || "The last copy reported an error. Check the log.";
  }
  return "Idle. Start / resume copy skips files whose destination size already matches. Incomplete files from a stop are recopied.";
}

async function refresh() {
  const data = await api("/api/status");
  $("route").textContent = `${data.source_label} → ${data.dest_label}`;
  $("source-fs").textContent = `src  ${data.source_fs}`;
  $("dest-fs").textContent = `dst  ${data.dest_fs}`;
  if ($("set-source-path")) $("set-source-path").textContent = data.source_fs || "Not set";
  if ($("set-dest-path")) $("set-dest-path").textContent = data.dest_fs || "Not set";
  $("windows-banner").classList.toggle("hidden", !data.windows_host);

  const stopNoise = (text) =>
    /context cancel+ed|job was aborted|job stopped/i.test(text || "");
  const copyBusy = ["running", "starting", "stopping"].includes(data.copy.status);
  const copyErr = stopNoise(data.copy.error) ? null : data.copy.error;
  const statsErr = data.stats && stopNoise(data.stats.lastError || data.stats.error)
    ? null
    : (data.stats && data.stats.error);
  const err = [
    copyBusy || data.copy.status === "error" ? copyErr : null,
    data.scan.running ? data.scan.error : null,
    statsErr,
  ].find(Boolean);
  $("error-banner").textContent = err || "";
  $("error-banner").classList.toggle("hidden", !err);

  const pill = $("status-pill");
  pill.textContent = data.activity;
  pill.className = `status-pill ${data.activity}`;

  const sourceBytes = data.totals.source_bytes;
  const destBytes = data.totals.dest_bytes_estimate || data.totals.dest_bytes;
  const percent = sourceBytes && destBytes != null ? Math.min((destBytes / sourceBytes) * 100, 100) : NaN;
  setRing(percent);

  $("source-size").textContent = bytes(sourceBytes);
  $("source-files").textContent = data.totals.source_files != null
    ? `${data.totals.source_files.toLocaleString()} files`
    : "scan to measure files";
  $("dest-size").textContent = bytes(destBytes);
  $("dest-files").textContent = data.totals.dest_files != null
    ? `${data.totals.dest_files.toLocaleString()} files`
    : "scan to measure files";
  $("remaining").textContent = bytes(data.totals.remaining_bytes);
  $("session").textContent = bytes(data.totals.session_bytes);

  const stats = data.stats || {};
  $("speed").textContent = stats.speed ? `${bytes(stats.speed)}/s` : "—";
  $("eta").textContent = duration(stats.eta);
  $("checks").textContent = stats.totalChecks
    ? `${stats.checks || 0} / ${stats.totalChecks}`
    : `${stats.checks || 0}`;
  $("transfers").textContent = stats.totalTransfers
    ? `${stats.transfers || 0} / ${stats.totalTransfers}`
    : `${stats.transfers || 0}`;
  $("errors").textContent = stats.errors || 0;
  $("elapsed").textContent = duration(stats.elapsedTime);
  $("session-detail").textContent = stats.speed ? `${bytes(stats.speed)}/s` : "";
  $("activity-hint").textContent = activityHint(data);
  renderFiles(stats);
  renderHistory(data.history);
  renderSpace(data);
  renderDashExcludes(data.setup || {});

  const busy = ["running", "starting", "stopping"].includes(data.copy.status);
  $("btn-start").disabled = busy;
  $("btn-dry").disabled = busy;
  $("btn-stop").disabled = !busy;
  $("btn-scan").disabled = data.scan.running;
  const changeLocked = busy;
  if ($("btn-change-source")) $("btn-change-source").disabled = changeLocked;
  if ($("btn-change-dest")) $("btn-change-dest").disabled = changeLocked;
  if ($("settings-folder-hint")) {
    $("settings-folder-hint").textContent = changeLocked
      ? "Stop the copy before changing folders."
      : "Changing folders does not delete anything already copied.";
  }
}

async function refreshLog() {
  const data = await api("/api/logs");
  $("log").textContent = data.text || "No log yet.";
}

async function act(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (err) {
    $("error-banner").textContent = err.message;
    $("error-banner").classList.remove("hidden");
  }
  try {
    await refresh();
  } catch (_) {
    button.disabled = false;
  }
}

$("btn-start").addEventListener("click", (event) => {
  act(event.currentTarget, () => api("/api/copy/start", { method: "POST", body: JSON.stringify({ dry_run: false }) }));
});
$("btn-dry").addEventListener("click", (event) => {
  act(event.currentTarget, () => api("/api/copy/start", { method: "POST", body: JSON.stringify({ dry_run: true }) }));
});
$("btn-stop").addEventListener("click", (event) => {
  act(event.currentTarget, () => api("/api/copy/stop", { method: "POST" }));
});
$("btn-scan").addEventListener("click", (event) => {
  act(event.currentTarget, () => api("/api/scan/start", { method: "POST" }));
});
$("btn-test").addEventListener("click", async (event) => {
  await act(event.currentTarget, async () => {
    const result = await api("/api/test", { method: "POST" });
    $("test-result").classList.remove("hidden");
    $("test-result").textContent = JSON.stringify(result, null, 2);
  });
});
$("btn-log").addEventListener("click", () => refreshLog());

const wiz = {
  step: 1,
  canCancel: true,
  sourcePath: "",
  destHost: "",
  destUser: "",
  destPass: "",
  destDomain: "WORKGROUP",
  destShare: "",
  destPath: "",
  excludeSystem: true,
  extraExcludes: [],
  builtin: ["CacheClip"],
  system: ["ix-applications"],
};

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function lockedChips(names) {
  return (names || [])
    .map((name) => `<span class="chip locked">${escapeHtml(name)}</span>`)
    .join("");
}

function customChips(names, attr) {
  if (!names || !names.length) {
    return `<span class="chip locked">None extra — type a name above</span>`;
  }
  return names
    .map((name) => `<span class="chip" title="Remove">${escapeHtml(name)} <button type="button" data-name="${escapeHtml(name)}" ${attr} aria-label="Stop skipping ${escapeHtml(name)}">×</button></span>`)
    .join("");
}

function builtinList(setup) {
  const names = [...(setup.builtin_excludes || ["CacheClip"])];
  if (setup.exclude_system !== false) {
    names.push(...(setup.system_excludes || ["ix-applications"]));
  }
  return names;
}

let lastExcludeKey = "";

function renderDashExcludes(setup) {
  if (!$("dash-builtin")) return;
  const extras = setup.extra_excludes || [];
  const key = JSON.stringify([setup.exclude_system, extras, setup.builtin_excludes, setup.system_excludes]);
  const typing = document.activeElement === $("exclude-input");
  if (key === lastExcludeKey && typing) return;
  lastExcludeKey = key;
  $("dash-builtin").innerHTML = lockedChips(builtinList(setup));
  $("dash-custom").innerHTML = customChips(extras, "data-dash-remove");
  if (document.activeElement !== $("dash-exclude-system")) {
    $("dash-exclude-system").checked = setup.exclude_system !== false;
  }
  $("dash-custom").querySelectorAll("[data-dash-remove]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      saveDashExcludes(extras.filter((name) => name !== btn.dataset.name));
    });
  });
  $("dash-custom").querySelectorAll(".chip:not(.locked)").forEach((chip) => {
    chip.addEventListener("click", () => {
      const btn = chip.querySelector("[data-dash-remove]");
      if (btn) saveDashExcludes(extras.filter((name) => name !== btn.dataset.name));
    });
  });
}

function addExcludeName(list, raw) {
  const name = (raw || "").trim().replace(/^\/+|\/+$/g, "");
  if (!name) return list;
  const lower = name.toLowerCase();
  if (list.some((item) => item.toLowerCase() === lower)) return list;
  return [...list, name];
}

async function saveDashExcludes(extras) {
  try {
    const setup = await api("/api/excludes", {
      method: "POST",
      body: JSON.stringify({
        extra_excludes: extras,
        exclude_system: $("dash-exclude-system").checked,
      }),
    });
    lastExcludeKey = "";
    renderDashExcludes(setup);
  } catch (err) {
    toast(err.message);
  }
}

function openSettings() {
  $("settings-backdrop").classList.remove("hidden");
  $("exclude-input").focus();
}

function closeSettings() {
  $("settings-backdrop").classList.add("hidden");
  $("settings-error").classList.add("hidden");
}

async function showWizard(force, startStep = 1) {
  if (!force) return;
  closeSettings();
  try {
    const setup = await api("/api/setup");
    wiz.sourcePath = setup.source_path || "";
    wiz.destHost = setup.dest_smb_host || "";
    wiz.destUser = setup.dest_smb_user || "";
    wiz.destPass = "";
    wiz.destDomain = setup.dest_smb_domain || "WORKGROUP";
    wiz.destShare = setup.dest_smb_share || "";
    wiz.destPath = setup.dest_smb_path || "";
    wiz.excludeSystem = setup.exclude_system !== false;
    wiz.extraExcludes = setup.extra_excludes || [];
    wiz.builtin = setup.builtin_excludes || ["CacheClip"];
    wiz.system = setup.system_excludes || ["ix-applications"];
    $("dest-host").value = wiz.destHost;
    $("dest-user").value = wiz.destUser;
    $("dest-domain").value = wiz.destDomain;
    $("dest-pass").placeholder = setup.dest_smb_pass_set ? "Leave blank to keep current password" : "";
    wiz.canCancel = setup.complete !== false;
  } catch (err) {
    toast(err.message);
  }
  wiz.step = startStep === 2 ? 2 : 1;
  $("wizard").classList.remove("hidden");
  $("monitor").classList.add("hidden");
  $("btn-settings").classList.add("hidden");
  renderWiz();
  if (wiz.step === 1) {
    loadDisks().catch((err) => toast(err.message));
    if (wiz.sourcePath) loadLocalFolders(wiz.sourcePath).catch(() => {});
  }
}

function hideWizard() {
  $("wizard").classList.add("hidden");
  $("monitor").classList.remove("hidden");
  $("btn-settings").classList.remove("hidden");
  refresh().catch(() => {});
}

function renderWiz() {
  [1, 2, 3].forEach((n) => $(`wiz-${n}`).classList.toggle("hidden", wiz.step !== n));
  document.querySelectorAll("#wizard-steps span").forEach((el) => {
    el.classList.toggle("on", Number(el.dataset.step) === wiz.step);
  });
  $("btn-wiz-back").disabled = wiz.step === 1;
  $("btn-wiz-cancel").classList.toggle("hidden", !wiz.canCancel);
  $("btn-wiz-next").textContent = wiz.step === 3 ? "Save and open dashboard" : "Next";
  $("picked-source").textContent = wiz.sourcePath || "No folder selected";
  $("picked-dest").textContent = wiz.destHost
    ? `${wiz.destHost}/${wiz.destShare}${wiz.destPath ? "/" + wiz.destPath : ""}`
    : "No share selected";
  $("review-source").textContent = `from  ${wiz.sourcePath}`;
  $("review-dest").textContent = `to    ${wiz.destHost}/${wiz.destShare}${wiz.destPath ? "/" + wiz.destPath : ""}`;
}

function diskCard(disk, selected) {
  const pct = disk.percent || 0;
  return `<button type="button" class="pick ${selected ? "on" : ""}" data-path="${disk.path}">
    <strong>${disk.name}</strong>
    <div>${bytes(disk.used)} used · ${bytes(disk.free)} free · ${bytes(disk.total)}</div>
    <div class="bar"><i style="width:${pct}%"></i></div>
  </button>`;
}

async function loadDisks() {
  $("local-disks").textContent = "Reading local disks…";
  const data = await api("/api/disks");
  $("local-disks").innerHTML = (data.disks || []).map((disk) => diskCard(disk, disk.path === wiz.sourcePath)).join("") || "No /mnt datasets found";
  $("local-disks").querySelectorAll(".pick").forEach((btn) => {
    btn.addEventListener("click", async () => {
      wiz.sourcePath = btn.dataset.path;
      renderWiz();
      await loadLocalFolders(wiz.sourcePath);
      loadDisks().catch(() => {});
    });
  });
}

async function loadLocalFolders(path) {
  $("local-folders").textContent = "Listing folders…";
  const data = await api(`/api/folders?path=${encodeURIComponent(path)}`);
  const up = data.parent
    ? `<button type="button" class="pick" data-path="${data.parent}">↑ ${data.parent}</button>`
    : "";
  const items = (data.folders || [])
    .map((folder) => `<button type="button" class="pick" data-path="${folder.path}">${folder.name}</button>`)
    .join("");
  $("local-folders").innerHTML = up + items || "No subfolders";
  $("local-folders").querySelectorAll(".pick").forEach((btn) => {
    btn.addEventListener("click", async () => {
      wiz.sourcePath = btn.dataset.path;
      renderWiz();
      await loadLocalFolders(wiz.sourcePath);
    });
  });
}

$("btn-scan-lan").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  $("lan-hosts").textContent = "Scanning this LAN for SMB (port 445)…";
  try {
    const data = await api("/api/lan/smb");
    $("lan-hosts").innerHTML = (data.hosts || [])
      .map((item) => `<button type="button" class="pick" data-host="${item.host}">${item.host}</button>`)
      .join("") || "None found. Type the IP yourself.";
    $("lan-hosts").querySelectorAll(".pick").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("dest-host").value = btn.dataset.host;
        wiz.destHost = btn.dataset.host;
      });
    });
  } catch (err) {
    $("lan-hosts").textContent = err.message;
  } finally {
    event.currentTarget.disabled = false;
  }
});

$("btn-list-shares").addEventListener("click", async () => {
  wiz.destHost = $("dest-host").value.trim();
  wiz.destUser = $("dest-user").value.trim();
  wiz.destPass = $("dest-pass").value;
  wiz.destDomain = $("dest-domain").value.trim() || "WORKGROUP";
  $("share-list").textContent = "Listing shares…";
  const data = await api("/api/smb/shares", {
    method: "POST",
    body: JSON.stringify({
      host: wiz.destHost,
      user: wiz.destUser,
      password: wiz.destPass,
      domain: wiz.destDomain,
    }),
  });
  $("share-list").innerHTML = (data.shares || [])
    .map((name) => `<button type="button" class="pick" data-share="${name}">${name}</button>`)
    .join("") || "No shares visible for this user";
  $("share-list").querySelectorAll(".pick").forEach((btn) => {
    btn.addEventListener("click", async () => {
      wiz.destShare = btn.dataset.share;
      wiz.destPath = "";
      renderWiz();
      await loadDestFolders("");
    });
  });
});

async function loadDestFolders(path) {
  $("dest-folders").textContent = "Listing folders…";
  const data = await api("/api/smb/folders", {
    method: "POST",
    body: JSON.stringify({
      host: wiz.destHost,
      user: wiz.destUser,
      password: wiz.destPass,
      domain: wiz.destDomain,
      share: wiz.destShare,
      path,
    }),
  });
  const up = path
    ? `<button type="button" class="pick" data-path="${path.split("/").slice(0, -1).join("/")}">↑ share root or parent</button>`
    : "";
  $("dest-folders").innerHTML =
    up +
    (data.folders || [])
      .map((name) => {
        const next = path ? `${path}/${name}` : name;
        return `<button type="button" class="pick" data-path="${next}">${name}</button>`;
      })
      .join("");
  $("dest-folders").querySelectorAll(".pick").forEach((btn) => {
    btn.addEventListener("click", async () => {
      wiz.destPath = btn.dataset.path;
      renderWiz();
      await loadDestFolders(wiz.destPath);
    });
  });
}

$("btn-wiz-back").addEventListener("click", () => {
  wiz.step = Math.max(1, wiz.step - 1);
  renderWiz();
});
$("btn-wiz-cancel").addEventListener("click", () => hideWizard());

$("btn-settings").addEventListener("click", () => openSettings());
$("btn-settings-close").addEventListener("click", () => closeSettings());
$("settings-backdrop").addEventListener("click", (event) => {
  if (event.target === $("settings-backdrop")) closeSettings();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("settings-backdrop").classList.contains("hidden")) closeSettings();
});
$("btn-change-source").addEventListener("click", () => showWizard(true, 1));
$("btn-change-dest").addEventListener("click", () => showWizard(true, 2));
$("btn-exclude-add").addEventListener("click", async () => {
  const setup = await api("/api/setup").catch(() => ({ extra_excludes: [] }));
  const extras = addExcludeName(setup.extra_excludes || [], $("exclude-input").value);
  $("exclude-input").value = "";
  await saveDashExcludes(extras);
});
$("exclude-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    $("btn-exclude-add").click();
  }
});
$("dash-exclude-system").addEventListener("change", async () => {
  const setup = await api("/api/setup").catch(() => ({ extra_excludes: [] }));
  await saveDashExcludes(setup.extra_excludes || []);
});

$("btn-wiz-next").addEventListener("click", async () => {
  try {
    if (wiz.step === 1) {
      if (!wiz.sourcePath) {
        toast("Pick a source folder on this NAS");
        return;
      }
      wiz.step = 2;
      renderWiz();
      return;
    }
    if (wiz.step === 2) {
      wiz.destHost = $("dest-host").value.trim();
      wiz.destUser = $("dest-user").value.trim();
      wiz.destPass = $("dest-pass").value;
      wiz.destDomain = $("dest-domain").value.trim() || "WORKGROUP";
      if (!wiz.destHost || !wiz.destShare) {
        toast("Pick a destination host and share");
        return;
      }
      wiz.step = 3;
      renderWiz();
      return;
    }
    wiz.excludeSystem = wiz.excludeSystem !== false;
    await api("/api/setup", {
      method: "POST",
      body: JSON.stringify({
        source_path: wiz.sourcePath,
        dest_smb_host: wiz.destHost,
        dest_smb_share: wiz.destShare,
        dest_smb_path: wiz.destPath,
        dest_smb_user: wiz.destUser,
        dest_smb_pass: wiz.destPass,
        dest_smb_domain: wiz.destDomain,
        exclude_system: wiz.excludeSystem,
        extra_excludes: wiz.extraExcludes,
        source_label: "This NAS",
        dest_label: `${wiz.destHost}/${wiz.destShare}`,
      }),
    });
    $("wizard").classList.add("hidden");
    $("monitor").classList.remove("hidden");
    $("btn-settings").classList.remove("hidden");
    $("error-banner").classList.add("hidden");
    await refresh();
    await refreshLog();
  } catch (err) {
    toast(err.message);
  }
});

function toast(message) {
  const settingsOpen = !$("settings-backdrop").classList.contains("hidden");
  const banner = settingsOpen ? $("settings-error") : $("error-banner");
  banner.textContent = message;
  banner.classList.remove("hidden");
}

async function boot() {
  let setup = { complete: true };
  try {
    setup = await api("/api/setup");
  } catch (err) {
    toast(err.message);
  }
  if (setup && setup.complete === false) {
    wiz.canCancel = false;
    $("wizard").classList.remove("hidden");
    $("monitor").classList.add("hidden");
    $("btn-settings").classList.add("hidden");
    renderWiz();
    await loadDisks();
    return;
  }
  $("wizard").classList.add("hidden");
  $("monitor").classList.remove("hidden");
  await refresh();
  await refreshLog();
  setInterval(() => {
    if ($("monitor").classList.contains("hidden")) return;
    refresh().catch(() => {});
  }, 1500);
  setInterval(() => {
    if ($("monitor").classList.contains("hidden")) return;
    refreshLog().catch(() => {});
  }, 8000);
}

boot().catch((err) => {
  toast(err.message);
  $("monitor").classList.remove("hidden");
  refresh().catch(() => {});
});
