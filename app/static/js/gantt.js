/* =======================================================================================
   Gantt / allocation-timeline renderer (pure SVG, no dependencies).

   Draws one row per asset across the mission horizon: faint bands show when each asset is
   online, coloured blocks are allocations (hue = priority, opacity = lifecycle status), a
   dashed "now" line marks the simulated clock, and locked allocations get a white marker.
   Blocks are clickable (human-in-the-loop selection) and hoverable (tooltip).
   ======================================================================================= */
(function (global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const PRIORITY_COLOR = {
    ROUTINE: "#6b7a99",
    PRIORITY: "#3fa7ff",
    IMMEDIATE: "#ffb454",
    FLASH: "#ff5d6c",
  };

  const GUTTER = 116; // left label column width
  const AXIS_H = 26; // top time-axis height
  const ROW_H = 34; // per-asset row height
  const PAD = 4; // vertical padding inside a row

  function el(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }

  function formatT(min) {
    const h = Math.floor(min / 60);
    const m = min % 60;
    return "T+" + String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  }

  /**
   * Render the timeline.
   * @param {SVGElement} svg target <svg> element
   * @param {object} snap engine snapshot
   * @param {object} handlers {onSelect(alloc), onHover(alloc, evt), onLeave(), selectedId}
   */
  function render(svg, snap, handlers) {
    handlers = handlers || {};
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const resources = snap.resources || [];
    const horizon = (snap.scenario && snap.scenario.horizon) || 480;
    const rows = resources.length;

    const wrap = svg.parentElement;
    const available = Math.max(640, (wrap ? wrap.clientWidth : 900) - 16);
    const pxPerMin = Math.max(1.1, (available - GUTTER) / horizon);
    const width = GUTTER + horizon * pxPerMin;
    const height = AXIS_H + rows * ROW_H + 6;

    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

    const x0 = (min) => GUTTER + min * pxPerMin;

    // --- time axis: vertical hour gridlines + labels ---
    for (let t = 0; t <= horizon; t += 60) {
      const x = x0(t);
      svg.appendChild(el("line", { class: "grid-line", x1: x, y1: AXIS_H, x2: x, y2: height - 4 }));
      svg.appendChild(el("text", { class: "axis-text", x: x + 3, y: 16 }, formatT(t)));
    }

    // --- rows ---
    resources.forEach((res, i) => {
      const y = AXIS_H + i * ROW_H;
      if (i % 2 === 0) {
        svg.appendChild(el("rect", { class: "row-band", x: 0, y: y, width: width, height: ROW_H }));
      }
      // availability bands (when this asset is online)
      (res.availability || []).forEach((w) => {
        svg.appendChild(
          el("rect", {
            class: "avail-band",
            x: x0(w[0]),
            y: y + 2,
            width: Math.max(1, (w[1] - w[0]) * pxPerMin),
            height: ROW_H - 4,
          })
        );
      });
      // label
      svg.appendChild(el("text", { class: "row-label", x: 10, y: y + 16 }, res.id));
      svg.appendChild(el("text", { class: "row-kind", x: 10, y: y + 27 }, res.kind));
      svg.appendChild(el("line", { class: "grid-line", x1: 0, y1: y + ROW_H, x2: width, y2: y + ROW_H }));
    });

    // --- allocation blocks ---
    const rowIndex = {};
    resources.forEach((r, i) => (rowIndex[r.id] = i));

    (snap.allocations || []).forEach((a) => {
      const i = rowIndex[a.resource_id];
      if (i === undefined) return;
      const y = AXIS_H + i * ROW_H + PAD;
      const x = x0(a.start);
      const w = Math.max(3, (a.end - a.start) * pxPerMin);
      const h = ROW_H - 2 * PAD;
      const color = PRIORITY_COLOR[a.priority] || PRIORITY_COLOR.ROUTINE;

      const statusClass =
        a.status === "COMMITTED"
          ? "alloc-committed"
          : a.status === "COMPLETED"
          ? "alloc-completed"
          : "alloc-planned";

      const g = el("g", { class: "alloc " + statusClass });
      const selected = handlers.selectedId && handlers.selectedId === a.request_id;
      const block = el("rect", {
        class: "alloc-block" + (selected ? " alloc-selected" : ""),
        x: x,
        y: y,
        width: w,
        height: h,
        rx: 4,
        fill: color,
        stroke: selected ? "#fff" : "rgba(0,0,0,0.25)",
        "stroke-width": selected ? 2 : 1,
      });
      g.appendChild(block);

      // capability label if there is room
      if (w > 24) {
        g.appendChild(el("text", { class: "alloc-label", x: x + 5, y: y + h / 2 + 3 }, a.capability));
      }
      // locked marker: white corner square + white outline
      if (a.locked) {
        g.appendChild(el("rect", { x: x, y: y, width: w, height: h, rx: 4, fill: "none", stroke: "#fff", "stroke-width": 1.6, "stroke-dasharray": "3 2" }));
        g.appendChild(el("rect", { x: x + w - 7, y: y + 2, width: 5, height: 5, rx: 1, fill: "#fff" }));
      }

      g.addEventListener("click", (e) => {
        e.stopPropagation();
        if (handlers.onSelect) handlers.onSelect(a);
      });
      g.addEventListener("mousemove", (e) => handlers.onHover && handlers.onHover(a, e));
      g.addEventListener("mouseleave", () => handlers.onLeave && handlers.onLeave());
      svg.appendChild(g);
    });

    // --- now line ---
    const nx = x0(Math.min(snap.clock, horizon));
    svg.appendChild(el("line", { class: "now-line", x1: nx, y1: AXIS_H - 2, x2: nx, y2: height - 4 }));
    const flagW = 34;
    svg.appendChild(el("rect", { class: "now-flag", x: nx - flagW / 2, y: 0, width: flagW, height: 15, rx: 3 }));
    svg.appendChild(el("text", { class: "now-flag-text", x: nx, y: 11, "text-anchor": "middle" }, "NOW"));
  }

  global.Gantt = { render: render, PRIORITY_COLOR: PRIORITY_COLOR, formatT: formatT };
})(window);
