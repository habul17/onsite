// The design-round whiteboard: boxes and arrows on an SVG canvas.
// The server turns the board into text for the interviewer, so only kinds, labels and arrows matter there.
// Positions only help you think.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const KINDS = {
  client: "Client",
  service: "Service",
  database: "Database",
  cache: "Cache",
  queue: "Queue",
  storage: "Storage",
  note: "Note",
};
const BOX_H = 56;
const CANVAS = { w: 1600, h: 1000 };

function whiteboard(ui) {
  let boxes = []; // {id, kind, label, x, y}, x and y are the box's top-left corner
  let arrows = []; // {source, target, label}
  let selected = null; // {type: "box", id} or {type: "arrow", index}
  let nextId = 1;
  let drag = null;
  let lastDown = null;
  const svg = ui.svg;

  // Sizes and geometry

  function width(b) {
    const longest = Math.max(b.label.length, KINDS[b.kind].length + 2);
    return Math.min(240, Math.max(120, 24 + 7.6 * longest));
  }

  function box(id) {
    return boxes.find((b) => b.id === id);
  }

  function center(b) {
    return { x: b.x + width(b) / 2, y: b.y + BOX_H / 2 };
  }

  function boxAt(p) {
    // Topmost first, so the box drawn last wins.
    return [...boxes].reverse().find((b) => p.x >= b.x && p.x <= b.x + width(b) && p.y >= b.y && p.y <= b.y + BOX_H);
  }

  function toSvg(event) {
    const pt = svg.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }

  function edgePoint(b, dx, dy) {
    // Where the line from the box's centre in direction (dx, dy) leaves the box.
    const c = center(b);
    const t = Math.min((width(b) / 2) / Math.abs(dx || 1e-9), (BOX_H / 2) / Math.abs(dy || 1e-9));
    return { x: c.x + dx * t, y: c.y + dy * t };
  }

  function freeSpot() {
    // The first spot on a grid, left to right and top to bottom, that no box covers.
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 6; col++) {
        const spot = { x: 40 + col * 260, y: 40 + row * 120 };
        const probe = { x: spot.x + 60, y: spot.y + BOX_H / 2 };
        if (!boxes.some((b) => Math.abs(center(b).x - probe.x) < 200 && Math.abs(center(b).y - probe.y) < 90)) return spot;
      }
    }
    return { x: 40, y: 40 };
  }

  function clamp(b) {
    b.x = Math.max(0, Math.min(CANVAS.w - width(b), b.x));
    b.y = Math.max(0, Math.min(CANVAS.h - BOX_H, b.y));
  }

  // Changes

  function changed() {
    render();
    if (ui.onChange) ui.onChange();
  }

  function add(kind, label, at) {
    const b = { id: `b${nextId++}`, kind, label: label.trim(), ...(at || freeSpot()) };
    clamp(b);
    boxes.push(b);
    selected = { type: "box", id: b.id };
    changed();
    return b;
  }

  function connect(source, target, label = "") {
    if (!source || !target || source === target) return false;
    label = label.trim();
    if (arrows.some((a) => a.source === source && a.target === target && a.label === label)) return false;
    arrows.push({ source, target, label });
    selected = { type: "arrow", index: arrows.length - 1 };
    changed();
    return true;
  }

  function removeSelected() {
    if (!selected) return;
    if (selected.type === "box") {
      boxes = boxes.filter((b) => b.id !== selected.id);
      arrows = arrows.filter((a) => a.source !== selected.id && a.target !== selected.id);
    } else {
      arrows.splice(selected.index, 1);
    }
    selected = null;
    changed();
  }

  function selectedItem() {
    if (!selected) return null;
    return selected.type === "box" ? box(selected.id) : arrows[selected.index];
  }

  // Drawing

  function node(tag, attrs, parent) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.append(n);
    return n;
  }

  function render() {
    svg.replaceChildren();
    const defs = node("defs", {}, svg);
    const marker = node("marker", { id: "wb-head", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: "auto-start-reverse" }, defs);
    node("path", { d: "M0,0 L10,5 L0,10 z", class: "wb-head" }, marker);
    node("rect", { x: 0, y: 0, width: CANVAS.w, height: CANVAS.h, class: "wb-paper" }, svg);

    arrows.forEach((a, i) => {
      const from = box(a.source);
      const to = box(a.target);
      if (!from || !to) return;
      let dx = center(to).x - center(from).x;
      let dy = center(to).y - center(from).y;
      const start = edgePoint(from, dx, dy);
      const end = edgePoint(to, -dx, -dy);
      // Two arrows between the same boxes in opposite directions sit side by side, not on top of each other.
      const back = arrows.some((o) => o.source === a.target && o.target === a.source);
      const len = Math.hypot(dx, dy) || 1;
      const off = back ? 7 : 0;
      const ox = (-dy / len) * off;
      const oy = (dx / len) * off;
      const g = node("g", { class: `wb-arrow${isSelected("arrow", i) ? " selected" : ""}`, "data-arrow": i }, svg);
      const d = `M${start.x + ox},${start.y + oy} L${end.x + ox},${end.y + oy}`;
      node("path", { d, class: "wb-hit" }, g);
      node("path", { d, class: "wb-line", "marker-end": "url(#wb-head)" }, g);
      if (a.label) {
        const t = node("text", { x: (start.x + end.x) / 2 + ox, y: (start.y + end.y) / 2 + oy - 6, class: "wb-arrow-label" }, g);
        t.textContent = a.label;
      }
    });

    for (const b of boxes) {
      const w = width(b);
      const g = node("g", { class: `wb-box kind-${b.kind}${isSelected("box", b.id) ? " selected" : ""}`, "data-box": b.id, transform: `translate(${b.x},${b.y})` }, svg);
      node("rect", { width: w, height: BOX_H, rx: b.kind === "database" ? 18 : 8, class: "wb-shape" }, g);
      const kind = node("text", { x: 12, y: 19, class: "wb-kind" }, g);
      kind.textContent = KINDS[b.kind];
      const label = node("text", { x: 12, y: 40, class: "wb-label" }, g);
      label.textContent = b.label || "Unnamed";
      if (b.kind !== "note") node("circle", { cx: w, cy: BOX_H / 2, r: 7, class: "wb-handle", "data-handle": b.id }, g);
    }

    if (drag && drag.type === "connect") {
      node("path", { d: `M${drag.from.x},${drag.from.y} L${drag.to.x},${drag.to.y}`, class: "wb-line wb-draft", "marker-end": "url(#wb-head)" }, svg);
    }
    syncTools();
  }

  function isSelected(type, key) {
    return selected && selected.type === type && (type === "box" ? selected.id === key : selected.index === key);
  }

  function syncTools() {
    // The connect form lists every component, keeping what was picked.
    const parts = boxes.filter((b) => b.kind !== "note");
    for (const select of [ui.from, ui.to]) {
      const keep = select.value;
      select.replaceChildren(...parts.map((b) => new Option(`${b.label || "Unnamed"} (${KINDS[b.kind].toLowerCase()})`, b.id)));
      if (parts.some((b) => b.id === keep)) select.value = keep;
    }
    if (parts.length > 1 && ui.from.value === ui.to.value) ui.to.selectedIndex = (ui.from.selectedIndex + 1) % parts.length;
    ui.connect.disabled = parts.length < 2;

    const item = selectedItem();
    ui.label.disabled = !item;
    ui.remove.disabled = !item;
    if (document.activeElement !== ui.label) ui.label.value = item ? item.label : "";
    ui.label.placeholder = !item ? "Select a box or arrow" : selected.type === "box" ? "Name this box" : "What flows along it";
  }

  // Mouse and touch

  function renameSoon() {
    // After the browser has finished moving focus for this click.
    setTimeout(() => ui.label.focus(), 0);
  }

  svg.addEventListener("pointerdown", (e) => {
    const p = toSvg(e);
    const handle = e.target.closest("[data-handle]");
    const boxNode = e.target.closest("[data-box]");
    const arrowNode = e.target.closest("[data-arrow]");
    svg.focus();

    // Double-clicks are spotted here, not with dblclick: the board is redrawn on every click, which removes
    // the element the browser would send dblclick to.
    const double = lastDown && e.timeStamp - lastDown.t < 450 && Math.hypot(p.x - lastDown.x, p.y - lastDown.y) < 8;
    lastDown = double ? null : { t: e.timeStamp, x: p.x, y: p.y };
    if (double && !handle) {
      const hit = boxAt(p);
      if (hit) {
        selected = { type: "box", id: hit.id };
        render();
      } else {
        add(ui.kind.value, "", { x: p.x - 60, y: p.y - BOX_H / 2 });
      }
      renameSoon();
      return;
    }

    if (handle) {
      const b = box(handle.dataset.handle);
      drag = { type: "connect", source: b.id, from: { x: b.x + width(b), y: b.y + BOX_H / 2 }, to: p };
    } else if (boxNode) {
      const b = box(boxNode.dataset.box);
      selected = { type: "box", id: b.id };
      drag = { type: "move", id: b.id, dx: p.x - b.x, dy: p.y - b.y, moved: false };
    } else if (arrowNode) {
      selected = { type: "arrow", index: Number(arrowNode.dataset.arrow) };
    } else {
      selected = null;
    }
    if (drag) svg.setPointerCapture(e.pointerId);
    render();
  });

  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = toSvg(e);
    if (drag.type === "move") {
      const b = box(drag.id);
      b.x = p.x - drag.dx;
      b.y = p.y - drag.dy;
      clamp(b);
      drag.moved = true;
    } else {
      drag.to = p;
    }
    render();
  });

  svg.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const done = drag;
    drag = null;
    if (done.type === "connect") {
      const target = boxAt(toSvg(e));
      if (!target || target.kind === "note" || !connect(done.source, target.id)) render();
      else renameSoon();
    } else {
      render();
    }
  });

  // Keyboard

  svg.addEventListener("keydown", (e) => {
    const item = selectedItem();
    if ((e.key === "Delete" || e.key === "Backspace") && item) {
      e.preventDefault();
      removeSelected();
    } else if (e.key === "Enter" && item) {
      e.preventDefault();
      ui.label.focus();
    } else if (selected && selected.type === "box" && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 40 : 10;
      const b = box(selected.id);
      b.x += { ArrowLeft: -step, ArrowRight: step }[e.key] || 0;
      b.y += { ArrowUp: -step, ArrowDown: step }[e.key] || 0;
      clamp(b);
      render();
    } else if (e.key === "Escape") {
      selected = null;
      render();
    }
  });

  // The toolbar

  ui.addForm.addEventListener("submit", (e) => {
    e.preventDefault();
    add(ui.kind.value, ui.newLabel.value);
    ui.newLabel.value = "";
    ui.newLabel.focus();
  });

  ui.connectForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (connect(ui.from.value, ui.to.value, ui.arrowLabel.value)) ui.arrowLabel.value = "";
  });

  ui.label.addEventListener("input", () => {
    const item = selectedItem();
    if (!item) return;
    item.label = ui.label.value.slice(0, 80);
    changed();
  });

  ui.label.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "Escape") {
      e.preventDefault();
      svg.focus();
    }
  });

  ui.remove.addEventListener("click", removeSelected);

  render();
  return {
    toJSON() {
      return {
        boxes: boxes.map((b) => ({ id: b.id, kind: b.kind, label: b.label.trim(), x: Math.round(b.x), y: Math.round(b.y) })),
        arrows: arrows.map((a) => ({ ...a, label: a.label.trim() })),
      };
    },
    // What the interviewer would read, ignoring where things sit, so moving a box doesn't count as a change.
    signature() {
      const parts = boxes.map((b) => `${b.id}|${b.kind}|${b.label.trim()}`).sort();
      const links = arrows.map((a) => `${a.source}>${a.target}|${a.label.trim()}`).sort();
      return JSON.stringify([parts, links]);
    },
    get size() {
      return { boxes: boxes.length, arrows: arrows.length };
    },
    clear() {
      boxes = [];
      arrows = [];
      selected = null;
      nextId = 1;
      render();
    },
  };
}

window.whiteboard = whiteboard;
