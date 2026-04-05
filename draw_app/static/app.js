// v0: just draw a single stroke and POST it to get an STL back. Saves the STL
// blob to a download link. No 3D preview yet.
const canvas = document.getElementById('sketch');
const ctx = canvas.getContext('2d');
let stroke = [];
let drawing = false;

function xy(e) { const r = canvas.getBoundingClientRect(); return [e.clientX-r.left, e.clientY-r.top]; }

canvas.addEventListener('mousedown', e => { drawing = true; stroke = [xy(e)]; });
canvas.addEventListener('mousemove', e => { if (!drawing) return; stroke.push(xy(e)); redraw(); });
canvas.addEventListener('mouseup', () => { drawing = false; });

function redraw() {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 2;
  ctx.beginPath();
  if (stroke.length) ctx.moveTo(stroke[0][0], stroke[0][1]);
  stroke.slice(1).forEach(p => ctx.lineTo(p[0], p[1]));
  ctx.stroke();
}

document.getElementById('clear').onclick = () => { stroke = []; redraw(); };
document.getElementById('generate').onclick = async () => {
  const r = await fetch('/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      strokes: [{points: stroke}],
      canvas_width: canvas.width, canvas_height: canvas.height,
      extrude_height_mm: parseFloat(document.getElementById('height').value) || 10,
      target_size_mm: parseFloat(document.getElementById('size').value) || 60,
    })
  });
  if (!r.ok) { alert('error: ' + r.status); return; }
  const blob = await r.blob();
  document.getElementById('download').href = URL.createObjectURL(blob);
};
