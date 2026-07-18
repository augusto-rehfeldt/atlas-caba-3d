const canvas = document.querySelector("#map");
const mainContext = canvas.getContext("2d", { alpha: false });
let ctx = mainContext;
const query = new URLSearchParams(location.search);
const exporting = query.get("export") === "1";
const exportView = {
  width: Math.max(1, Number(query.get("width")) || canvas.clientWidth),
  height: Math.max(1, Number(query.get("height")) || canvas.clientHeight),
  x: Math.max(0, Number(query.get("x")) || 0),
  y: Math.max(0, Number(query.get("y")) || 0),
};
const fit = exporting ? Math.min(exportView.width / 1440, exportView.height / 900) : 1;
const displayScale = exporting ? fit : 1;
const state = {
  zoom: .4 * fit,
  panX: 0,
  panY: 70 * fit,
  rotation: (Number(query.get("rotation")) || 0) * Math.PI / 180,
  dragging: false,
  night: query.get("night") === "1",
  x: 0,
  y: 0,
};
const palette = [
  ["#e3bd83", "#b26f57", "#8b5149"],
  ["#d8c3a1", "#9e7263", "#76514c"],
  ["#ead9bd", "#a97865", "#80564f"],
  ["#c7b6a0", "#846b63", "#66504d"],
  ["#e8c9a0", "#aa7059", "#805049"],
];
const GRID_SIZE = 120;
const TILE_SIZE = 128;
const MAX_ZOOM = 2.4;
const TILE_LEVELS = [.05, .08, .12, .18, .27, .4, .6, .9, 1.35, 1.8, 2.4];
const tileCache = new Map();
const tileQueue = [];
let wantedTiles = new Set();
let tileRendering = false;
let renderTile;
let fallbackView;
let renderedMap;
let groundMap;
let city;
let world;
let pixelRatio = 1;
let pendingFrame = 0;
let wheelTimer = 0;
let wheelStart;
let rotationStart;
let vectorMode = false;
let vectorDataPromise;
const dragSnapshot = document.createElement("canvas");

if (exporting) document.documentElement.classList.add("export");

const viewWidth = () => exporting ? exportView.width : canvas.clientWidth;
const viewHeight = () => exporting ? exportView.height : canvas.clientHeight;
const targetWidth = () => renderTile ? TILE_SIZE : canvas.clientWidth;
const targetHeight = () => renderTile ? TILE_SIZE : canvas.clientHeight;
const isInteracting = () => state.dragging || wheelStart || rotationStart !== undefined;

const iso = ([east, north]) => [(east + north) * .62, (east - north) * .31];
const ROTATION_TRIG = Array.from({ length: 361 }, (_, index) => {
  const angle = (index - 180) * Math.PI / 180;
  return [Math.cos(angle), Math.sin(angle)];
});
let cachedYawAngle;
let cachedYawCosine = 1;
let cachedYawSine = 0;
const rotationTrig = angle => {
  if (angle !== cachedYawAngle) {
    cachedYawAngle = angle;
    const degree = Math.round(angle * 180 / Math.PI);
    const preloaded = Math.abs(angle - degree * Math.PI / 180) < 1e-9 ? ROTATION_TRIG[degree + 180] : undefined;
    [cachedYawCosine, cachedYawSine] = preloaded || [Math.cos(angle), Math.sin(angle)];
  }
  return [cachedYawCosine, cachedYawSine];
};
const screenYaw = ([x, y], angle = state.rotation) => {
  const [cosine, sine] = rotationTrig(angle);
  return [x * cosine - y * sine, x * sine + y * cosine];
};
const yawIso = ([x, y], angle = state.rotation) => {
  if (window.SELECTED_RENDER && !vectorMode) return screenYaw([x, y], angle);
  const east = (x / .62 + y / .31) / 2;
  const north = (x / .62 - y / .31) / 2;
  const [cosine, sine] = rotationTrig(angle);
  return iso([
    east * cosine - north * sine,
    east * sine + north * cosine,
  ]);
};
const screen = (point, z = 0) => {
  const [x, y] = yawIso(point);
  return renderTile
  ? [x * state.zoom - renderTile.x, (y - z * 1.12) * state.zoom - renderTile.y]
  : [
      viewWidth() / 2 + state.panX + x * state.zoom - exportView.x,
      viewHeight() / 2 + state.panY + (y - z * 1.12) * state.zoom - exportView.y,
    ];
};
const rotationCheck = yawIso(yawIso([123, -45], Math.PI / 4), Math.PI / 4);
console.assert(
  iso([1, 0])[0] > 0 && iso([0, 1])[1] < 0 &&
  rotationCheck.every((value, index) => Math.abs(value - yawIso([123, -45], Math.PI / 2)[index]) < 1e-9),
  "Map orientation failed",
);

function path(points, z = 0) {
  if (!points.length) return;
  const [first, ...rest] = points;
  ctx.beginPath();
  ctx.moveTo(...screen(first, z));
  rest.forEach(point => ctx.lineTo(...screen(point, z)));
  ctx.closePath();
}

function strokeLine(points) {
  const [first, ...rest] = points;
  ctx.beginPath();
  ctx.moveTo(...screen(first));
  rest.forEach(point => ctx.lineTo(...screen(point)));
  ctx.stroke();
}

function hash(value) {
  let result = 2166136261;
  for (const char of value) result = Math.imul(result ^ char.charCodeAt(0), 16777619);
  return result >>> 0;
}

function shade(color, amount) {
  const value = Number.parseInt(color.slice(1), 16);
  const channel = shift => Math.round(((value >> shift) & 255) * amount).toString(16).padStart(2, "0");
  return `#${channel(16)}${channel(8)}${channel(0)}`;
}

function mixColour(color, target, amount) {
  const first = Number.parseInt(color.slice(1), 16);
  const second = Number.parseInt(target.slice(1), 16);
  const channel = shift => Math.round(
    ((first >> shift) & 255) * (1 - amount) + ((second >> shift) & 255) * amount,
  ).toString(16).padStart(2, "0");
  return `#${channel(16)}${channel(8)}${channel(0)}`;
}

function streetColour(color) {
  const value = Number.parseInt(color.slice(1), 16);
  const observed = [16, 8, 0].map(shift => (value >> shift) & 255);
  const light = Math.min(154, Math.max(54, observed[0] * .21 + observed[1] * .72 + observed[2] * .07));
  return "#" + observed.map((channel, index) => {
    const neutral = light + [6, 1, -7][index];
    return Math.round(neutral * .72 + channel * .28).toString(16).padStart(2, "0");
  }).join("");
}

function boxFor(points) {
  const xs = points.map(point => point[0]);
  const ys = points.map(point => point[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function spatialGrid(items) {
  const grid = new Map();
  items.forEach(item => {
    const [west, north, east, south] = item.box.map(value => Math.floor(value / GRID_SIZE));
    for (let x = west; x <= east; x++) {
      for (let y = north; y <= south; y++) {
        const key = `${x},${y}`;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(item);
      }
    }
  });
  return grid;
}

function prepare(data) {
  const convert = points => points.map(iso);
  const buildings = data.buildings.map(item => {
    const p = convert(item.p);
    return {
      ...item,
      p,
      depth: Math.max(...p.map(point => point[1])),
      color: hash(item.id) % palette.length,
      box: boxFor(p),
    };
  }).sort((a, b) => a.depth - b.depth);
  const parks = data.parks.map(item => {
    const p = convert(item.p);
    const box = boxFor(p);
    return { ...item, p, box, span: Math.max(box[2] - box[0], box[3] - box[1]) };
  });
  const roads = data.roads.map(item => {
    const p = convert(item.p);
    return { ...item, p, box: boxFor(p) };
  });
  return {
    boundary: data.boundary.map(convert),
    parks,
    roads,
    buildings,
    buildingGrid: spatialGrid(buildings),
    parkGrid: spatialGrid(parks),
    roadGrid: spatialGrid(roads),
    landmarks: data.landmarks.map(item => ({ ...item, p: iso(item.p) })),
  };
}

function gridCandidates(grid, verticalPadding = 100) {
  const baseX = renderTile ? -renderTile.x : viewWidth() / 2 + state.panX - exportView.x;
  const baseY = renderTile ? -renderTile.y : viewHeight() / 2 + state.panY - exportView.y;
  const horizontalPadding = renderTile ? 20 : 100;
  const topPadding = renderTile ? 20 : 100;
  const left = -baseX / state.zoom - horizontalPadding;
  const right = (targetWidth() - baseX) / state.zoom + horizontalPadding;
  const top = -baseY / state.zoom - topPadding;
  const bottom = (targetHeight() - baseY) / state.zoom + verticalPadding;
  const corners = [
    [left, top],
    [right, top],
    [left, bottom],
    [right, bottom],
  ].map(point => yawIso(point, -state.rotation));
  const xs = corners.map(point => point[0]);
  const ys = corners.map(point => point[1]);
  const west = Math.floor(Math.min(...xs) / GRID_SIZE);
  const east = Math.floor(Math.max(...xs) / GRID_SIZE);
  const north = Math.floor(Math.min(...ys) / GRID_SIZE);
  const south = Math.floor(Math.max(...ys) / GRID_SIZE);
  const candidates = new Set();
  for (let x = west; x <= east; x++) {
    for (let y = north; y <= south; y++) {
      grid.get(`${x},${y}`)?.forEach(item => candidates.add(item));
    }
  }
  return candidates;
}

function visibleBuildings() {
  const candidates = gridCandidates(world.buildingGrid, renderTile ? 200 : 260);
  if (Math.abs(state.rotation) > .001) {
    const rotationKey = Math.round(state.rotation * 180 / Math.PI);
    const ordered = [...candidates];
    ordered.forEach(building => {
      if (building.depthRotation === rotationKey) return;
      building.depthRotation = rotationKey;
      building.rotatedDepth = Math.max(...building.p.map(point => yawIso(point)[1]));
    });
    return ordered.sort((a, b) => a.rotatedDepth - b.rotatedDepth);
  }
  const ordered = candidates.size > world.buildings.length / 2
    ? world.buildings.filter(building => candidates.has(building))
    : [...candidates].sort((a, b) => a.depth - b.depth);
  return ordered;
}

function visible(points, height = 0) {
  const xs = points.map(point => screen(point)[0]);
  const ys = points.flatMap(point => [screen(point)[1], screen(point, height)[1]]);
  const margin = renderTile ? 0 : 80;
  return Math.max(...xs) > -margin && Math.min(...xs) < targetWidth() + margin &&
    Math.max(...ys) > -margin && Math.min(...ys) < targetHeight() + margin;
}

function drawGround() {
  ctx.fillStyle = state.night ? "#081217" : "#172428";
  ctx.fillRect(0, 0, targetWidth(), targetHeight());

  if (groundMap?.ready) {
    const origin = screen([0, 0]);
    const horizontal = screen([1, 0]);
    const vertical = screen([0, 1]);
    ctx.save();
    ctx.transform(
      horizontal[0] - origin[0], horizontal[1] - origin[1],
      vertical[0] - origin[0], vertical[1] - origin[1],
      origin[0], origin[1],
    );
    ctx.filter = state.night ? "brightness(.45) saturate(.7)" : "none";
    ctx.drawImage(groundMap.image, groundMap.x, groundMap.y, groundMap.width / groundMap.scale, groundMap.height / groundMap.scale);
    ctx.restore();
  } else {
    ctx.save();
    ctx.shadowColor = state.night ? "#00090dcc" : "#071114a8";
    ctx.shadowBlur = 30;
    ctx.shadowOffsetY = 20;
    world.boundary.forEach(polygon => { path(polygon); ctx.fillStyle = state.night ? "#283337" : "#cabd9d"; ctx.fill(); });
    ctx.restore();
    world.boundary.forEach(polygon => { path(polygon); ctx.fillStyle = state.night ? "#313c3e" : "#cfc3a6"; ctx.fill(); });
  }

  ctx.save();
  ctx.beginPath();
  world.boundary.forEach(polygon => {
    const [first, ...rest] = polygon;
    ctx.moveTo(...screen(first));
    rest.forEach(point => ctx.lineTo(...screen(point)));
    ctx.closePath();
  });
  ctx.clip();

  gridCandidates(world.parkGrid).forEach(park => {
    if (!visible(park.p)) return;
    path(park.p);
    ctx.fillStyle = state.night ? "#294b3c99" : groundMap?.ready ? "#738f6855" : "#738f68";
    ctx.fill();
    ctx.strokeStyle = state.night ? "#18362c" : "#536e52";
    ctx.lineWidth = Math.max(1, state.zoom * 2);
    ctx.stroke();
  });

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  gridCandidates(world.roadGrid).forEach(road => {
    if (!visible(road.p)) return;
    if (!road.rail && state.zoom > .48) {
      ctx.strokeStyle = state.night ? "#313d3f" : "#a69b83";
      ctx.lineWidth = Math.max(2, (road.w + 3) * state.zoom);
      strokeLine(road.p);
    }
    const roadColour = road.rail ? "#5a5752" : road.c ? streetColour(road.c) : "#807b70";
    ctx.strokeStyle = state.night ? shade(roadColour, .48) : groundMap?.ready ? `${roadColour}88` : roadColour;
    ctx.lineWidth = Math.max(1.5, road.w * state.zoom);
    strokeLine(road.p);
    if (!road.rail && road.w >= 13 && state.zoom > .55) {
      ctx.strokeStyle = "#e6d7a78c";
      ctx.lineWidth = Math.max(.7, state.zoom * .8);
      ctx.setLineDash([8 * state.zoom, 6 * state.zoom]);
      strokeLine(road.p);
      ctx.setLineDash([]);
    }
    if (road.rail && state.zoom > .35) {
      ctx.strokeStyle = "#d9cda9";
      ctx.lineWidth = Math.max(1, state.zoom * 2);
      ctx.setLineDash([5 * state.zoom, 5 * state.zoom]);
      strokeLine(road.p);
      ctx.setLineDash([]);
    }
  });
  ctx.restore();
}

function drawBuilding(building) {
  if (!visible(building.p, building.h)) return;
  const roof = building.c || palette[building.color][0];
  const materialTarget = {
    tile: "#a56f5c",
    concrete: "#aaa79f",
    metal: "#87969a",
    green: "#7b826d",
    dark: "#716b65",
    mixed: "#927d70",
  }[building.m] || "#978274";
  const facade = mixColour(roof, materialTarget, .58);
  let colors = [roof, shade(facade, .8), shade(facade, .63)];
  if (state.night) colors = colors.map(color => shade(color, .52));
  const shadow = building.p.map(([x, y]) => [x + 12, y + 9]);
  path(shadow);
  ctx.fillStyle = "#4a41394a";
  ctx.fill();

  for (let i = 0; i < building.p.length; i++) {
    const a = building.p[i];
    const b = building.p[(i + 1) % building.p.length];
    const [ax, ay] = screen(a);
    const [bx, by] = screen(b);
    const [tx, ty] = screen(b, building.h);
    const [ux, uy] = screen(a, building.h);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.lineTo(tx, ty);
    ctx.lineTo(ux, uy);
    ctx.closePath();
    ctx.fillStyle = bx - ax > 0 ? colors[1] : colors[2];
    ctx.fill();
    drawWindows(building, a, b, i);
  }

  path(building.p, building.h);
  ctx.fillStyle = colors[0];
  ctx.fill();
  ctx.strokeStyle = "#3c35302c";
  ctx.lineWidth = Math.max(.5, state.zoom);
  ctx.stroke();

  if (state.zoom > .72 && building.t) {
    const roof = building.p.map(point => screen(point, building.h));
    const xs = roof.map(point => point[0]);
    const ys = roof.map(point => point[1]);
    const contrast = building.t & 3;
    const roughness = building.t >> 2;
    const seed = hash(building.id);
    ctx.save();
    path(building.p, building.h);
    ctx.clip();
    ctx.fillStyle = contrast > 1 ? "#2634382e" : "#fff8e924";
    for (let mark = 0; mark <= roughness; mark++) {
      const x = Math.min(...xs) + (seed * (mark + 3) % 997) / 997 * (Math.max(...xs) - Math.min(...xs));
      const y = Math.min(...ys) + (seed * (mark + 5) % 991) / 991 * (Math.max(...ys) - Math.min(...ys));
      const size = Math.max(.45, state.zoom * .28);
      ctx.fillRect(x - size, y - size, size * 2, size * 2);
    }
    ctx.restore();
  }

  if (state.zoom > .58 && building.m && building.m !== "mixed") {
    const roof = building.p.map(point => screen(point, building.h));
    const xs = roof.map(point => point[0]);
    const ys = roof.map(point => point[1]);
    ctx.save();
    path(building.p, building.h);
    ctx.clip();
    ctx.strokeStyle = building.m === "tile" ? "#713f3638" : "#eef1e52e";
    ctx.lineWidth = Math.max(.55, state.zoom * .65);
    const spacing = ((building.m === "tile" ? 5 : 8) + (3 - (building.t >> 2)) * 1.5) * state.zoom;
    for (let offset = Math.min(...xs) - Math.max(...ys); offset < Math.max(...xs) - Math.min(...ys); offset += spacing) {
      ctx.beginPath();
      ctx.moveTo(offset + Math.min(...ys), Math.min(...ys));
      ctx.lineTo(offset + Math.max(...ys), Math.max(...ys));
      ctx.stroke();
    }
    ctx.restore();
  }

  if (state.zoom > .62 && building.h > 9) {
    const floors = Math.min(4, Math.floor(building.h / 5));
    ctx.strokeStyle = "#4d47413a";
    ctx.lineWidth = .7;
    for (let floor = 1; floor <= floors; floor++) {
      const z = building.h * floor / (floors + 1);
      for (let i = 0; i < building.p.length; i += 2) {
        ctx.beginPath();
        ctx.moveTo(...screen(building.p[i], z));
        ctx.lineTo(...screen(building.p[(i + 1) % building.p.length], z));
        ctx.stroke();
      }
    }
  }
}

function drawStreetLabels() {
  if (state.zoom < .32) return;
  const roads = [...gridCandidates(world.roadGrid)].sort((a, b) => b.w - a.w);
  const names = new Set();
  const boxes = [];
  const fontSize = Math.max(9, Math.min(12, 8 + state.zoom * 4)) * displayScale;
  ctx.save();
  ctx.font = `700 ${fontSize}px Inter, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineWidth = 3 * displayScale;
  ctx.strokeStyle = state.night ? "#071216d9" : "#f1e7d5d9";
  ctx.fillStyle = state.night ? "#dce6dd" : "#263438";
  for (const road of roads) {
    const name = road.n?.replace(/\s+/g, " ").trim();
    const key = name?.toLocaleUpperCase("es-AR");
    if (!name || names.has(key) || state.zoom < .55 && road.w < 13) continue;
    let segment;
    for (let index = 1; index < road.p.length; index++) {
      const start = screen(road.p[index - 1]);
      const end = screen(road.p[index]);
      const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
      if (!segment || length > segment.length) segment = { start, end, length };
    }
    if (!segment || segment.length < 70 * displayScale) continue;
    const x = (segment.start[0] + segment.end[0]) / 2;
    const y = (segment.start[1] + segment.end[1]) / 2;
    if (x < 30 || x > targetWidth() - 30 || y < 30 || y > targetHeight() - 30) continue;
    const radius = (ctx.measureText(name).width + 18 * displayScale) / 2;
    if (boxes.some(box => Math.abs(x - box.x) < radius + box.radius && Math.abs(y - box.y) < 15 * displayScale)) continue;
    let angle = Math.atan2(segment.end[1] - segment.start[1], segment.end[0] - segment.start[0]);
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.strokeText(name, 0, 0);
    ctx.fillText(name, 0, 0);
    ctx.restore();
    boxes.push({ x, y, radius });
    names.add(key);
  }
  ctx.restore();
}

function drawWindows(building, a, b, side) {
  if (state.zoom < .72 || building.h < 7) return;
  const edge = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const columns = Math.min(7, Math.floor(edge / 4.5));
  const floors = Math.min(14, Math.floor((building.h - 2) / 3.2));
  if (!columns || !floors) return;
  const between = amount => [a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount];
  for (let floor = 0; floor < floors; floor++) {
    const z0 = 1.2 + floor * (building.h - 2.4) / floors;
    const z1 = Math.min(building.h - .7, z0 + 1.65);
    for (let column = 0; column < columns; column++) {
      const u0 = (column + .2) / columns;
      const u1 = (column + .78) / columns;
      const lit = state.night && hash(`${building.id}-${side}-${floor}-${column}`) % 4 === 0;
      ctx.beginPath();
      ctx.moveTo(...screen(between(u0), z0));
      ctx.lineTo(...screen(between(u1), z0));
      ctx.lineTo(...screen(between(u1), z1));
      ctx.lineTo(...screen(between(u0), z1));
      ctx.closePath();
      ctx.fillStyle = lit ? "#ffd982" : state.night ? "#10262e" : "#38545a9e";
      ctx.fill();
    }
  }
}

function drawLandmarks() {
  world.landmarks.forEach(item => {
    const [x, y] = screen(item.p, item.k === "landmark" ? 18 : 4);
    if (x < 0 || y < 0 || x > targetWidth() || y > targetHeight()) return;
    ctx.beginPath();
    ctx.arc(x, y, 4 * displayScale, 0, Math.PI * 2);
    ctx.fillStyle = item.k === "landmark" ? "#f0cb7c" : "#eef2de";
    ctx.fill();
    ctx.strokeStyle = "#172428";
    ctx.lineWidth = 2 * displayScale;
    ctx.stroke();
    ctx.font = `700 ${10 * displayScale}px Inter, system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillStyle = "#fff8e9";
    ctx.shadowColor = "#102025";
    ctx.shadowBlur = 5 * displayScale;
    const [labelX, labelY] = item.k === "station" ? [x - 20 * displayScale, y - 13 * displayScale]
      : item.k === "landmark" ? [x + 44 * displayScale, y - 13 * displayScale]
        : [x + 8 * displayScale, y + 25 * displayScale];
    ctx.fillText(item.n.toUpperCase(), labelX, labelY);
    ctx.shadowBlur = 0;
  });
}

function drawVector() {
  const started = performance.now();
  drawGround();
  visibleBuildings().forEach(drawBuilding);
  drawStreetLabels();
  drawLandmarks();
  document.documentElement.dataset.renderMs = Math.round(performance.now() - started);
}

function nearestTileLevel(zoom) {
  return TILE_LEVELS.reduce((best, level) =>
    Math.abs(Math.log(zoom / level)) < Math.abs(Math.log(zoom / best)) ? level : best
  );
}

function withTileContext(job, tileContext, action) {
  const previousZoom = state.zoom;
  const previousRotation = state.rotation;
  const previousNight = state.night;
  ctx = tileContext;
  renderTile = { x: job.x * TILE_SIZE, y: job.y * TILE_SIZE };
  state.zoom = job.level;
  state.rotation = job.rotation;
  state.night = job.night;
  try {
    return action();
  } finally {
    state.zoom = previousZoom;
    state.rotation = previousRotation;
    state.night = previousNight;
    renderTile = undefined;
    ctx = mainContext;
  }
}

function makeTile(job, complete) {
  const tile = document.createElement("canvas");
  tile.width = TILE_SIZE;
  tile.height = TILE_SIZE;
  const tileContext = tile.getContext("2d", { alpha: false });
  const started = performance.now();
  let maximumSlice = 0;
  let buildings;
  const groundStarted = performance.now();
  withTileContext(job, tileContext, () => {
    drawGround();
    buildings = visibleBuildings();
  });
  maximumSlice = performance.now() - groundStarted;
  let index = 0;

  function batch() {
    if (isInteracting()) return requestAnimationFrame(batch);
    const sliceStarted = performance.now();
    withTileContext(job, tileContext, () => {
      do drawBuilding(buildings[index++]);
      while (index < buildings.length && performance.now() - sliceStarted < 6);
    });
    maximumSlice = Math.max(maximumSlice, performance.now() - sliceStarted);
    if (index < buildings.length) requestAnimationFrame(batch);
    else complete(tile, maximumSlice, performance.now() - started);
  }
  if (buildings.length) requestAnimationFrame(batch);
  else complete(tile, maximumSlice, performance.now() - started);
}

function cacheTile(key, tile) {
  tileCache.set(key, tile);
  while (tileCache.size > 384) tileCache.delete(tileCache.keys().next().value);
}

function processTileQueue() {
  if (tileRendering || isInteracting()) return;
  while (tileQueue.length && !wantedTiles.has(tileQueue[0].key)) tileQueue.shift();
  const job = tileQueue.shift();
  if (!job) return;
  tileRendering = true;
  requestAnimationFrame(() => {
    if (isInteracting()) {
      tileRendering = false;
      return;
    }
    makeTile(job, (tile, maximumSlice, total) => {
      cacheTile(job.key, tile);
      document.documentElement.dataset.tileMs = Math.round(total);
      document.documentElement.dataset.tileMaxMs = Math.max(
        Number(document.documentElement.dataset.tileMaxMs) || 0,
        Math.round(maximumSlice),
      );
      tileRendering = false;
      requestDraw();
    });
  });
}

function drawTileView() {
  const started = performance.now();
  ctx = mainContext;
  ctx.fillStyle = state.night ? "#081217" : "#172428";
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  if (fallbackView) {
    const fallbackScale = state.zoom / fallbackView.zoom;
    const oldX = canvas.clientWidth / 2 + fallbackView.panX;
    const oldY = canvas.clientHeight / 2 + fallbackView.panY;
    const newX = canvas.clientWidth / 2 + state.panX;
    const newY = canvas.clientHeight / 2 + state.panY;
    ctx.drawImage(
      fallbackView.canvas,
      newX - fallbackScale * oldX,
      newY - fallbackScale * oldY,
      fallbackView.width * fallbackScale,
      fallbackView.height * fallbackScale,
    );
  }
  const level = nearestTileLevel(state.zoom);
  const scale = state.zoom / level;
  const cameraX = canvas.clientWidth / 2 + state.panX;
  const cameraY = canvas.clientHeight / 2 + state.panY;
  const visibleWest = Math.floor(-cameraX / scale / TILE_SIZE);
  const visibleEast = Math.floor((canvas.clientWidth - cameraX) / scale / TILE_SIZE);
  const visibleNorth = Math.floor(-cameraY / scale / TILE_SIZE);
  const visibleSouth = Math.floor((canvas.clientHeight - cameraY) / scale / TILE_SIZE);
  // ponytail: one ring bounds memory; expand only if profiling shows spare idle budget.
  const west = visibleWest - 1, east = visibleEast + 1;
  const north = visibleNorth - 1, south = visibleSouth + 1;
  const visibleTotal = (visibleEast - visibleWest + 1) * (visibleSouth - visibleNorth + 1);
  const jobs = [];
  let ready = 0;
  wantedTiles = new Set();
  tileQueue.length = 0;
  const area = city.meta.name;
  const rotation = state.rotation;
  const rotationDegrees = Math.round(rotation * 180 / Math.PI);
  for (let y = north; y <= south; y++) {
    for (let x = west; x <= east; x++) {
      const inView = x >= visibleWest && x <= visibleEast && y >= visibleNorth && y <= visibleSouth;
      const key = `${area}:${state.night ? "n" : "d"}:${rotationDegrees}:${level}:${x}:${y}`;
      wantedTiles.add(key);
      const tile = tileCache.get(key);
      if (tile) {
        if (inView) ready++;
        tileCache.delete(key);
        tileCache.set(key, tile);
        if (inView) ctx.drawImage(
            tile,
            cameraX + x * TILE_SIZE * scale,
            cameraY + y * TILE_SIZE * scale,
            TILE_SIZE * scale + .6,
            TILE_SIZE * scale + .6,
          );
      } else {
        const centerX = (west + east) / 2;
        const centerY = (north + south) / 2;
        jobs.push({ key, level, x, y, rotation, night: state.night, distance: (inView ? 0 : 1000) + Math.hypot(x - centerX, y - centerY) });
      }
    }
  }
  jobs.sort((a, b) => a.distance - b.distance);
  tileQueue.push(...jobs);
  drawStreetLabels();
  drawLandmarks();
  document.documentElement.dataset.renderMs = Math.round(performance.now() - started);
  document.documentElement.dataset.tiles = `${ready}/${visibleTotal}`;
  document.documentElement.dataset.cache = tileCache.size;
  document.documentElement.dataset.queued = tileQueue.length;
  if (ready === visibleTotal) {
    document.documentElement.dataset.ready = "true";
    fallbackView = undefined;
  }
  else delete document.documentElement.dataset.ready;
  processTileQueue();
}

function drawRenderedView() {
  const started = performance.now();
  ctx = mainContext;
  ctx.fillStyle = state.night ? "#081217" : "#172428";
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  const horizontal = yawIso([1, 0]);
  const vertical = yawIso([0, 1]);
  ctx.save();
  ctx.translate(canvas.clientWidth / 2 + state.panX, canvas.clientHeight / 2 + state.panY);
  ctx.transform(
    horizontal[0] * state.zoom, horizontal[1] * state.zoom,
    vertical[0] * state.zoom, vertical[1] * state.zoom,
    0, 0,
  );
  ctx.filter = state.night ? "brightness(.46) saturate(.75)" : "none";
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(
    renderedMap.image,
    renderedMap.x, renderedMap.y,
    renderedMap.width / renderedMap.scale, renderedMap.height / renderedMap.scale,
  );
  ctx.restore();
  drawStreetLabels();
  drawLandmarks();
  document.documentElement.dataset.renderMs = Math.round(performance.now() - started);
  document.documentElement.dataset.ready = "true";
}

function draw() {
  if (renderedMap?.ready) drawRenderedView();
  else if (exporting) drawVector();
  else drawTileView();
}

function requestDraw() {
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => {
    pendingFrame = 0;
    draw();
  });
}

function fitWorld() {
  const points = world.boundary.flat().map(point => yawIso(point));
  const xs = points.map(point => point[0]);
  const ys = points.map(point => point[1]);
  const west = Math.min(...xs), east = Math.max(...xs);
  const north = Math.min(...ys), south = Math.max(...ys);
  state.zoom = Math.min(1.35, .96 * Math.min(viewWidth() / (east - west), viewHeight() / (south - north)));
  state.minZoom = state.zoom * .55;
  state.panX = -(west + east) / 2 * state.zoom;
  state.panY = -(north + south) / 2 * state.zoom + (exporting ? 0 : 45);
}

function overviewFallback() {
  const preview = document.createElement("canvas");
  preview.width = canvas.clientWidth;
  preview.height = canvas.clientHeight;
  const painter = preview.getContext("2d", { alpha: false });
  const project = (point, z = 0) => {
    const [x, y] = yawIso(point);
    return [
    canvas.clientWidth / 2 + state.panX + x * state.zoom,
    canvas.clientHeight / 2 + state.panY + (y - z * 1.12) * state.zoom,
    ];
  };
  const polygon = points => {
    painter.beginPath();
    points.forEach((point, index) => index ? painter.lineTo(...project(point)) : painter.moveTo(...project(point)));
    painter.closePath();
  };
  painter.fillStyle = state.night ? "#081217" : "#172428";
  painter.fillRect(0, 0, preview.width, preview.height);
  world.boundary.forEach(points => { polygon(points); painter.fillStyle = state.night ? "#313c3e" : "#cfc3a6"; painter.fill(); });
  world.parks.forEach(park => { polygon(park.p); painter.fillStyle = state.night ? "#294b3c" : "#738f68"; painter.fill(); });
  painter.lineCap = "round";
  world.roads.forEach(road => {
    painter.beginPath();
    road.p.forEach((point, index) => index ? painter.lineTo(...project(point)) : painter.moveTo(...project(point)));
    const colour = road.rail ? "#5a5752" : road.c ? streetColour(road.c) : "#807b70";
    painter.strokeStyle = state.night ? shade(colour, .48) : colour;
    painter.lineWidth = Math.max(1, road.w * state.zoom);
    painter.stroke();
  });
  world.buildings.forEach(building => {
    const center = building.p.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0])
      .map(value => value / building.p.length);
    const [x, y] = project(center, building.h);
    painter.fillStyle = state.night ? shade(building.c || palette[building.color][0], .55) : building.c || palette[building.color][0];
    const size = Math.max(1, Math.min(3, building.h * state.zoom * .12));
    painter.fillRect(x - size / 2, y - size / 2, size, size);
  });
  return { canvas: preview, zoom: state.zoom, panX: state.panX, panY: state.panY, width: preview.width, height: preview.height };
}

function snapshotFallback() {
  const snapshot = document.createElement("canvas");
  snapshot.width = canvas.width;
  snapshot.height = canvas.height;
  snapshot.getContext("2d").drawImage(canvas, 0, 0);
  return { canvas: snapshot, zoom: state.zoom, panX: state.panX, panY: state.panY, width: canvas.clientWidth, height: canvas.clientHeight };
}

function resize() {
  pixelRatio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(canvas.clientWidth * pixelRatio);
  canvas.height = Math.round(canvas.clientHeight * pixelRatio);
  mainContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  if (world) {
    if (!exporting && !renderedMap?.ready) fallbackView = overviewFallback();
    draw();
  }
}

function zoom(amount, x = canvas.clientWidth / 2, y = canvas.clientHeight / 2, preview = false) {
  const before = state.zoom;
  if (preview && !wheelStart) {
    wheelStart = { zoom: before, panX: state.panX, panY: state.panY };
    fallbackView = snapshotFallback();
  }
  state.zoom = Math.min(MAX_ZOOM, Math.max(state.minZoom || .08, state.zoom * amount));
  const factor = state.zoom / before;
  state.panX = x - canvas.clientWidth / 2 - (x - canvas.clientWidth / 2 - state.panX) * factor;
  state.panY = y - canvas.clientHeight / 2 - (y - canvas.clientHeight / 2 - state.panY) * factor;
  if (!preview) return requestDraw();
  const scale = state.zoom / wheelStart.zoom;
  const oldX = canvas.clientWidth / 2 + wheelStart.panX;
  const oldY = canvas.clientHeight / 2 + wheelStart.panY;
  const newX = canvas.clientWidth / 2 + state.panX;
  const newY = canvas.clientHeight / 2 + state.panY;
  canvas.style.transformOrigin = "0 0";
  canvas.style.transform = `matrix(${scale},0,0,${scale},${newX - scale * oldX},${newY - scale * oldY})`;
  clearTimeout(wheelTimer);
  wheelTimer = setTimeout(() => {
    canvas.style.transform = "none";
    wheelStart = undefined;
    requestDraw();
  }, 140);
}

canvas.addEventListener("pointerdown", event => {
  if (wheelStart) {
    clearTimeout(wheelTimer);
    canvas.style.transform = "none";
    wheelStart = undefined;
    draw();
  }
  state.dragging = true;
  state.x = event.clientX;
  state.y = event.clientY;
  state.startPanX = state.panX;
  state.startPanY = state.panY;
  dragSnapshot.width = canvas.width;
  dragSnapshot.height = canvas.height;
  dragSnapshot.getContext("2d").drawImage(canvas, 0, 0);
  fallbackView = { canvas: dragSnapshot, zoom: state.zoom, panX: state.panX, panY: state.panY, width: canvas.clientWidth, height: canvas.clientHeight };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", event => {
  if (!state.dragging) return;
  const dx = event.clientX - state.x;
  const dy = event.clientY - state.y;
  state.panX = state.startPanX + dx;
  state.panY = state.startPanY + dy;
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => {
    pendingFrame = 0;
    ctx.fillStyle = "#172428";
    ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    ctx.drawImage(
      dragSnapshot,
      0, 0, dragSnapshot.width, dragSnapshot.height,
      dx, dy, canvas.clientWidth, canvas.clientHeight,
    );
  });
});
function endDrag() {
  state.dragging = false;
  if (pendingFrame) cancelAnimationFrame(pendingFrame);
  pendingFrame = 0;
  requestDraw();
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
canvas.addEventListener("wheel", event => {
  event.preventDefault();
  zoom(event.deltaY < 0 ? 1.12 : .89, event.clientX, event.clientY, true);
}, { passive: false });

document.querySelector("#minus").addEventListener("click", () => zoom(.82));
document.querySelector("#plus").addEventListener("click", () => zoom(1.22));
document.querySelector("#theme").addEventListener("click", event => {
  state.night = !state.night;
  event.currentTarget.textContent = state.night ? "DÍA" : "NOCHE";
  event.currentTarget.setAttribute("aria-pressed", String(state.night));
  fallbackView = renderedMap?.ready ? undefined : overviewFallback();
  draw();
});
document.querySelector("#theme").textContent = state.night ? "DÍA" : "NOCHE";
document.querySelector("#theme").setAttribute("aria-pressed", String(state.night));
document.querySelector("#style").addEventListener("change", event => {
  const style = event.currentTarget.value;
  canvas.style.filter = {
    real: "none",
    vivid: "saturate(1.25) contrast(1.08)",
    warm: "sepia(.28) saturate(1.18) contrast(1.04)",
    mono: "grayscale(.82) contrast(1.18)",
    future: "saturate(1.65) contrast(1.28) hue-rotate(145deg) brightness(.88)",
    animated: "saturate(1.35) contrast(1.12)",
  }[style];
  canvas.classList.toggle("animated-style", style === "animated");
});
document.querySelector("#reset").addEventListener("click", () => {
  fitWorld();
  fallbackView = renderedMap?.ready ? undefined : overviewFallback();
  draw();
});

const rotationInput = document.querySelector("#rotation");
rotationInput.value = Math.round(state.rotation * 180 / Math.PI);
document.documentElement.dataset.rotation = rotationInput.value;
rotationInput.addEventListener("pointerdown", () => {
  rotationStart = state.rotation;
  ensureVectorMap();
});
rotationInput.addEventListener("pointerup", () => setTimeout(() => {
  rotationStart = undefined;
  processTileQueue();
}));
rotationInput.addEventListener("pointercancel", () => { rotationStart = undefined; });
rotationInput.addEventListener("input", event => {
  const next = Number(event.currentTarget.value) * Math.PI / 180;
  const delta = next - (rotationStart ?? state.rotation);
  const horizontal = screenYaw([1, 0], delta);
  const vertical = screenYaw([0, 1], delta);
  canvas.style.willChange = "transform";
  canvas.style.transformOrigin = "50% 50%";
  canvas.style.transform = `matrix(${horizontal[0]},${horizontal[1]},${vertical[0]},${vertical[1]},0,0)`;
});
rotationInput.addEventListener("change", event => {
  const oldRotation = state.rotation;
  const oldZoom = state.zoom;
  const centre = yawIso([-state.panX / oldZoom, -state.panY / oldZoom], -oldRotation);
  state.rotation = Number(event.currentTarget.value) * Math.PI / 180;
  const projectedCentre = yawIso(centre);
  state.panX = -projectedCentre[0] * oldZoom;
  state.panY = -projectedCentre[1] * oldZoom;
  canvas.style.transform = "none";
  canvas.style.willChange = "auto";
  fallbackView = renderedMap?.ready ? undefined : overviewFallback();
  document.documentElement.dataset.rotation = event.currentTarget.value;
  rotationStart = undefined;
  draw();
});
window.addEventListener("resize", resize);

function loadMap(data) {
    if (!data) throw new Error("Falta data/flores-data.js");
    city = data;
    world = prepare(data);
    renderedMap = undefined;
    groundMap = undefined;
    tileCache.clear();
    tileQueue.length = 0;
    wantedTiles = new Set();
    delete document.documentElement.dataset.tileMaxMs;
    fitWorld();
    if (!exporting && Number(query.get("zoom"))) state.zoom = Math.min(MAX_ZOOM, Math.max(state.minZoom, Number(query.get("zoom"))));
    resize();
    const heading = document.querySelector("#area-title");
    heading.textContent = data.meta.name;
    document.title = `${data.meta.name} — Atlas CABA 3D`;
    document.querySelector("#status").textContent = `${data.meta.counts.buildings.toLocaleString("es-AR")} volúmenes reales`;
    document.querySelector("#detail").textContent = `${data.meta.counts.roads.toLocaleString("es-AR")} tramos · ${data.meta.counts.parks} espacios verdes`;
    if (window.SELECTED_RENDER) {
      const image = window.CABA_RENDER_IMAGE || new Image();
      renderedMap = { ...window.SELECTED_RENDER, image, ready: false };
      document.querySelector("#status").textContent = `${renderedMap.buildings.toLocaleString("es-AR")} volúmenes pre-renderizados`;
      document.querySelector("#detail").textContent = "Cargando mapa completo guardado…";
      image.onload = () => {
        renderedMap.ready = true;
        tileQueue.length = 0;
        document.documentElement.dataset.rendered = renderedMap.buildings;
        document.querySelector("#detail").textContent = `${renderedMap.roads.toLocaleString("es-AR")} tramos · ${renderedMap.parks.toLocaleString("es-AR")} espacios verdes`;
        draw();
      };
      image.onerror = () => { document.querySelector("#detail").textContent = "No se pudo cargar el mapa pre-renderizado"; };
      image.src = renderedMap.src;
      if (renderedMap.ground) {
        const groundImage = new Image();
        groundMap = { ...renderedMap, image: groundImage, ready: false };
        groundImage.onload = () => {
          groundMap.ready = true;
          if (vectorMode) { tileCache.clear(); draw(); }
        };
      }
    }
    if (exporting) document.documentElement.dataset.ready = "true";
}

function ensureVectorMap() {
  if (vectorMode || vectorDataPromise || window.SELECTED_AREA?.name === "CABA completa") return vectorDataPromise;
  if (groundMap && !groundMap.image.src) groundMap.image.src = groundMap.ground;
  vectorDataPromise = new Promise(resolve => {
    const script = document.createElement("script");
    script.src = window.SELECTED_AREA.file;
    script.onload = () => {
      const data = window.SELECTED_AREA.name === "Flores" ? window.FLORES_DATA : window.MAP_DATA;
      if (!data?.buildings?.length) return resolve(false);
      const centre = screenYaw([-state.panX / state.zoom, -state.panY / state.zoom], -state.rotation);
      fallbackView = snapshotFallback();
      vectorMode = true;
      renderedMap = undefined;
      city = data;
      world = prepare(data);
      tileCache.clear();
      tileQueue.length = 0;
      const projectedCentre = yawIso(centre);
      state.panX = -projectedCentre[0] * state.zoom;
      state.panY = -projectedCentre[1] * state.zoom;
      document.documentElement.dataset.vectors = data.buildings.length;
      document.querySelector("#status").textContent = `${data.buildings.length.toLocaleString("es-AR")} volúmenes 3D`;
      draw();
      resolve(true);
    };
    script.onerror = () => {
      document.querySelector("#detail").textContent = "Vista guardada: no se pudo cargar la geometría 3D";
      resolve(false);
    };
    document.head.append(script);
  });
  return vectorDataPromise;
}

const areaSelect = document.querySelector("#area");
(window.CABA_AREAS || [{ name: "Flores", file: "data/flores-data.js" }]).forEach(area => {
  const option = document.createElement("option");
  option.value = area.file;
  option.textContent = area.name;
  option.selected = area.name === (window.SELECTED_AREA?.name || "Flores");
  areaSelect.append(option);
});
areaSelect.addEventListener("change", () => {
  const area = window.CABA_AREAS.find(item => item.file === areaSelect.value);
  const url = new URL(location.href);
  url.searchParams.set("area", area.name);
  location.href = url.href;
});

try {
    window.MAP_DATA ||= window.FLORES_DATA;
    loadMap(window.MAP_DATA);
  } catch (error) {
    document.querySelector("#status").textContent = "No se pudo cargar el mapa";
    document.querySelector("#detail").textContent = error.message;
    console.error(error);
  }
