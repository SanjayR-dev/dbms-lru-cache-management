const $ = (id) => document.getElementById(id);

const API = "";

const FRAME_HINTS = {
  lru: "Frame order: MRU → LRU",
  fifo: "Frame order: oldest (FCFS) → newest",
  optimal: "Frame order: current frames in pool",
};

function parsePages(raw) {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const n = Number(s);
      if (!Number.isInteger(n)) throw new Error(`Not an integer page id: "${s}"`);
      return n;
    });
}

async function apiFetch(path, options = {}) {
  if (window.location.protocol === "file:") {
    throw new Error(
      "Open via the server: run python server.py then visit http://127.0.0.1:8765/"
    );
  }
  const res = await fetch(`${API}${path}`, options);
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    if (!res.ok) {
      throw new Error(`Server returned non-JSON (${res.status}). Restart: python server.py`);
    }
  }
  if (!res.ok) {
    const msg = apiError(data, res) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function apiError(data, res) {
  const detail = data.detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  if (typeof detail === "string") return detail;
  return res.statusText || `HTTP ${res.status}`;
}

function ioLabel(step) {
  const parts = [];
  if (step.disk_read) parts.push("R");
  if (step.disk_write) parts.push("W");
  return parts.length ? parts.join("+") : "—";
}

function normalizeStep(step) {
  return {
    page: step.page ?? step.page_id,
    operation: step.operation || "read",
    hit: step.hit,
    evicted: step.evicted ?? step.evicted_page_id,
    disk_read: step.disk_read,
    disk_write: step.disk_write,
    dirty_after: step.dirty_after,
    frames_mru_to_lru: step.frames_mru_to_lru || step.frames || [],
  };
}

function renderTrace(steps, tbodyId = "trace-body", compact = false) {
  const tbody = $(tbodyId);
  if (!tbody) return;
  tbody.replaceChildren();
  (steps || []).forEach((raw, i) => {
    const step = normalizeStep(raw);
    const tr = document.createElement("tr");
    const frames = step.frames_mru_to_lru.length
      ? `[${step.frames_mru_to_lru.join(", ")}]`
      : "[]";
    const op = step.operation;
    const ev = step.evicted != null ? String(step.evicted) : "—";
    if (compact) {
      tr.innerHTML = `
        <td class="cell-num">${i + 1}</td>
        <td class="cell-ref">${step.page}</td>
        <td><span class="badge ${step.hit ? "hit" : "miss"}">${step.hit ? "HIT" : "MISS"}</span></td>
        <td class="cell-ref">${ev}</td>
        <td class="frames-cell">${frames}</td>
      `;
    } else {
      tr.innerHTML = `
        <td class="cell-num">${i + 1}</td>
        <td class="cell-ref">${step.page}</td>
        <td><span class="op ${op}">${op.toUpperCase()}</span></td>
        <td><span class="badge ${step.hit ? "hit" : "miss"}">${step.hit ? "HIT" : "MISS"}</span></td>
        <td class="cell-ref">${ev}</td>
        <td class="io-cell">${ioLabel(step)}</td>
        <td>${step.dirty_after ? "yes" : "—"}</td>
        <td class="frames-cell">${frames}</td>
      `;
    }
    tbody.appendChild(tr);
  });
}

function updatePolicyUi() {
  const mode = $("mode").value;
  const policy = $("policy");
  const dbBacked = mode === "db_backed";
  policy.querySelectorAll("option").forEach((opt) => {
    if (dbBacked) {
      opt.disabled = opt.value !== "lru";
    } else {
      opt.disabled = false;
    }
  });
  if (dbBacked && policy.value !== "lru") {
    policy.value = "lru";
  }
  const hint = $("frame-order-hint");
  if (hint) {
    hint.textContent = FRAME_HINTS[policy.value] || FRAME_HINTS.lru;
  }
}

async function run() {
  const status = $("status");
  const summary = $("summary");
  const compareStat = $("compare-stat");
  status.textContent = "";
  status.classList.remove("error");
  if (compareStat) compareStat.hidden = true;

  const frames = Number($("frames").value);
  const mode = $("mode").value;
  const policy = $("policy").value;
  const writeNth = Number($("write-nth").value);
  const persist = $("persist").checked;
  let pages;
  try {
    pages = parsePages($("pages").value);
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("error");
    summary.hidden = true;
    return;
  }

  if (!Number.isInteger(frames) || frames < 1 || frames > 64) {
    status.textContent = "Frames must be 1–64.";
    status.classList.add("error");
    return;
  }

  updatePolicyUi();
  status.textContent = "Running…";
  try {
    const data = await apiFetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        frames,
        pages,
        mode,
        policy,
        write_every_nth: writeNth,
        persist,
        auto_seed: true,
      }),
    });

    renderTrace(data.steps);
    $("stat-hit-ratio").textContent = String(data.stats.hit_ratio);
    $("stat-hm").textContent = `${data.stats.hits} / ${data.stats.misses}`;
    $("stat-evictions").textContent = String(data.stats.evictions);
    $("stat-opt").textContent = `${policy.toUpperCase()} · opt ${data.optimal_hit_ratio} · run ${data.run_id != null ? "#" + data.run_id : "—"}`;

    const ioBlock = $("io-stat");
    if (data.io) {
      ioBlock.hidden = false;
      $("stat-io").textContent = `${data.io.disk_reads} / ${data.io.disk_writes}`;
    } else {
      ioBlock.hidden = true;
    }
    summary.hidden = false;
    status.textContent = `${data.stats.references} refs · ${policy} · ${mode}`;
    loadRuns().catch(() => {});
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("error");
    summary.hidden = true;
  }
}

async function compareAll() {
  const status = $("status");
  status.textContent = "Comparing policies…";
  status.classList.remove("error");
  try {
    const pages = parsePages($("pages").value);
    const frames = Number($("frames").value);
    const data = await apiFetch("/api/simulate/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        frames,
        pages,
        mode: "memory",
        write_every_nth: Number($("write-nth").value),
        persist: false,
      }),
    });
    const parts = data.policies.map((p) => `${p.policy}=${p.hit_ratio}`);
    $("stat-compare").textContent = parts.join(" · ");
    $("compare-stat").hidden = false;
    $("summary").hidden = false;
    status.textContent = `Compared ${data.reference_count} refs (memory mode)`;
    document.querySelector('.tab[data-tab="simulator"]')?.click();
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("error");
  }
}

async function loadWorkloads() {
  try {
    const data = await apiFetch("/api/workloads");
    const sel = $("workload-select");
    sel.replaceChildren(new Option("— custom —", ""));
    for (const w of data.workloads || []) {
      sel.appendChild(new Option(`${w.name} (${w.reference_count} refs)`, String(w.id)));
    }
  } catch {
    /* optional */
  }
}

async function onWorkloadSelect() {
  const id = $("workload-select").value;
  if (!id) return;
  const wl = await apiFetch(`/api/workloads/${id}`);
  $("frames").value = String(wl.frame_count);
  $("pages").value = wl.references.map((r) => r.page_id).join(",");
}

async function loadDisk() {
  const tbody = $("disk-body");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="4">Loading…</td></tr>`;
  try {
    const data = await apiFetch("/api/disk/pages?limit=200");
    tbody.replaceChildren();
    for (const p of data.pages || []) {
      const tr = document.createElement("tr");
      const payload =
        p.payload.length > 60 ? p.payload.slice(0, 57) + "…" : p.payload;
      tr.innerHTML = `
        <td class="cell-ref">${p.page_id}</td>
        <td class="frames-cell">${payload}</td>
        <td>${p.version}</td>
        <td class="cell-muted">${p.updated_at || "—"}</td>
      `;
      tbody.appendChild(tr);
    }
    if (!data.pages?.length) {
      tbody.innerHTML = `<tr><td colspan="4">No pages yet — click <strong>Seed pages 0–15</strong>.</td></tr>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="panel-error">${e.message}</td></tr>`;
  }
}

async function seedDisk() {
  try {
    const ids = Array.from({ length: 16 }, (_, i) => i);
    await apiFetch("/api/disk/seed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page_ids: ids }),
    });
    await loadDisk();
  } catch (e) {
    alert(e.message);
  }
}

async function loadRuns() {
  const tbody = $("runs-body");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="8">Loading…</td></tr>`;
  try {
    const data = await apiFetch("/api/runs?limit=40");
    tbody.replaceChildren();
    if (!data.runs?.length) {
      tbody.innerHTML = `<tr><td colspan="8">No saved runs yet. Run a simulation with “Save run to database” checked.</td></tr>`;
      return;
    }
    for (const r of data.runs) {
      const tr = document.createElement("tr");
      const when = r.created_at ? new Date(r.created_at).toLocaleString() : "—";
      const pol = (r.replacement_policy || "lru").toUpperCase();
      tr.innerHTML = `
        <td>${r.id}</td>
        <td><span class="mode-tag">${pol}</span></td>
        <td>${r.mode}</td>
        <td>${r.frame_count}</td>
        <td>${r.hit_ratio}</td>
        <td>${r.disk_reads} / ${r.disk_writes}</td>
        <td class="cell-muted">${when}</td>
        <td><button type="button" class="btn-link btn-view-run" data-run="${r.id}">View</button></td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="panel-error">${e.message}</td></tr>`;
  }
}

async function showRun(id) {
  const detail = $("run-detail");
  const wrap = $("history-trace-wrap");
  try {
    const run = await apiFetch(`/api/runs/${id}`);
    const pol = (run.replacement_policy || "lru").toUpperCase();
    detail.hidden = false;
    detail.innerHTML = `
      <h3>Run #${run.id} — ${pol} (${run.mode})</h3>
      <p>Hit ratio <strong>${run.hit_ratio}</strong> · ${run.hits} hits, ${run.misses} misses · disk I/O ${run.disk_reads}/${run.disk_writes}</p>
    `;
    if (wrap) wrap.hidden = false;
    renderTrace(run.steps || [], "history-trace-body", true);
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    alert(e.message);
  }
}

async function loadDbStatus() {
  const panel = $("db-status-panel");
  if (!panel) return;
  panel.innerHTML = `<p class="panel-desc">Loading…</p>`;
  try {
    const health = await apiFetch("/api/health");
    const data = await apiFetch("/api/db/status");
    const c = data.connection;
    const t = data.tables;
    panel.innerHTML = `
      <p class="server-ok">Server v${health.version} connected</p>
      <h2 class="section-title">Connection</h2>
      <dl class="db-dl">
        <dt>Backend</dt><dd>${c.backend || c.dialect}</dd>
        <dt>URI</dt><dd><code>${c.url}</code></dd>
        <dt>Database</dt><dd><code>${c.database || "—"}</code></dd>
        <dt>Status</dt><dd>${c.connected ? "connected" : "disconnected"}</dd>
      </dl>
      <h2 class="section-title">Collections</h2>
      <div class="summary">
        <div class="stat"><span class="stat-label">disk_pages</span><span class="stat-value">${t.disk_pages}</span></div>
        <div class="stat"><span class="stat-label">workloads</span><span class="stat-value">${t.workloads}</span></div>
        <div class="stat"><span class="stat-label">simulation_runs</span><span class="stat-value">${t.simulation_runs}</span></div>
      </div>
    `;
  } catch (e) {
    panel.innerHTML = `
      <p class="panel-desc panel-error"><strong>Could not load DB status.</strong> ${e.message}</p>
      <p class="panel-desc">Start MongoDB, then run <code>python server.py</code> and refresh.</p>
    `;
  }
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => {
        p.classList.remove("active");
        p.hidden = true;
      });
      tab.classList.add("active");
      const panel = $(`panel-${tab.dataset.tab}`);
      if (!panel) return;
      panel.classList.add("active");
      panel.hidden = false;
      if (tab.dataset.tab === "disk") loadDisk();
      if (tab.dataset.tab === "history") loadRuns();
      if (tab.dataset.tab === "database") loadDbStatus();
    });
  });
}

function bindClick(id, handler) {
  const el = $(id);
  if (!el) return;
  el.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    handler(e.currentTarget);
  });
}

function setButtonBusy(btn, busy, busyLabel = "Refreshing…") {
  if (!(btn instanceof HTMLButtonElement)) return;
  if (busy) {
    btn.dataset.prevLabel = btn.textContent;
    btn.textContent = busyLabel;
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.prevLabel || btn.textContent;
    btn.disabled = false;
  }
}

async function refreshDisk(btn) {
  setButtonBusy(btn, true);
  try {
    await loadDisk();
  } finally {
    setButtonBusy(btn, false);
  }
}

async function refreshRuns(btn) {
  setButtonBusy(btn, true);
  try {
    await loadRuns();
  } finally {
    setButtonBusy(btn, false);
  }
}

async function clearAllRuns(btn) {
  if (
    !confirm(
      "Delete all saved simulation runs? This cannot be undone."
    )
  ) {
    return;
  }
  setButtonBusy(btn, true, "Deleting…");
  try {
    let data;
    try {
      data = await apiFetch("/api/runs/clear", { method: "POST" });
    } catch (first) {
      if (String(first.message).includes("404") || String(first.message).includes("405")) {
        data = await apiFetch("/api/runs", { method: "DELETE" });
      } else {
        throw first;
      }
    }
    const detail = $("run-detail");
    const wrap = $("history-trace-wrap");
    if (detail) detail.hidden = true;
    if (wrap) wrap.hidden = true;
    await loadRuns();
    const n = data.deleted ?? 0;
    alert(n ? `Deleted ${n} run(s).` : "History was already empty.");
  } catch (e) {
    const msg =
      e.message === "Failed to fetch"
        ? "Cannot reach server. Restart: python server.py"
        : e.message.includes("405") || e.message.includes("404")
          ? `${e.message}\n\nRestart the server (python server.py) and hard-refresh the page (Ctrl+F5).`
          : e.message;
    alert(msg);
  } finally {
    setButtonBusy(btn, false);
  }
}

async function refreshDb(btn) {
  setButtonBusy(btn, true);
  try {
    await loadDbStatus();
  } finally {
    setButtonBusy(btn, false);
  }
}

function initClickHandlers() {
  bindClick("refresh-disk", refreshDisk);
  bindClick("refresh-runs", refreshRuns);
  bindClick("clear-runs", clearAllRuns);
  bindClick("refresh-db", refreshDb);
  bindClick("seed-disk", () => seedDisk());

  document.body.addEventListener("click", (e) => {
    const viewBtn = e.target.closest?.(".btn-view-run");
    if (viewBtn) {
      e.preventDefault();
      const id = viewBtn.getAttribute("data-run");
      if (id) showRun(Number(id));
    }
  });
}

async function boot() {
  initTabs();
  updatePolicyUi();

  const health = await checkServer();
  const banner = $("server-banner");
  if (banner) {
    if (health) {
      banner.textContent = `Connected · API v${health.version} · ${health.backend || "db"}`;
      banner.className = "server-banner ok";
    } else {
      banner.textContent =
        "API not reachable — run python server.py and open http://127.0.0.1:8765/";
      banner.className = "server-banner error";
    }
  }
  loadWorkloads().catch(() => {});
}

async function checkServer() {
  try {
    return await apiFetch("/api/health");
  } catch {
    return null;
  }
}

function initControls() {
  bindClick("run", () => run());
  bindClick("compare-all", () => compareAll());
  bindClick("preset-belady", () => {
    $("frames").value = "3";
    $("pages").value = "1,2,3,4,1,2,5,1,2,3,4,5";
    $("write-nth").value = "0";
  });
  $("workload-select")?.addEventListener("change", onWorkloadSelect);
  $("mode")?.addEventListener("change", updatePolicyUi);
  $("policy")?.addEventListener("change", updatePolicyUi);
  $("pages")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) run();
  });
}

function startApp() {
  initControls();
  initClickHandlers();
  boot();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startApp);
} else {
  startApp();
}
