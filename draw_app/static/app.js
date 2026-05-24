// cadLLM — sketch & word client.
//
// Two interlocked input modalities feed a single op list:
//   1) Drawing strokes on the canvas → the sketch_extrude op (op 0 by convention).
//   2) Typed commands → fillet / chamfer / hole / mirror / pattern ops appended.
//
// Innovations:
//   - Inline contextual input: clicking on/near a stroke opens a text field
//     anchored at the click; what you type is interpreted relative to the
//     click target (dimension on the clicked stroke, or a global command).
//   - Engineering-drawing aesthetics: center marks on classified circles,
//     dimension leaders + labels for annotated strokes, role-coloured strokes.
//   - Fusion-style keyboard shortcuts (L/R/C/D/E/F/H/M/X/Ctrl+S/O/Z).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

// ---- DOM refs ---------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const canvas = $('sketch');
const viewerEl = $('viewer');
const heightInput = $('height');
const sizeInput = $('size');
const planeSelect = $('plane');
const modeSelect = $('mode');
const constructionCheck = $('construction');
const snapCheck = $('snap');
const liveCheck = $('live');
const generateBtn = $('generate');
const undoBtn = $('undo');
const clearBtn = $('clear');
const saveBtn = $('save');
const partsBtn = $('partsBtn');
const helpBtn = $('help');
const downloadMenu = $('downloadMenu');
const downloadBtn = $('downloadBtn');
const inspectBtn = $('inspectBtn');
const statusEl = $('status');
const interpEl = $('interpretation');
const timelineEl = $('timeline');
const dofPill = $('dofPill');
const meshPill = $('meshPill');
const inspectText = $('inspect');
const globalPromptInput = $('globalPrompt');
const globalPromptGo = $('globalPromptGo');
const inlineInputEl = $('inlineInput');
const inlineInputField = $('inlineInputField');
const inlineInputHelp = $('inlineInputHelp');
const partsModal = $('partsModal');
const partsList = $('partsList');
const partsClose = $('partsClose');
const helpModal = $('helpModal');
const helpClose = $('helpClose');
const viewcube = $('viewcube');
const rollbackInput = $('rollback');
const rollbackLabel = $('rollbackLabel');
const clipZInput = $('clipZ');
const clipZLabel = $('clipZLabel');
const hardwareMenu = $('hardwareMenu');
const hardwareBtn = $('hardwareBtn');
const hardwareList = $('hardwareList');
const shareBtn = $('shareBtn');

// Standard hardware catalog — common metric socket-head cap screws.
// Clearance hole diameters per ASME / ISO close-fit. Numbers are mm.
const HARDWARE = [
  { id: 'm3',  name: 'M3 (clearance hole)',  diameter: 3.4 },
  { id: 'm4',  name: 'M4 (clearance hole)',  diameter: 4.5 },
  { id: 'm5',  name: 'M5 (clearance hole)',  diameter: 5.5 },
  { id: 'm6',  name: 'M6 (clearance hole)',  diameter: 6.6 },
  { id: 'm8',  name: 'M8 (clearance hole)',  diameter: 9.0 },
  { id: 'm10', name: 'M10 (clearance hole)', diameter: 11.0 },
  { id: 'shaft-6', name: '6mm dowel shaft', diameter: 6.0 },
  { id: 'shaft-8', name: '8mm dowel shaft', diameter: 8.0 },
];

// Rollback / clip state
let rollbackLimit = -1;   // -1 = include all ops
let clipFraction = 1.0;   // 1.0 = no clip

// ---- canvas state -----------------------------------------------------------
const ctx = canvas.getContext('2d');
const HIT_TOLERANCE_PX = 18;
const SNAP_PX = 20;

// Each stroke = { points, annotation, construction }
let strokes = [];
let currentStroke = null;
let drawing = false;
let lastInterpretations = [];
let extraOps = [];
let inlineAnchor = null;
let lastSTLBuffer = null;

const STROKE_COLORS = {
  outer: '#2962ff', hole: '#e53935', additive: '#43a047',
  construction: '#b88a2c', skipped: '#888', unknown: '#888',
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
  return [Math.round(p[0] / SNAP_PX) * SNAP_PX, Math.round(p[1] / SNAP_PX) * SNAP_PX];
}

function getXY(e) {
  const r = canvas.getBoundingClientRect();
  const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const y = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
  return [x, y];
}

// ---- drawing ---------------------------------------------------------------
let lastDownXY = null;
let lastDownTime = 0;

function startStroke(e) {
  if (!inlineInputEl.classList.contains('hidden')) return;
  e.preventDefault();
  drawing = true;
  currentStroke = {
    points: [snap(getXY(e))],
    annotation: null,
    construction: constructionCheck.checked,
  };
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
  if (liveCheck.checked && strokes.length > 0) scheduleBuild();
}

canvas.addEventListener('mousedown', (e) => { lastDownXY = getXY(e); lastDownTime = performance.now(); startStroke(e); });
canvas.addEventListener('mousemove', extendStroke);
canvas.addEventListener('mouseup', (e) => {
  endStroke(e);
  // Click detection (small movement + short hold)
  if (!lastDownXY) return;
  const xy = getXY(e);
  const dist = Math.hypot(xy[0] - lastDownXY[0], xy[1] - lastDownXY[1]);
  const dt = performance.now() - lastDownTime;
  if (dist < 4 && dt < 400) {
    if (strokes.length > 0 && strokes[strokes.length - 1].points.length <= 3) strokes.pop();
    openInlineInput(xy);
  }
});
canvas.addEventListener('mouseleave', endStroke);
canvas.addEventListener('touchstart', startStroke, { passive: false });
canvas.addEventListener('touchmove', extendStroke, { passive: false });
canvas.addEventListener('touchend', endStroke, { passive: false });

constructionCheck.addEventListener('change', () => {
  canvas.classList.toggle('construction-mode', constructionCheck.checked);
});

// ---- click-to-annotate ------------------------------------------------------
function openInlineInput(canvasXY) {
  const hit = pickStroke(canvasXY);
  inlineAnchor = { strokeIndex: hit, x: canvasXY[0], y: canvasXY[1] };
  const left = Math.min(canvasXY[0] + 8, canvas.width - 220);
  const top = Math.min(canvasXY[1] + 8, canvas.height - 60);
  inlineInputEl.style.left = `${left}px`;
  inlineInputEl.style.top = `${top}px`;
  inlineInputEl.classList.remove('hidden');
  inlineInputField.value = '';
  if (hit !== null) {
    const interp = lastInterpretations[hit];
    const desc = interp ? interp.description : 'unclassified stroke';
    inlineInputField.placeholder = '⌀10mm  /  width 30mm  /  height 12mm';
    inlineInputHelp.textContent = `Anchored to stroke ${hit + 1}: ${desc}`;
  } else {
    inlineInputField.placeholder = 'fillet all edges 2mm  /  5mm hole at center';
    inlineInputHelp.textContent = 'Global command (no stroke targeted)';
  }
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
    const dim = parseDimension(text);
    if (dim) {
      strokes[inlineAnchor.strokeIndex].annotation = dim;
      closeInlineInput();
      setStatus(`Stroke ${inlineAnchor.strokeIndex + 1}: ${dim.kind} = ${dim.value_mm}mm`, 'ok');
      if (liveCheck.checked) scheduleBuild();
      return;
    }
  }
  parseAndAppend(text).then(() => closeInlineInput());
}

function parseDimension(text) {
  const t = text.replace(/Ø|⌀/g, 'dia').toLowerCase().trim();
  let m;
  m = t.match(/(?:dia|diameter|d)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'diameter', value_mm: parseFloat(m[1]) };
  m = t.match(/(?:width|w)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'width', value_mm: parseFloat(m[1]) };
  m = t.match(/(?:height|h)\s*=?\s*(\d+(?:\.\d+)?)\s*(?:mm)?/);
  if (m) return { kind: 'height', value_mm: parseFloat(m[1]) };
  m = t.match(/^(\d+(?:\.\d+)?)\s*(?:mm)?$/);
  if (m) return { kind: 'size', value_mm: parseFloat(m[1]) };
  return null;
}

async function parseAndAppend(text) {
  setStatus('Parsing…');
  try {
    const r = await fetch('/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    if (j.operations.length === 0) {
      setStatus(`Didn't understand: "${text}"`, 'error');
      return;
    }
    extraOps.push(...j.operations);
    const llmTag = j.llm_used ? ' (via LLM)' : '';
    if (j.unparsed.length > 0) {
      setStatus(`Parsed ${j.operations.length} op${llmTag}. Unparsed: ${j.unparsed.join('; ')}`, 'warn');
    } else {
      setStatus(`Parsed ${j.operations.length} op(s)${llmTag}.`, 'ok');
    }
    renderTimeline();
    if (liveCheck.checked) scheduleBuild();
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

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
  if (strokes[idx]?.construction) return STROKE_COLORS.construction;
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
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  strokes.forEach((s, idx) => {
    if (s.points.length < 2) return;
    ctx.strokeStyle = strokeColor(idx);
    ctx.lineWidth = 2.5;
    if (s.construction) {
      ctx.setLineDash([6, 6]);
      ctx.lineWidth = 1.5;
    }
    ctx.beginPath();
    ctx.moveTo(s.points[0][0], s.points[0][1]);
    for (let i = 1; i < s.points.length; i++) ctx.lineTo(s.points[i][0], s.points[i][1]);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // Snapped primitive overlay.
  ctx.lineWidth = 1.2;
  ctx.setLineDash([4, 4]);
  strokes.forEach((s, idx) => {
    if (s.construction) return;
    const interp = lastInterpretations[idx];
    if (!interp || interp.role === 'skipped' || interp.role === 'construction') return;
    const c = strokeAnchor(s);
    if (!c) return;
    ctx.strokeStyle = strokeColor(idx) + 'cc';
    if (interp.kind === 'circle') {
      ctx.beginPath();
      ctx.arc(c.cx, c.cy, (c.w + c.h) / 4, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(c.cx - 6, c.cy); ctx.lineTo(c.cx + 6, c.cy);
      ctx.moveTo(c.cx, c.cy - 6); ctx.lineTo(c.cx, c.cy + 6);
      ctx.stroke();
    } else if (interp.kind === 'rect') {
      ctx.strokeRect(c.cx - c.w / 2, c.cy - c.h / 2, c.w, c.h);
    }
  });
  ctx.setLineDash([]);

  // Dimension labels.
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
    ctx.strokeStyle = strokeColor(idx);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(c.cx + c.w / 2, c.cy);
    ctx.lineTo(labelX - 3, labelY);
    ctx.stroke();
    const m = ctx.measureText(label);
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--panel');
    ctx.fillRect(labelX - 2, labelY - 8, m.width + 4, 16);
    ctx.fillStyle = strokeColor(idx);
    ctx.fillText(label, labelX, labelY);
  });

  // Anchor halo.
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

// ---- interpretation chips & DOF pill ----------------------------------------
function updateInterpretationPanel() {
  if (lastInterpretations.length === 0 && strokes.length === 0) {
    interpEl.innerHTML = '<span class="muted small">Strokes appear here after the model builds.</span>';
  } else {
    interpEl.innerHTML = lastInterpretations.map((it, i) => {
      const colour = STROKE_COLORS[it.role] || STROKE_COLORS.unknown;
      return `<span class="chip" style="--role:${colour}; border-color:${colour}; color:${colour}">
        <span class="idx">${i + 1}</span> ${escapeHtml(it.description)}
      </span>`;
    }).join('');
  }
  updateDofPill();
}

function updateDofPill() {
  const total = strokes.length;
  if (total === 0) {
    dofPill.textContent = 'no sketch yet';
    dofPill.className = 'dof-pill muted small';
    return;
  }
  const classified = lastInterpretations.filter(i => i.role !== 'skipped').length;
  const construction = strokes.filter(s => s.construction).length;
  const annotated = strokes.filter(s => s.annotation).length;
  const skipped = lastInterpretations.filter(i => i.role === 'skipped').length;
  const parts = [`${classified}/${total} ok`];
  if (annotated > 0) parts.push(`${annotated} dimmed`);
  if (construction > 0) parts.push(`${construction} construction`);
  if (skipped > 0) parts.push(`${skipped} skipped`);
  dofPill.textContent = parts.join(' · ');
  if (skipped > 0) dofPill.className = 'dof-pill warn small';
  else if (annotated > 0 && annotated >= classified - construction) dofPill.className = 'dof-pill ok small';
  else dofPill.className = 'dof-pill muted small';
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
}

// ---- ops timeline -----------------------------------------------------------
function renderTimeline(feedback = []) {
  const items = [];
  let feedbackIdx = 0;
  if (strokes.length > 0) {
    const fb = feedback[feedbackIdx++];
    const summary = fb ? fb.summary :
      (lastInterpretations.length > 0
        ? lastInterpretations.map(i => i.description).join(' · ')
        : `${strokes.length} stroke${strokes.length === 1 ? '' : 's'}`);
    items.push({ type: 'sketch_extrude', summary, isSketch: true, error: fb && fb.status === 'error' });
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

  // Update rollback slider's max to the total op count.
  const totalOps = items.length;
  rollbackInput.max = String(totalOps);
  if (rollbackLimit < 0 || rollbackLimit > totalOps) {
    rollbackInput.value = String(totalOps);
    rollbackLabel.textContent = totalOps === 0 ? 'no ops' : 'all ops';
  } else {
    rollbackInput.value = String(rollbackLimit);
    rollbackLabel.textContent = rollbackLimit === totalOps ? 'all ops' : `up to op ${rollbackLimit}`;
  }
  const effectiveLimit = rollbackLimit < 0 ? totalOps : rollbackLimit;

  if (items.length === 0) {
    timelineEl.innerHTML = '<div class="timeline-empty">No operations yet. Draw something, or press <kbd>?</kbd>.</div>';
    return;
  }
  timelineEl.innerHTML = items.map((it, i) => {
    const suppressed = i >= effectiveLimit;
    return `
    <li class="${it.error ? 'error' : 'ok'}${suppressed ? ' suppressed' : ''}">
      <span class="op-num">${i + 1}</span>
      <span class="op-type">${escapeHtml(it.type)}</span>
      <span class="op-summary">${escapeHtml(it.summary)}</span>
      ${it.isSketch
        ? '<span class="op-delete" title="clear strokes to delete">⋯</span>'
        : `<button class="op-delete" data-extra-idx="${it.opIndex}" title="remove">×</button>`}
    </li>`;
  }).join('');
  timelineEl.querySelectorAll('button.op-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.extraIdx, 10);
      extraOps.splice(idx, 1);
      // If rollback was past this index, reduce it.
      if (rollbackLimit > 0) rollbackLimit -= 1;
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
    case 'circular_pattern': return `${op.count}× r=${op.radius_mm}mm`;
    case 'mirror':  return `across ${op.plane}`;
    case 'shell':   return `${op.thickness_mm}mm wall · remove ${op.remove}`;
    case 'revolve': return `${op.angle_deg}° around ${op.axis}`;
    default: return JSON.stringify(op);
  }
}

// ---- three.js viewer --------------------------------------------------------
let renderer, scene, camera, controls, currentMesh = null;
let currentBBox = { x: 60, y: 60, z: 10 };  // mm extents of the last loaded mesh

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
  lastSTLBuffer = buffer;
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
  currentBBox = { x: size.x, y: size.y, z: size.z };
  const longest = Math.max(size.x, size.y, size.z, 1);
  const dist = longest * 2.2;
  camera.position.set(dist, dist * 0.8, dist);
  controls.target.set(0, 0, size.z / 2);
  controls.update();

  meshPill.textContent = `${size.x.toFixed(1)}×${size.y.toFixed(1)}×${size.z.toFixed(1)} mm`;
  meshPill.className = 'dof-pill ok small';
}

function snapView(view) {
  if (!currentMesh) return;
  const longest = Math.max(currentBBox.x, currentBBox.y, currentBBox.z, 10);
  const d = longest * 2.4;
  const ty = currentBBox.z / 2;
  switch (view) {
    case 'iso':   camera.position.set(d, d * 0.8, d); break;
    case 'top':   camera.position.set(0, 0, d * 1.5); break;
    case 'front': camera.position.set(0, -d, ty); break;
    case 'right': camera.position.set(d, 0, ty); break;
  }
  controls.target.set(0, 0, ty);
  controls.update();
}

viewcube.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-view]');
  if (btn) snapView(btn.dataset.view);
});

// ---- build with debounce ---------------------------------------------------
let pendingTimer = null;
function scheduleBuild(delayMs = 380) {
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => { pendingTimer = null; build(); }, delayMs);
}

function allOps() {
  const ops = [];
  if (strokes.length > 0) {
    const strokesPayload = strokes.map(s => ({
      points: s.points,
      annotation: s.annotation,
      construction: s.construction,
    }));
    ops.push({
      op: 'sketch_extrude',
      strokes: strokesPayload,
      height_mm: parseFloat(heightInput.value) || 10,
      plane: planeSelect.value,
      mode: modeSelect.value,
    });
  }
  ops.push(...extraOps);
  return ops;
}

function buildRequest() {
  let ops = allOps();
  // Apply rollback limit: include only ops up to the slider value.
  if (rollbackLimit >= 0 && rollbackLimit < ops.length) {
    ops = ops.slice(0, rollbackLimit);
  }
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
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildRequest()),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    lastInterpretations = (j.stroke_interpretations || []).filter(i => i.op_index === 0);
    redraw();
    updateInterpretationPanel();
    renderTimeline(j.feedback || []);

    const binStr = atob(j.stl_base64);
    const buf = new ArrayBuffer(binStr.length);
    const view = new Uint8Array(buf);
    for (let i = 0; i < binStr.length; i++) view[i] = binStr.charCodeAt(i);
    showSTL(buf);
    const errorCount = (j.feedback || []).filter(f => f.status === 'error').length;
    if (errorCount > 0) setStatus(`${errorCount} op(s) failed — see timeline.`, 'error');
    else setStatus(`Built ${j.feedback.length} op(s).`, 'ok');
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

function setStatus(msg, kind = '') {
  statusEl.textContent = msg;
  statusEl.className = kind;
}

// ---- buttons & menus --------------------------------------------------------
undoBtn.addEventListener('click', undoLastStroke);
function undoLastStroke() {
  strokes.pop();
  lastInterpretations = [];
  redraw();
  updateInterpretationPanel();
  renderTimeline();
  if (strokes.length === 0 && extraOps.length === 0) {
    clearViewer();
    setStatus('Empty.');
  } else if (liveCheck.checked) scheduleBuild();
}

clearBtn.addEventListener('click', () => {
  strokes = []; extraOps = []; lastInterpretations = [];
  redraw();
  updateInterpretationPanel();
  renderTimeline();
  clearViewer();
  setStatus('Cleared.');
});

generateBtn.addEventListener('click', build);

saveBtn.addEventListener('click', saveAsPart);
async function saveAsPart() {
  const name = prompt('Save as cad/<name>.py — enter snake_case name:', 'my_part');
  if (!name) return;
  if (!/^[a-z][a-z0-9_]*$/.test(name)) {
    setStatus(`Invalid name "${name}". snake_case starting with a letter.`, 'error');
    return;
  }
  if (strokes.length === 0 && extraOps.length === 0) { setStatus('Nothing to save.', 'error'); return; }
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
  } catch (e) { setStatus(e.message, 'error'); }
}

// Download menu
downloadBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  downloadMenu.classList.toggle('open');
});
document.addEventListener('click', (e) => {
  if (!downloadMenu.contains(e.target)) downloadMenu.classList.remove('open');
});
downloadMenu.querySelectorAll('a[data-format]').forEach(a => {
  a.addEventListener('click', async (e) => {
    e.preventDefault();
    downloadMenu.classList.remove('open');
    const format = a.dataset.format;
    if (strokes.length === 0 && extraOps.length === 0) {
      setStatus('Nothing to export.', 'error'); return;
    }
    setStatus(`Exporting ${format.toUpperCase()}…`);
    try {
      const body = buildRequest();
      body.format = format;
      const r = await fetch('/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `part.${format}`;
      link.click();
      URL.revokeObjectURL(url);
      setStatus(`Downloaded part.${format}`, 'ok');
    } catch (e) { setStatus(e.message, 'error'); }
  });
});

// Mesh inspect
inspectBtn.addEventListener('click', async () => {
  if (strokes.length === 0 && extraOps.length === 0) {
    setStatus('Nothing to inspect.', 'error'); return;
  }
  inspectText.textContent = 'inspecting…';
  try {
    const r = await fetch('/inspect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildRequest()),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    const ok = j.watertight && j.is_volume && j.issues.length === 0;
    const summary = `vol ${j.volume_mm3.toFixed(0)}mm³ · area ${j.surface_area_mm2.toFixed(0)}mm² · ${j.n_triangles} tris · ${j.watertight ? 'watertight' : 'NOT watertight'}`;
    inspectText.textContent = summary;
    inspectText.style.color = ok ? 'var(--ok)' : 'var(--warn)';
    if (j.issues.length > 0) {
      setStatus(`Inspect: ${j.issues.join(' · ')}`, 'warn');
    } else {
      setStatus('Inspect: clean.', 'ok');
    }
  } catch (e) { setStatus(e.message, 'error'); }
});

// Parts gallery
partsBtn.addEventListener('click', openPartsModal);
partsClose.addEventListener('click', () => partsModal.classList.add('hidden'));
async function openPartsModal() {
  partsModal.classList.remove('hidden');
  partsList.innerHTML = 'Loading…';
  try {
    const r = await fetch('/parts');
    const j = await r.json();
    if (j.parts.length === 0) {
      partsList.innerHTML = '<li>No parts in <code>cad/</code> yet.</li>';
      return;
    }
    partsList.innerHTML = j.parts.map(p => `
      <li data-name="${escapeHtml(p.name)}">
        <span class="name">${escapeHtml(p.name)}</span>
        <span class="doc">${escapeHtml(p.doc || '(no docstring)')}</span>
        <span class="size">${(p.size_bytes / 1024).toFixed(1)} kB${p.has_render ? ' · png' : ''}</span>
      </li>
    `).join('');
    partsList.querySelectorAll('li[data-name]').forEach(li => {
      li.addEventListener('click', () => openPart(li.dataset.name));
    });
  } catch (e) { partsList.innerHTML = `<li>error: ${escapeHtml(e.message)}</li>`; }
}
async function openPart(name) {
  partsModal.classList.add('hidden');
  setStatus(`Opening ${name}…`);
  try {
    const r = await fetch(`/open/${name}`);
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    const j = await r.json();
    // We can't actually re-execute a cad/<name>.py as ops (different model),
    // so just show the params in the status + interpretation area.
    const paramSummary = j.parameters.slice(0, 6).map(p => `${p.name}=${p.value}`).join(' · ');
    setStatus(`Opened ${name}: ${j.parameters.length} params (${paramSummary}${j.parameters.length > 6 ? ' …' : ''})`, 'ok');
    interpEl.innerHTML = `
      <div style="font-size:11px; color: var(--muted)">${escapeHtml(j.docstring)}</div>
      ${j.parameters.map(p => `<span class="chip" style="--role:var(--accent); border-color:var(--accent); color:var(--accent)">
        <span class="idx">P</span> ${escapeHtml(p.name)} = ${escapeHtml(p.value)}
      </span>`).join('')}`;
  } catch (e) { setStatus(e.message, 'error'); }
}

// Help modal
helpBtn.addEventListener('click', () => helpModal.classList.remove('hidden'));
helpClose.addEventListener('click', () => helpModal.classList.add('hidden'));

// Close modals with Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    partsModal.classList.add('hidden');
    helpModal.classList.add('hidden');
  }
});

// ---- keyboard shortcuts -----------------------------------------------------
document.addEventListener('keydown', (e) => {
  // Ignore when typing in inputs.
  const t = e.target.tagName;
  if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') return;
  // Ignore if a modal is open and the key isn't Escape.
  if (!partsModal.classList.contains('hidden') || !helpModal.classList.contains('hidden')) {
    if (e.key !== 'Escape') return;
  }

  const k = e.key.toLowerCase();
  if (e.ctrlKey || e.metaKey) {
    if (k === 'z') { e.preventDefault(); undoLastStroke(); return; }
    if (k === 's') { e.preventDefault(); saveAsPart(); return; }
    if (k === 'o') { e.preventDefault(); openPartsModal(); return; }
  }
  if (e.shiftKey && k === 'c') { e.preventDefault(); clearBtn.click(); return; }
  if (k === '?' || (e.shiftKey && k === '/')) { e.preventDefault(); helpModal.classList.remove('hidden'); return; }
  if (k === 'd') { e.preventDefault(); globalPromptInput.focus(); return; }
  if (k === 'f') { e.preventDefault(); globalPromptInput.value = 'fillet all edges 2mm'; globalPromptInput.focus(); return; }
  if (k === 'c') { e.preventDefault(); globalPromptInput.value = 'chamfer 1mm'; globalPromptInput.focus(); return; }
  if (k === 'h') { e.preventDefault(); globalPromptInput.value = '5mm hole at center'; globalPromptInput.focus(); return; }
  if (k === 'm') { e.preventDefault(); parseAndAppend('mirror across YZ'); return; }
  if (k === 'e') { e.preventDefault(); build(); return; }
  if (k === 'x') {
    e.preventDefault();
    constructionCheck.checked = !constructionCheck.checked;
    canvas.classList.toggle('construction-mode', constructionCheck.checked);
    setStatus(`Construction mode: ${constructionCheck.checked ? 'ON' : 'off'}`, '');
  }
});

[heightInput, sizeInput, planeSelect, modeSelect, snapCheck].forEach(el => {
  el.addEventListener('change', () => {
    if (liveCheck.checked && strokes.length > 0) scheduleBuild(80);
  });
});

// Close inline input on outside click
document.addEventListener('mousedown', (e) => {
  if (inlineInputEl.classList.contains('hidden')) return;
  if (!inlineInputEl.contains(e.target) && e.target !== canvas) closeInlineInput();
});

// ---- rollback bar ----------------------------------------------------------
rollbackInput.addEventListener('input', () => {
  const v = parseInt(rollbackInput.value, 10);
  const total = parseInt(rollbackInput.max, 10);
  rollbackLimit = (v === total) ? -1 : v;  // -1 = no limit
  rollbackLabel.textContent = (rollbackLimit < 0 || rollbackLimit === total) ? 'all ops' : `up to op ${rollbackLimit}`;
  renderTimeline();
  if (liveCheck.checked) scheduleBuild();
});

// ---- clip plane (three.js global clipping) ---------------------------------
clipZInput.addEventListener('input', () => {
  clipFraction = parseInt(clipZInput.value, 10) / 100;
  applyClipPlane();
});
function applyClipPlane() {
  if (!renderer) return;
  if (clipFraction >= 0.999) {
    renderer.clippingPlanes = [];
    clipZLabel.textContent = 'no clip';
    return;
  }
  const cutZ = currentBBox.z * clipFraction;
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, -1), cutZ);
  renderer.clippingPlanes = [plane];
  renderer.localClippingEnabled = true;
  clipZLabel.textContent = `Z ≤ ${cutZ.toFixed(1)}mm`;
}

// ---- hardware library ------------------------------------------------------
hardwareList.innerHTML = HARDWARE.map(h =>
  `<a href="#" data-id="${h.id}">${h.name} ⌀${h.diameter}mm</a>`
).join('');
hardwareBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  hardwareMenu.classList.toggle('open');
});
document.addEventListener('click', (e) => {
  if (!hardwareMenu.contains(e.target)) hardwareMenu.classList.remove('open');
});
hardwareList.querySelectorAll('a[data-id]').forEach(a => {
  a.addEventListener('click', (e) => {
    e.preventDefault();
    hardwareMenu.classList.remove('open');
    const id = a.dataset.id;
    const item = HARDWARE.find(h => h.id === id);
    if (!item) return;
    // Insert a hole op at (0,0) with the standard diameter.
    extraOps.push({
      op: 'hole', x_mm: 0, y_mm: 0,
      diameter_mm: item.diameter, depth_mm: null, plane: 'top',
    });
    setStatus(`Added ${item.name} as a hole at center.`, 'ok');
    renderTimeline();
    if (liveCheck.checked && strokes.length > 0) scheduleBuild();
  });
});

// ---- URL share state -------------------------------------------------------
function encodeState() {
  const state = {
    s: strokes.map(s => ({
      p: s.points.map(p => [Math.round(p[0]), Math.round(p[1])]),
      a: s.annotation, c: s.construction,
    })),
    o: extraOps,
    h: parseFloat(heightInput.value),
    z: parseFloat(sizeInput.value),
    pl: planeSelect.value,
    m: modeSelect.value,
  };
  return btoa(unescape(encodeURIComponent(JSON.stringify(state))));
}
function decodeState(hash) {
  try {
    const s = JSON.parse(decodeURIComponent(escape(atob(hash))));
    strokes = (s.s || []).map(x => ({ points: x.p, annotation: x.a, construction: !!x.c }));
    extraOps = s.o || [];
    if (s.h) heightInput.value = s.h;
    if (s.z) sizeInput.value = s.z;
    if (s.pl) planeSelect.value = s.pl;
    if (s.m) modeSelect.value = s.m;
    redraw();
    renderTimeline();
    updateInterpretationPanel();
    if (strokes.length > 0 || extraOps.length > 0) build();
    setStatus('Loaded from URL.', 'ok');
  } catch (e) {
    setStatus(`Could not decode URL state: ${e.message}`, 'error');
  }
}
shareBtn.addEventListener('click', async () => {
  if (strokes.length === 0 && extraOps.length === 0) {
    setStatus('Nothing to share.', 'error'); return;
  }
  const url = `${location.origin}/#${encodeState()}`;
  try {
    await navigator.clipboard.writeText(url);
    setStatus(`Copied share URL (${url.length} chars).`, 'ok');
  } catch {
    history.replaceState(null, '', `#${encodeState()}`);
    setStatus(`Updated URL hash (couldn't auto-copy).`, 'warn');
  }
});

// ---- boot ------------------------------------------------------------------
fitCanvas();
initViewer();
renderTimeline();
updateInterpretationPanel();
if (location.hash && location.hash.length > 1) {
  decodeState(location.hash.slice(1));
} else {
  setStatus('Ready. Draw a closed shape — click it to annotate, or press ? for shortcuts.');
}
