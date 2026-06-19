/* =======================================================================================
   Dashboard controller: WebSocket stream -> render; controls + human-in-the-loop -> REST.

   The server is the single source of truth. We never mutate state locally; every control
   action POSTs to the API, the server applies it and broadcasts a fresh snapshot, and the
   whole UI re-renders from that snapshot. This keeps the optimizer engine and the screen
   perfectly consistent, which is exactly the human-in-the-loop story.
   ======================================================================================= */
(function () {
  "use strict";

  const PRIORITY_COLOR = Gantt.PRIORITY_COLOR;
  let state = null;
  let selectedId = null;
  let ws = null;

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- networking
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => {
      state = JSON.parse(e.data);
      render();
    };
    ws.onclose = () => setTimeout(connect, 1200); // auto-reconnect
  }

  async function post(path, body) {
    try {
      await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      /* broadcast will resync UI; ignore transient errors */
    }
  }

  // ---------------------------------------------------------------- helpers
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
  const pct = (n) => (n == null ? "—" : Number(n).toFixed(n >= 100 ? 0 : 1));

  function prioChip(priority) {
    const span = document.createElement("span");
    span.className = "prio-chip";
    span.textContent = priority[0]; // R / P / I / F
    span.title = priority;
    span.style.background = PRIORITY_COLOR[priority] || PRIORITY_COLOR.ROUTINE;
    return span;
  }

  // ---------------------------------------------------------------- main render
  function render() {
    if (!state) return;
    renderHeader();
    renderKpis();
    renderProposal();
    renderEvents();
    renderPending();
    Gantt.render($("gantt"), state, {
      selectedId,
      onSelect: selectAllocation,
      onHover: showTooltip,
      onLeave: hideTooltip,
    });
    renderSelection();
    renderUtilization();
    renderPriority();
    renderOptimizer();
    renderFlags();
  }

  function renderHeader() {
    const horizon = state.scenario.horizon;
    $("clock").textContent = Gantt.formatT(state.clock);
    $("clock-bar").style.width = `${Math.min(100, (100 * state.clock) / horizon)}%`;

    document.querySelectorAll(".mode-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === state.mode);
    });
    $("btn-play").textContent = state.playing ? "⏸" : "▶";
  }

  function renderKpis() {
    const h = state.headline || {};
    const k = state.kpis || {};
    $("kpi-value").textContent = fmt(h.optimizer_delivered);
    const uplift = h.online_uplift_pct || 0;
    const badge = $("kpi-uplift");
    badge.textContent = (uplift >= 0 ? "+" : "") + pct(uplift) + "% vs FIFO";
    badge.style.color = uplift > 0.05 ? "var(--good)" : "var(--muted)";

    $("kpi-opt").textContent = fmt(h.optimizer_delivered);
    $("kpi-base").textContent = fmt(h.baseline_delivered);
    const maxv = Math.max(h.optimizer_delivered || 1, h.baseline_delivered || 1, 1);
    $("bar-opt").style.width = `${(100 * (h.optimizer_delivered || 0)) / maxv}%`;
    $("bar-base").style.width = `${(100 * (h.baseline_delivered || 0)) / maxv}%`;

    $("kpi-capture").textContent = pct(k.value_capture_pct);
    $("kpi-served").textContent = k.served_count != null ? k.served_count : 0;
    $("kpi-served-foot").textContent = `of ${k.total_count || 0} revealed`;
    $("kpi-util").textContent = pct(k.mean_utilization_pct);

    const sbp = k.served_by_priority || {};
    const hi = ["FLASH", "IMMEDIATE"].reduce(
      (acc, t) => {
        const v = sbp[t] || [0, 0];
        return [acc[0] + v[0], acc[1] + v[1]];
      },
      [0, 0]
    );
    $("kpi-flash").textContent = `${hi[0]}/${hi[1]}`;
  }

  function renderProposal() {
    const banner = $("proposal-banner");
    const p = state.proposed;
    if (p && state.mode === "MANUAL") {
      banner.classList.remove("hidden");
      $("proposal-text").innerHTML = `<b>${p.change_count}</b> change(s) proposed by re-optimization — review and approve to apply.`;
    } else {
      banner.classList.add("hidden");
    }
  }

  function renderEvents() {
    const box = $("event-stream");
    box.innerHTML = "";
    (state.events || []).slice(0, 40).forEach((e) => {
      const row = document.createElement("div");
      let cls = "evt-arrival";
      if (e.type === "RESOURCE_OFFLINE") cls = "evt-outage";
      else if (e.type === "PRIORITY_CHANGED") cls = "evt-retask";
      row.className = "evt " + cls;
      row.innerHTML =
        `<span class="evt-dot"></span>` +
        `<span class="evt-time">${Gantt.formatT(e.time)}</span>` +
        `<span class="evt-body">${escapeHtml(e.description)}</span>`;
      box.appendChild(row);
    });
  }

  function renderPending() {
    const list = $("pending-list");
    list.innerHTML = "";
    const pending = state.pending || [];
    $("pending-count").textContent = pending.length;
    if (pending.length === 0) {
      list.innerHTML = `<div class="empty-note">All revealed taskings are scheduled.</div>`;
      return;
    }
    pending.slice(0, 16).forEach((p) => {
      const item = document.createElement("div");
      item.className = "pending-item";
      item.style.borderLeftColor = PRIORITY_COLOR[p.priority] || PRIORITY_COLOR.ROUTINE;
      const chip = prioChip(p.priority);
      const name = document.createElement("span");
      name.className = "pending-name";
      name.textContent = `${p.request_id} · ${p.capability}`;
      const meta = document.createElement("span");
      meta.className = "pending-meta";
      meta.textContent = `w${p.weight} · due ${Gantt.formatT(p.deadline)}`;
      item.appendChild(chip);
      item.appendChild(name);
      item.appendChild(meta);
      item.addEventListener("click", () => selectRequest(p));
      list.appendChild(item);
    });
  }

  function renderUtilization() {
    const list = $("util-list");
    list.innerHTML = "";
    const util = (state.kpis && state.kpis.utilization_by_resource) || {};
    (state.resources || []).forEach((r) => {
      const v = util[r.id] || 0;
      const row = document.createElement("div");
      row.className = "util-row";
      row.innerHTML =
        `<div class="util-top"><span class="uname">${r.id} <span class="util-kind">${r.capabilities.join("·")}</span></span><span class="uval">${pct(v)}%</span></div>` +
        `<div class="util-track"><div class="util-fill" style="width:${Math.min(100, v)}%"></div></div>`;
      list.appendChild(row);
    });
  }

  function renderPriority() {
    const list = $("prio-list");
    list.innerHTML = "";
    const sbp = (state.kpis && state.kpis.served_by_priority) || {};
    ["FLASH", "IMMEDIATE", "PRIORITY", "ROUTINE"].forEach((tier) => {
      const v = sbp[tier] || [0, 0];
      const frac = v[1] ? (100 * v[0]) / v[1] : 0;
      const row = document.createElement("div");
      row.className = "prio-row";
      row.innerHTML =
        `<span class="prio-tag" style="color:${PRIORITY_COLOR[tier]}">${tier}</span>` +
        `<span class="prio-track"><span class="prio-fill" style="width:${frac}%;background:${PRIORITY_COLOR[tier]}"></span></span>` +
        `<span class="prio-count">${v[0]}/${v[1]}</span>`;
      list.appendChild(row);
    });
  }

  function renderOptimizer() {
    const o = state.optimizer || {};
    $("opt-passes").textContent = o.passes != null ? o.passes : 0;
    $("opt-moves").textContent = o.moves_applied != null ? o.moves_applied : 0;
    $("opt-time").textContent = (o.elapsed_ms != null ? o.elapsed_ms : 0) + " ms";
    const head = state.headline || {};
    $("opt-offline").textContent = head.offline_uplift_pct != null ? "+" + pct(head.offline_uplift_pct) + "%" : "—";
    drawConvergence(o.history || []);
  }

  function drawConvergence(history) {
    const svg = $("convergence");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!history.length) return;
    const w = svg.clientWidth || 270;
    const h = 46;
    const min = Math.min.apply(null, history);
    const max = Math.max.apply(null, history);
    const span = max - min || 1;
    const n = history.length;
    const pts = history
      .map((v, i) => {
        const x = (i / Math.max(1, n - 1)) * (w - 4) + 2;
        const y = h - 4 - ((v - min) / span) * (h - 10);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    poly.setAttribute("points", pts);
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "#4cc9f0");
    poly.setAttribute("stroke-width", "1.6");
    svg.appendChild(poly);
  }

  function renderFlags() {
    const list = $("flags-list");
    const flags = state.flags || [];
    if (flags.length === 0) {
      list.innerHTML = `<div class="flags-empty">No exceptions flagged.</div>`;
      return;
    }
    list.innerHTML = "";
    flags.slice(0, 8).forEach((f) => {
      const div = document.createElement("div");
      div.className = "flag";
      div.textContent = f;
      list.appendChild(div);
    });
  }

  // ---------------------------------------------------------------- selection + HITL
  function findAllocation(requestId) {
    return (state.allocations || []).find((a) => a.request_id === requestId) || null;
  }

  function selectAllocation(alloc) {
    selectedId = alloc.request_id;
    render();
  }

  function selectRequest(p) {
    selectedId = p.request_id;
    render();
  }

  function renderSelection() {
    const bar = $("selection-bar");
    const info = $("sel-info");
    const actions = $("sel-actions");
    actions.innerHTML = "";
    if (!selectedId) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    const alloc = findAllocation(selectedId);
    const pending = (state.pending || []).find((p) => p.request_id === selectedId);

    if (alloc) {
      info.innerHTML = `<b>${alloc.request_id}</b> · ${alloc.priority} · ${alloc.capability} → ${alloc.resource_id} @ ${Gantt.formatT(alloc.start)}–${Gantt.formatT(alloc.end)} <span style="color:var(--muted)">(${alloc.status.toLowerCase()})</span>`;
      if (alloc.locked) {
        actions.appendChild(button("Unlock", () => post(`/api/task/${selectedId}/unlock`)));
      } else {
        actions.appendChild(button("🔒 Lock", () => post(`/api/task/${selectedId}/lock`)));
      }
      actions.appendChild(button("Hold (don't service)", () => post(`/api/task/${selectedId}/hold`)));
      addOverrideControls(actions, alloc.request_id);
    } else if (pending) {
      info.innerHTML = `<b>${pending.request_id}</b> · ${pending.priority} · ${pending.capability} · <span style="color:var(--muted)">unscheduled</span> · eligible: ${pending.eligible_resources.join(", ") || "none"}`;
      if (state.held && state.held.includes(selectedId)) {
        actions.appendChild(button("Release hold", () => post(`/api/task/${selectedId}/release`)));
      } else {
        actions.appendChild(button("Hold", () => post(`/api/task/${selectedId}/hold`)));
      }
      addOverrideControls(actions, pending.request_id, pending.eligible_resources);
    } else {
      info.textContent = `${selectedId} is no longer active.`;
    }
    actions.appendChild(button("✕", () => { selectedId = null; render(); }, "btn-sm"));
  }

  function addOverrideControls(actions, requestId, eligible) {
    const resSelect = document.createElement("select");
    const opts = eligible || (state.resources || []).map((r) => r.id);
    opts.forEach((rid) => {
      const o = document.createElement("option");
      o.value = rid;
      o.textContent = rid;
      resSelect.appendChild(o);
    });
    const startInput = document.createElement("input");
    startInput.type = "number";
    startInput.min = "0";
    startInput.max = String(state.scenario.horizon);
    startInput.value = String(state.clock);
    startInput.style.width = "62px";
    startInput.title = "Start minute";
    const go = button("Override →", () =>
      post("/api/task/override", {
        request_id: requestId,
        resource_id: resSelect.value,
        start: parseInt(startInput.value, 10) || state.clock,
      })
    );
    actions.appendChild(resSelect);
    actions.appendChild(startInput);
    actions.appendChild(go);
  }

  function button(label, onClick, cls) {
    const b = document.createElement("button");
    b.className = cls || "btn-sm";
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------------------------------------------------------------- tooltip
  function showTooltip(a, evt) {
    const tt = $("tooltip");
    tt.classList.remove("hidden");
    tt.innerHTML =
      `<div class="tt-title" style="color:${PRIORITY_COLOR[a.priority]}">${a.request_id} · ${a.priority}</div>` +
      `<div class="tt-row">${a.name}</div>` +
      `<div class="tt-row">Asset <b>${a.resource_id}</b> · ${a.capability} · ${a.region}</div>` +
      `<div class="tt-row">Window <b>${Gantt.formatT(a.start)}</b>–<b>${Gantt.formatT(a.end)}</b> · value <b>${a.weight}</b></div>` +
      `<div class="tt-row">Status <b>${a.status.toLowerCase()}</b>${a.locked ? " · 🔒 locked" : ""}</div>`;
    const pad = 14;
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;
    const r = tt.getBoundingClientRect();
    if (x + r.width > window.innerWidth) x = evt.clientX - r.width - pad;
    if (y + r.height > window.innerHeight) y = evt.clientY - r.height - pad;
    tt.style.left = x + "px";
    tt.style.top = y + "px";
  }

  function hideTooltip() {
    $("tooltip").classList.add("hidden");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ---------------------------------------------------------------- controls wiring
  const SPEEDS = {
    slow: { minutes_per_tick: 2, interval_ms: 320 },
    normal: { minutes_per_tick: 3, interval_ms: 180 },
    fast: { minutes_per_tick: 4, interval_ms: 110 },
    turbo: { minutes_per_tick: 6, interval_ms: 70 },
  };

  function wireControls() {
    document.querySelectorAll(".mode-btn").forEach((b) => {
      b.addEventListener("click", () => post("/api/control/mode", { mode: b.dataset.mode }));
    });
    $("btn-play").addEventListener("click", () => post(state && state.playing ? "/api/control/pause" : "/api/control/play"));
    $("btn-step").addEventListener("click", () => post("/api/control/step"));
    $("btn-reset").addEventListener("click", () => { selectedId = null; post("/api/control/reset"); });
    $("speed").addEventListener("change", (e) => post("/api/control/speed", SPEEDS[e.target.value] || SPEEDS.normal));
    $("btn-approve").addEventListener("click", () => post("/api/proposal/approve"));
    $("btn-reject").addEventListener("click", () => post("/api/proposal/reject"));
    document.body.addEventListener("click", () => {}); // (reserved) click-away
    window.addEventListener("resize", () => render());
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    wireControls();
    try {
      const r = await fetch("/api/state");
      state = await r.json();
      render();
    } catch (e) {
      /* socket will deliver state */
    }
    connect();
  }

  boot();
})();
