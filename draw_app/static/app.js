// cadLLM — sketch & word client.
//
// Two interlocked input modalities feed a single op list:
//   1) Drawing strokes on the canvas → the sketch_extrude op (op 0 by convention).
//   2) Typed commands → fillet / chamfer / hole / mirror / pattern ops appended.
//
// The innovation is the *inline contextual input*: clicking on (or near) a stroke
// opens a text field anchored at that location. What you type is interpreted
// relative to the click target — a dimension on the clicked stroke, or a global
// command if you clicked empty canvas. This collapses "select then act" into one
// gesture and removes the always-on prompt textbox tax for tiny edits.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

// ---- DOM refs ---------------------------------------------------------------
const canvas = document.getElementById('sketch');
const viewerEl = document.getElementById('viewer');
const heightInput = document.getElementById('height');
const sizeInput = document.getElementById('size');
const planeSelect = document.getElementById('plane');
const snapCheck = document.getElementById('snap');
const liveCheck = document.getElementById('live');
const generateBtn = document.getElementById('generate');
const undoBtn = document.getElementById('undo');
const clearBtn = document.getElementById('clear');
const saveBtn = document.getElementById('save');
const statusEl = document.getElementById('status');
const interpEl = document.getElementById('interpretation');
const timelineEl = document.getElementById('timeline');
const globalPromptInput = document.getElementById('globalPrompt');
const globalPromptGo = document.getElementById('globalPromptGo');
const inlineInputEl = document.getElementById('inlineInput');
const inlineInputField = document.getElementById('inlineInputField');
const inlineInputHelp = document.getElementById('inlineInputHelp');

// ---- canvas state -----------------------------------------------------------
const ctx = canvas.getContext('2d');
const SNAP_PX = 14;
const HIT_TOLERANCE_PX = 18;

// Each stroke = { points: [[x,y],...], annotation: null | {kind, value_mm}, kind: null|"circle"|"rect"|"polygon" }
let strokes = [];
let currentStroke = null;
let drawing = false;
let lastInterpretations = [];
let extraOps = [];          // ops beyond the sketch (fillet, hole, etc.)
let inlineAnchor = null;    // { strokeIndex: number | null, x, y }

const STROKE_COLORS = {
  outer: '#2962ff', hole: '#e53935', additive: '#43a047', skipped: '#888', unknown: '#888',
};

function fitCanvas() {
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.floor(r.width);
  canvas.height = Math.floor(r.height);
  redraw();
}
window.addEventListener('resize', fitCanvas);

function snap(p) {
  if (!snapCheck.checked) return p;
  const s = 20;
  return [Math.round(p[0] / s) * s, Math.round(p[1] / s) * s];
}

function getXY(e) {
  const r = canvas.getBoundingClientRect();
  const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const y = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
  return [x, y];
}

// ---- drawing ---------------------------------------------------------------
function startStroke(e) {
  // If the inline input is open, swallow the canvas click so dragging doesn't
  // start a stroke underneath it.
  if (!inlineInputEl.classList.contains('hidden')) return;
  e.preventDefault();
  drawing = true;
  currentStroke = { points: [snap(getXY(e))], annotation: null, kind: null };
  strokes.push(currentStroke);
}
function extendStroke(e) {
  if (!drawing) return;
  e.preventDefault();
  currentStroke.points.push(snap(getXY(e)));
  redraw();
}
function endStroke(e) {
  if (!drawing) return;
  e.preventDefault();
  drawing = false;
  currentStroke = null;
  redraw();
  if (liveCheck.checked && strokes.length > 0) {
    scheduleBuild();
  }
}

canvas.addEventListener('mousedown', startStroke);
canvas.addEventListener('mousemove', extendStroke);
canvas.addEventListener('mouseup', endStroke);
canvas.addEventListener('mouseleave', endStroke);
canvas.addEventListener('touchstart', startStroke, { passive: false });
canvas.addEventListener('touchmove', extendStroke, { passive: false });
canvas.addEventListener('touchend', endStroke, { passive: false });

// ---- click-to-annotate ------------------------------------------------------
// A click without drag opens the inline input. The position determines whether
// it's anchored to a stroke (per-stroke dimension) or a global command.
let lastDownXY = null;
let lastDownTime = 0;
canvas.addEventListener('mousedown', (e) => { lastDownXY = getXY(e); lastDownTime = performance.now(); });
canvas.addEventListener('mouseup', (e) => {
  if (!lastDownXY) return;
  const xy = getXY(e);
  const dist = Math.hypot(xy[0] - lastDownXY[0], xy[1] - lastDownXY[1]);
  const dt = performance.now() - lastDownTime;
  // Treat as a click (not a drag) if movement < 4px and the stroke has ≤ 2 points
  // (a quick click can still register a tiny stroke). Remove the tiny stroke and
  // pop the inline input instead.
  if (dist < 4 && dt < 400) {
    // Discard any tiny stroke that the mouseup just finalized.
    if (strokes.length > 0 && strokes[strokes.length - 1].points.length <= 3) {
      strokes.pop();
    }
    openInlineInput(xy);
  }
});

function openInlineInput(canvasXY) {
  const hit = pickStroke(canvasXY);
  inlineAnchor = { strokeIndex: hit, x: canvasXY[0], y: canvasXY[1] };
  // Position the input. canvas-wrap is the offsetParent.
  const left = Math.min(canvasXY[0] + 8, canvas.width - 200);
  const top = Math.min(canvasXY[1] + 8, canvas.height - 60);
  inlineInputEl.style.left = `${left}px`;
  inlineInputEl.style.top = `${top}px`;
  inlineInputEl.classList.remove('hidden');
  inlineInputField.value = '';
  if (hit !== null) {
    const interp = lastInterpretations[hit];
    const desc = interp ? interp.description : 'unclassified stroke';
    inlineInputField.placeholder = '⌀10mm  /  width 30mm  /  fillet 2mm';
    inlineInputHelp.textContent = `Anchored to stroke ${hit + 1}: ${desc}`;
  } else {
    inlineInputField.placeholder = 'fillet all edges 2mm  /  add a 5mm hole at center';
    inlineInputHelp.textContent = 'Global command (no stroke targeted)';
  }
  // Defer focus so the click event finishes first.
  setTimeout(() => inlineInputField.focus(), 0);
  redraw();
}

function closeInlineInput() {
  inlineInputEl.classList.add('hidden');
  inlineAnchor = null;
  redraw();
}

inlineInputField.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeInlineInput(); return; }
  if (e.key !== 'Enter') return;
  const text = inlineInputField.value.trim();
  if (!text) { closeInlineInput(); return; }
  commitInlineInput(text);
});

function commitInlineInput(text) {
  if (inlineAnchor && inlineAnchor.strokeIndex !== null) {
    // Try as a per-stroke dimension first.
    const dim = parseDimension(text);
    if (dim) {
      strokes[inlineAnchor.strokeIndex].annotation = dim;
      closeInlineInput();
      setStatus(`Stroke ${inlineAnchor.strokeIndex + 1}: ${dim.kind} = ${dim.value_mm}mm`, 'ok');
      if (liveCheck.checked) scheduleBuild();
      return;
    }
  }
  // Else treat as a global command — POST to /parse and append ops.
  parseAndAppend(text).then(() => {
    closeInlineInput();
  });
}

function parseDimension(text) {
  // Patterns:
  //   "⌀10mm" / "dia 10mm" / "d=10" / "10mm dia" → diameter
  //   "10mm" / "10" alone → size (uses stroke's longest extent)
  //   "width 30mm" / "w=30" → width
  //   "height 12mm" / "h=12" → height
  const t = text.replace(/Ø|⌀/g, 'dia').toLowerCase().trim();
  let m;
  m = t.match(/(?:dia|diameter|d)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'diameter', value_mm: parseFloat(m[1]) };
  m = t.match(/(?:width|w)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'width', value_mm: parseFloat(m[1]) };
  m = t.match(/(?:height|h)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'height', value_mm: parseFloat(m[1]) };
  // Bare "10mm" or "10" → size (only applies if no other word)
  m = t.match(/^(\d+(?:\.\d+)?)\s*(?:mm)?$/);
  if (m) return { kind: 'size', value_mm: parseFloat(m[1]) };
  return null;
}

async function parseAndAppend(text) {
  setStatus('Parsing…');
  try {
    const r = await fetch('/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    if (j.operations.length === 0) {
      setStatus(`Didn't understand: "${text}"`, 'error');
      return;
    }
    extraOps.push(...j.operations);
    if (j.unparsed.length > 0) {
      setStatus(`Parsed ${j.operations.length} op(s). Didn't understand: ${j.unparsed.join('; ')}`, 'error');
    } else {
      setStatus(`Parsed ${j.operations.length} op(s).`, 'ok');
    }
    renderTimeline();
    if (liveCheck.checked) scheduleBuild();
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

// Global prompt at the bottom of the canvas pane.
function submitGlobalPrompt() {
  const text = globalPromptInput.value.trim();
  if (!text) return;
  globalPromptInput.value = '';
  parseAndAppend(text);
}
globalPromptGo.addEventListener('click', submitGlobalPrompt);
globalPromptInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') submitGlobalPrompt();
});

// ---- stroke picking ---------------------------------------------------------
function pickStroke(xy) {
  // Returns the index of the stroke whose nearest point is within HIT_TOLERANCE_PX.
  let best = { idx: -1, d: HIT_TOLERANCE_PX };
  for (let i = 0; i < strokes.length; i++) {
    for (const p of strokes[i].points) {
      const d = Math.hypot(p[0] - xy[0], p[1] - xy[1]);
      if (d < best.d) best = { idx: i, d };
    }
  }
  return best.idx === -1 ? null : best.idx;
}

// ---- drawing helpers --------------------------------------------------------
function strokeColor(idx) {
  const interp = lastInterpretations[idx];
  if (!interp) return STROKE_COLORS.unknown;
  return STROKE_COLORS[interp.role] || STROKE_COLORS.unknown;
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  // Grid.
  ctx.save();
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--grid') || 'rgba(120,120,120,0.16)';
  ctx.lineWidth = 1;
  const step = 30;
  for (let x = step; x < canvas.width; x += step) { ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,canvas.height); ctx.stroke(); }
  for (let y = step; y < canvas.height; y += step) { ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(canvas.width,y); ctx.stroke(); }
  ctx.restore();

  // Strokes.
  ctx.lineWidth = 2.5;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  strokes.forEach((s, idx) => {
    if (s.points.length < 2) return;
    ctx.strokeStyle = strokeColor(idx);
    ctx.beginPath();
    ctx.moveTo(s.points[0][0], s.points[0][1]);
    for (let i = 1; i < s.points.length; i++) ctx.lineTo(s.points[i][0], s.points[i][1]);
    ctx.stroke();
  });

  // Snapped primitives — faint dashed overlay of the classifier's interpretation.
  ctx.lineWidth = 1.2;
  ctx.setLineDash([4, 4]);
  strokes.forEach((s, idx) => {
    const interp = lastInterpretations[idx];
    if (!interp || interp.role === 'skipped') return;
    const c = strokeAnchor(s);
    if (!c) return;
    ctx.strokeStyle = strokeColor(idx) + 'cc';
    if (interp.kind === 'circle') {
      ctx.beginPath();
      ctx.arc(c.cx, c.cy, (c.w + c.h) / 4, 0, Math.PI * 2);
      ctx.stroke();
      // Center mark — a small + at the centroid (engineering drawing convention).
      ctx.beginPath();
      ctx.moveTo(c.cx - 6, c.cy); ctx.lineTo(c.cx + 6, c.cy);
      ctx.moveTo(c.cx, c.cy - 6); ctx.lineTo(c.cx, c.cy + 6);
      ctx.stroke();
    } else if (interp.kind === 'rect') {
      ctx.strokeRect(c.cx - c.w / 2, c.cy - c.h / 2, c.w, c.h);
    }
  });
  ctx.setLineDash([]);

  // Dimension labels for annotated strokes.
  ctx.font = '11px ui-monospace, monospace';
  ctx.textBaseline = 'middle';
  strokes.forEach((s, idx) => {
    if (!s.annotation) return;
    const c = strokeAnchor(s);
    if (!c) return;
    const label = formatAnnotation(s.annotation);
    const labelX = c.cx + c.w / 2 + 8;
    const labelY = c.cy;
    ctx.fillStyle = strokeColor(idx);
    // Small dimension leader.
    ctx.strokeStyle = strokeColor(idx);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(c.cx + c.w / 2, c.cy);
    ctx.lineTo(labelX - 3, labelY);
    ctx.stroke();
    // Label background for legibility.
    const m = ctx.measureText(label);
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--panel');
    ctx.fillRect(labelX - 2, labelY - 8, m.width + 4, 16);
    ctx.fillStyle = strokeColor(idx);
    ctx.fillText(label, labelX, labelY);
  });

  // Inline input anchor halo.
  if (inlineAnchor && inlineAnchor.strokeIndex !== null) {
    const s = strokes[inlineAnchor.strokeIndex];
    const c = strokeAnchor(s);
    if (c) {
      ctx.strokeStyle = '#2962ff';
      ctx.lineWidth = 2;
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(c.cx - c.w / 2 - 4, c.cy - c.h / 2 - 4, c.w + 8, c.h + 8);
      ctx.setLineDash([]);
    }
  }
}

function strokeAnchor(stroke) {
  if (stroke.points.length < 2) return null;
  const xs = stroke.points.map(p => p[0]);
  const ys = stroke.points.map(p => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs);
  const miny = Math.min(...ys), maxy = Math.max(...ys);
  return { cx: (minx + maxx) / 2, cy: (miny + maxy) / 2, w: maxx - minx, h: maxy - miny };
}

function formatAnnotation(a) {
  const sym = { diameter: '⌀', width: 'W', height: 'H', size: '' }[a.kind] || '';
  return `${sym}${a.value_mm.toFixed(1)}mm`;
}

// ---- interpretation chips ---------------------------------------------------
function updateInterpretationPanel() {
  if (lastInterpretations.length === 0) {
    interpEl.innerHTML = '<span class="muted small">Strokes appear here after the model builds.</span>';
    return;
  }
  interpEl.innerHTML = lastInterpretations.map((it, i) => {
    const colour = STROKE_COLORS[it.role] || STROKE_COLORS.unknown;
    return `<span class="chip" style="--role:${colour}; border-color:${colour}; color:${colour}">
      <span class="idx">${i + 1}</span> ${escapeHtml(it.description)}
    </span>`;
  }).join('');
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
}

// ---- ops timeline -----------------------------------------------------------
function renderTimeline() {
  const items = [];
  if (strokes.length > 0) {
    const summary = lastInterpretations.length > 0
      ? lastInterpretations.map(i => i.description).join(' · ')
      : `${strokes.length} stroke${strokes.length === 1 ? '' : 's'} (not yet built)`;
    items.push({ type: 'sketch_extrude', summary, isSketch: true });
  }
  extraOps.forEach((op, i) => {
    items.push({ type: op.op, summary: opSummary(op), opIndex: i });
  });

  if (items.length === 0) {
    timelineEl.innerHTML = '<div class="timeline-empty">No operations yet.</div>';
    return;
  }
  timelineEl.innerHTML = items.map((it, i) => `
    <li class="${it.error ? 'error' : 'ok'}">
      <span class="op-num">${i + 1}</span>
      <span class="op-type">${escapeHtml(it.type)}</span>
      <span class="op-summary">${escapeHtml(it.summary)}</span>
      ${it.isSketch
        ? '<span class="op-delete" title="clear strokes to delete this op">⋯</span>'
        : `<button class="op-delete" data-extra-idx="${it.opIndex}" title="remove">×</button>`}
    </li>
  `).join('');
  timelineEl.querySelectorAll('button.op-delete').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = parseInt(btn.dataset.extraIdx, 10);
      extraOps.splice(idx, 1);
      renderTimeline();
      if (liveCheck.checked && strokes.length > 0) scheduleBuild();
    });
  });
}

function opSummary(op) {
  switch (op.op) {
    case 'fillet':  return `radius ${op.radius_mm}mm · ${op.target}`;
    case 'chamfer': return `dist ${op.distance_mm}mm · ${op.target}`;
    case 'hole':    return `⌀${op.diameter_mm}mm at (${op.x_mm},${op.y_mm})`;
    case 'set_height': return `${op.value_mm}mm tall`;
    case 'pattern_linear': return `${op.count}× along ${op.axis} @ ${op.spacing_mm}mm`;
    case 'mirror':  return `across ${op.plane}`;
    default: return JSON.stringify(op);
  }
}

// ---- three.js viewer (carry over from previous version) ---------------------
let renderer, scene, camera, controls, currentMesh = null;

function initViewer() {
  const w = viewerEl.clientWidth, h = viewerEl.clientHeight;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1e1e1e);

  camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 5000);
  camera.position.set(120, 100, 120);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(w, h);
  viewerEl.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 0, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const d1 = new THREE.DirectionalLight(0xffffff, 0.85);
  d1.position.set(80, 120, 60);
  scene.add(d1);
  const d2 = new THREE.DirectionalLight(0xffffff, 0.35);
  d2.position.set(-100, 40, -80);
  scene.add(d2);

  const grid = new THREE.GridHelper(200, 20, 0x444444, 0x2a2a2a);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);
  scene.add(new THREE.AxesHelper(40));

  window.addEventListener('resize', onViewerResize);
  animate();
}

function onViewerResize() {
  const w = viewerEl.clientWidth, h = viewerEl.clientHeight;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function clearViewer() {
  if (currentMesh) {
    scene.remove(currentMesh);
    currentMesh.geometry.dispose();
    currentMesh.material.dispose();
    currentMesh = null;
  }
}

function showSTL(buffer) {
  clearViewer();
  const geom = new STLLoader().parse(buffer);
  geom.computeVertexNormals();
  geom.computeBoundingBox();
  const bb = geom.boundingBox;
  const cx = (bb.max.x + bb.min.x) / 2;
  const cy = (bb.max.y + bb.min.y) / 2;
  geom.translate(-cx, -cy, 0);

  const mat = new THREE.MeshStandardMaterial({
    color: 0xb88a2c, metalness: 0.1, roughness: 0.6, flatShading: false
  });
  currentMesh = new THREE.Mesh(geom, mat);
  scene.add(currentMesh);

  const size = new THREE.Vector3();
  bb.getSize(size);
  const longest = Math.max(size.x, size.y, size.z, 1);
  const dist = longest * 2.2;
  camera.position.set(dist, dist * 0.8, dist);
  controls.target.set(0, 0, size.z / 2);
  controls.update();
}

// ---- build with debounce ---------------------------------------------------
let pendingTimer = null;
function scheduleBuild(delayMs = 380) {
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => { pendingTimer = null; build(); }, delayMs);
}

function buildRequest() {
  const ops = [];
  // Sketch op (always first if any strokes).
  if (strokes.length > 0) {
    const strokesPayload = strokes.map(s => ({
      points: s.points,
      annotation: s.annotation,
    }));
    ops.push({
      op: 'sketch_extrude',
      strokes: strokesPayload,
      height_mm: parseFloat(heightInput.value) || 10,
      plane: planeSelect.value,
    });
  }
  ops.push(...extraOps);
  return {
    operations: ops,
    canvas_width: canvas.width,
    canvas_height: canvas.height,
    target_size_mm: parseFloat(sizeInput.value) || 60,
  };
}

async function build() {
  if (strokes.length === 0 && extraOps.length === 0) {
    setStatus('Draw something or type a command.', 'error');
    return;
  }
  setStatus('Building…');
  try {
    const r = await fetch('/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildRequest()),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    lastInterpretations = (j.stroke_interpretations || []).filter(i => i.op_index === 0);
    redraw();
    updateInterpretationPanel();

    // Show op feedback in the timeline (which we'll regenerate with status flags).
    renderTimelineWithFeedback(j.feedback || []);

    const binStr = atob(j.stl_base64);
    const buf = new ArrayBuffer(binStr.length);
    const view = new Uint8Array(buf);
    for (let i = 0; i < binStr.length; i++) view[i] = binStr.charCodeAt(i);
    showSTL(buf);
    const errorCount = (j.feedback || []).filter(f => f.status === 'error').length;
    if (errorCount > 0) {
      setStatus(`${errorCount} op(s) failed — see timeline.`, 'error');
    } else {
      setStatus(`Built ${j.feedback.length} op(s).`, 'ok');
    }
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

function renderTimelineWithFeedback(feedback) {
  // Re-render the timeline but annotate each op with its server-side status.
  const sketchActive = strokes.length > 0;
  const items = [];
  let feedbackIdx = 0;
  if (sketchActive) {
    const fb = feedback[feedbackIdx++];
    items.push({
      type: 'sketch_extrude',
      summary: fb ? fb.summary : `${strokes.length} stroke(s)`,
      isSketch: true,
      error: fb && fb.status === 'error',
    });
  }
  extraOps.forEach((op, i) => {
    const fb = feedback[feedbackIdx++];
    items.push({
      type: op.op,
      summary: fb && fb.status !== 'ok' ? `${opSummary(op)} — ${fb.summary}` : opSummary(op),
      opIndex: i,
      error: fb && fb.status === 'error',
    });
  });

  if (items.length === 0) {
    timelineEl.innerHTML = '<div class="timeline-empty">No operations yet.</div>';
    return;
  }
  timelineEl.innerHTML = items.map((it, i) => `
    <li class="${it.error ? 'error' : 'ok'}">
      <span class="op-num">${i + 1}</span>
      <span class="op-type">${escapeHtml(it.type)}</span>
      <span class="op-summary">${escapeHtml(it.summary)}</span>
      ${it.isSketch
        ? '<span class="op-delete" title="clear strokes to delete this op">⋯</span>'
        : `<button class="op-delete" data-extra-idx="${it.opIndex}" title="remove">×</button>`}
    </li>
  `).join('');
  timelineEl.querySelectorAll('button.op-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.extraIdx, 10);
      extraOps.splice(idx, 1);
      renderTimeline();
      if (liveCheck.checked && strokes.length > 0) scheduleBuild();
    });
  });
}

function setStatus(msg, kind = '') {
  statusEl.textContent = msg;
  statusEl.className = kind;
}

// ---- buttons ---------------------------------------------------------------
undoBtn.addEventListener('click', () => {
  strokes.pop();
  lastInterpretations = [];
  redraw();
  updateInterpretationPanel();
  renderTimeline();
  if (strokes.length === 0 && extraOps.length === 0) {
    clearViewer();
    setStatus('Empty.');
  } else if (liveCheck.checked) {
    scheduleBuild();
  }
});

clearBtn.addEventListener('click', () => {
  strokes = []; extraOps = []; lastInterpretations = [];
  redraw();
  updateInterpretationPanel();
  renderTimeline();
  clearViewer();
  setStatus('Cleared.');
});

generateBtn.addEventListener('click', build);

saveBtn.addEventListener('click', async () => {
  const name = prompt('Save as cad/<name>.py — enter snake_case name:', 'my_part');
  if (!name) return;
  if (!/^[a-z][a-z0-9_]*$/.test(name)) {
    setStatus(`Invalid name "${name}". Use snake_case starting with a letter.`, 'error');
    return;
  }
  if (strokes.length === 0 && extraOps.length === 0) {
    setStatus('Nothing to save.', 'error'); return;
  }
  try {
    const body = buildRequest();
    body.save_as = name;
    const r = await fetch('/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    setStatus(`Saved → ${j.path}`, 'ok');
  } catch (e) {
    setStatus(e.message, 'error');
  }
});

[heightInput, sizeInput, planeSelect, snapCheck].forEach(el => {
  el.addEventListener('change', () => {
    if (liveCheck.checked && strokes.length > 0) scheduleBuild(80);
  });
});

// Close the inline input on outside click.
document.addEventListener('mousedown', (e) => {
  if (inlineInputEl.classList.contains('hidden')) return;
  if (!inlineInputEl.contains(e.target) && e.target !== canvas) {
    closeInlineInput();
  }
});

// ---- boot ------------------------------------------------------------------
fitCanvas();
initViewer();
renderTimeline();
updateInterpretationPanel();
setStatus('Ready. Draw a closed shape — click it to annotate (⌀10mm) or click empty canvas to type a command.');
