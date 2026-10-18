// Aquagentic: algal blooms at Iceland's licensed fish and shellfish farms.
// Modes: Ocean data (one input on the map) · Past blooms (detected blooms, farms pulse when hit)
//        · Forecast (bloom risk per farm, why, and how sure we are).
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const getJSON = (path) => fetch(path).then((r) => { if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json(); });
const cache = {};
const once = (key, fn) => (cache[key] ??= fn().catch((e) => { delete cache[key]; throw e; }));

const S = {
  mode: "forecast", layer: "chl", horizon: 3, fjord: true,
  cfg: null, farms: [], byName: {}, year: null, date: null, days: [],
  day: {}, prevDay: {}, timeline: null, farm: null, farmData: null, conf: null,
  playing: null, speed: 1, layerDates: {}, kindFilter: "all",
};

const LAYER_ORDER = ["chl", "chl_obs", "anomaly", "sst", "salinity", "mixed_layer", "currents", "waves", "rrs555"];
const ICONS = {
  chl: '<path d="M5 19c0-8 6-14 14-14 0 8-6 14-14 14Z"/><path d="M5 19l8-8"/>',
  chl_obs: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  anomaly: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  sst: '<path d="M10 14V5a2 2 0 1 1 4 0v9a4 4 0 1 1-4 0Z"/><path d="M12 10v6"/>',
  salinity: '<path d="M12 3c3.5 4 6 7.2 6 10.2A6 6 0 0 1 6 13.2C6 10.2 8.5 7 12 3Z"/>',
  mixed_layer: '<path d="M3 7l9-4 9 4-9 4-9-4Z"/><path d="M3 12l9 4 9-4"/><path d="M3 17l9 4 9-4"/>',
  currents: '<path d="M3 8h14l-3-3"/><path d="M21 16H7l3 3"/>',
  waves: '<path d="M2 15c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2"/><path d="M2 9c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2"/>',
  rrs555: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  pin: '<path d="M12 21s-6-5.6-6-11a6 6 0 1 1 12 0c0 5.4-6 11-6 11Z"/><circle cx="12" cy="10" r="2.2"/>',
  fish: '<path d="M3 12c3-4.5 9-6 14-2l4-3v10l-4-3c-5 4-11 2.5-14-2Z"/><circle cx="8" cy="11" r=".8" fill="currentColor"/>',
  shell: '<path d="M12 20c-5 0-8-3.5-8-7.5C4 8 8 4 12 4s8 4 8 8.5C20 16.5 17 20 12 20Z"/><path d="M12 20V7M8 19l2-11M16 19l-2-11"/>',
  person: '<circle cx="12" cy="7.5" r="3.2"/><path d="M5.5 20c.8-4 3.4-6 6.5-6s5.7 2 6.5 6"/>',
  lake: '<path d="M3 14c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0"/><path d="M3 18c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0"/><path d="M7 10l3-5 3 4 2-2 3 3"/>',
  sat: '<path d="M13 7l4 4M5 15l4 4M9.5 10.5l4 4"/><rect x="9" y="7.5" width="5" height="9" rx="1" transform="rotate(45 11.5 12)"/><path d="M17 3a4 4 0 0 1 4 4M16.5 5.5a1.5 1.5 0 0 1 2 2"/>',
};

const icon = (k, cls = "") => `<svg viewBox="0 0 24 24" class="${cls}">${ICONS[k] || ""}</svg>`;
const FMT = {
  chl: (v) => `${v.toFixed(v < 1 ? 2 : 1)} mg/m³`, chl_obs: (v) => `${v.toFixed(v < 1 ? 2 : 1)} mg/m³`,
  anomaly: (v) => `${v.toFixed(1)}× normal`, bloom: (v) => `${v.toFixed(1)}× normal`, sst: (v) => `${v.toFixed(1)} °C`,
  salinity: (v) => `${v.toFixed(2)} PSU`, mixed_layer: (v) => `${Math.round(v)} m deep`, currents: (v) => `${v.toFixed(2)} m/s`,
  waves: (v) => `${v.toFixed(1)} m`, rrs555: (v) => `${v.toFixed(4)} sr⁻¹${v >= 0.008 ? " · milky" : ""}`,
};
const LEVEL_COLOR = { low: "var(--low)", medium: "var(--medium)", high: "var(--high)" };

// ---------------- static data (replaces the Python API) ----------------
const expand = (rngs) => { const out = new Set(); for (const [a, b] of rngs || []) for (let d = a; d <= b; d = addDays(d, 1)) out.add(d); return out; };
const level = (p) => (p == null ? null : p >= S.cfg.risk.high ? "high" : p >= S.cfg.risk.low ? "medium" : "low");
const dayDiff = (a, b) => Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 864e5);
const kmBetween = (a, b) => { const dy = (a.lat - b.lat) * 111.2, dx = (a.lon - b.lon) * 111.2 * Math.cos(((a.lat + b.lat) / 2) * Math.PI / 180); return Math.hypot(dx, dy); };

async function dayStatus(date) {
  const Y = await once(`day${date.slice(0, 4)}`, () => getJSON(`data/day/${date.slice(0, 4)}.json`));
  const i = Y.dates.indexOf(date), out = {};
  if (i < 0) return out;
  Y.farms.forEach((name, j) => {
    const r3 = Y.r3[i][j], r7 = Y.r7[i][j];
    out[name] = { bloom: Y.bloom[i][j] === "1", milky: Y.milky[i][j] === "1", data: Y.data[i][j] === "1", ratio: Y.ratio[i][j],
                  risk_3d: r3, risk_7d: r7, level_3d: level(r3), level_7d: level(r7) };
  });
  return out;
}

async function farmDetails(name, date) {
  const info = S.byName[name];
  const F = await once(`farm${info.slug}`, () => getJSON(`data/farm/${info.slug}.json`));
  const D = F.daily, R_ = S.cfg.rule, i = dayDiff(F.start, date);
  const at = (k, j = i) => (j >= 0 && j < D[k].length ? D[k][j] : null);
  const win = (k) => D[k].slice(Math.max(0, i - 60), i + 1);
  const inputs = [["chl", "Chlorophyll", "chl", "mg/m³"], ["anomaly", "Vs normal", "ratio", "×"], ["sst", "Sea temperature", "sst", "°C"],
    ["salinity", "Salinity", "sal", "PSU"], ["mixed_layer", "Surface layer", "mld", "m"], ["currents", "Currents", "cur", "m/s"],
    ["waves", "Waves", "wav", "m"], ["wind", "Wind", "wind", "m/s"], ["sun", "Sunlight", "sun", "MJ/m²"]]
    .map(([key, label, k, unit]) => ({ key, label, unit, value: at(k), spark: win(k) }));
  const covering = F.events.filter((e) => e.start <= date && e.end >= date).sort((a, b) => (a.status === "confirmed" ? -1 : 1) - (b.status === "confirmed" ? -1 : 1));
  const hb = covering.find((e) => e.kind === "high-biomass"), mk = covering.find((e) => e.kind === "coccolithophore");
  // the bloom test: on the bloom's peak day if we are inside one (that is how it was found), otherwise today
  const ref = hb ? dayDiff(F.start, hb.peak) : i;
  const chl = at("chl", ref), thr = at("thr", ref), ratio = at("ratio", ref), frac = at("frac", ref);
  const checks = chl == null ? null : { bloom: at("bloom") === 1, on: hb ? hb.peak : date, steps: [
    { label: `Algae above ${R_.min_chl} mg/m³`, value: `${chl.toFixed(1)} mg/m³`, ok: chl >= R_.min_chl },
    { label: `At least ${R_.ratio}× the normal level`, value: ratio != null ? `${ratio.toFixed(1)}× normal` : "–", ok: ratio != null && ratio >= R_.ratio },
    { label: `Rarer than ${R_.percentile}% of days on this date (${R_.baseline[0]}–${R_.baseline[1]})`, value: thr != null ? `limit ${thr.toFixed(1)} mg/m³` : "–", ok: thr != null && chl >= thr },
    { label: `Covers ≥${Math.round(R_.area * 100)}% of the sea within ${R_.radius_km} km`, value: frac != null ? `${Math.round(frac * 100)}%` : "–", ok: frac != null && frac >= R_.area },
    { label: "Lasts at least 3 days", value: hb ? `${hb.days} days` : "–", ok: !!hb && hb.days >= 3 },
    { label: "Seen by the satellite on a clear day", value: hb ? `${hb.obs} clear day${hb.obs === 1 ? "" : "s"}` : "–", ok: !!hb && hb.obs > 0 },
  ] };
  let milky_checks = null;
  if (mk || at("cocco") === 1) {
    const rrs = at("rrs"), normal = F.rrs_normal[+date.slice(5, 7) - 1], rr = rrs != null && normal ? rrs / normal : null;
    milky_checks = { bloom: at("milky") === 1, steps: [
      { label: "Very bright green light (≥ 0.008 sr⁻¹)", value: rrs != null ? rrs.toFixed(4) : "cloudy today", ok: rrs != null && rrs >= 0.008 },
      { label: `At least 3× this farm's normal for ${fmtDay(date, { month: "long" })}`, value: rr ? `${rr.toFixed(1)}×` : "–", ok: !!rr && rr >= 3 },
      { label: "Clear water, not muddy (Secchi ≥ 5 m)", value: F.secchi != null ? `${Math.round(F.secchi)} m` : "–", ok: F.secchi != null && F.secchi >= 5 },
      { label: "In coccolithophore season (May–Oct)", value: fmtDay(date, { month: "long" }), ok: +date.slice(5, 7) >= 5 && +date.slice(5, 7) <= 10 },
      { label: "Seen on at least 2 clear days", value: mk ? `${mk.obs} clear days` : "–", ok: !!mk && mk.obs >= 2 },
    ] };
  }
  const c = covering[0];
  const current = c ? { kind: c.kind, start: c.start, end: c.end, days: c.days, day: dayDiff(c.start, date) + 1, status: c.status,
                        type: c.type, peak_chl: c.peak_chl, peak_ratio: c.peak_ratio } : null;
  const history = F.events.filter((e) => e.status === "confirmed").sort((a, b) => (a.start < b.start ? 1 : -1))
    .map((e) => ({ kind: e.kind, start: e.start, peak: e.peak, days: e.days, peak_chl: e.peak_chl, peak_ratio: e.peak_ratio, type: e.type }));
  let forecast = null;
  if (at("r3") != null) {
    const lo = Math.max(0, i - 60), hi = Math.min(D.r3.length, i + 22);
    forecast = { trained_until: F.trained_until[date.slice(0, 4)],
      "3d": { risk: at("r3"), level: level(at("r3")), happened: at("b3") },
      "7d": { risk: at("r7"), level: level(at("r7")), happened: at("b7") },
      series: { date: Array.from({ length: hi - lo }, (_, k) => addDays(F.start, lo + k)), risk_3d: D.r3.slice(lo, hi),
                risk_7d: D.r7.slice(lo, hi), bloom: D.bloom.slice(lo, hi).map((v) => v || 0) } };
  }
  let toxic = null;
  if (info.kind === "shellfish") {
    const st = (S.cfg.stations || []).map((x) => ({ ...x, km: Math.round(kmBetween(x, info) * 10) / 10 })).sort((a, b) => a.km - b.km)[0];
    toxic = { month: S.cfg.toxic_by_month?.[+date.slice(5, 7)] || null, station: st || null };
  }
  return { info, date, inputs, checks, milky_checks, current, history, forecast, toxic };
}

// cursor read-out: read the tile pixel under the mouse and turn its colour back into a value
const pix = { url: null, data: null, w: 0, h: 0 };
function grabPixels(img, url) {
  try {
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    Object.assign(pix, { url, data: ctx.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height });
  } catch { pix.url = null; }
}
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
function pixelValue(key, lat, lon) {
  const L_ = S.cfg.layers[key];
  if (!L_?.lut || pix.url !== `tiles/${key}/${S.date}.png` || !pix.data) return null;
  const [[s_, w_], [n_, e_]] = L_.bounds;
  const x = Math.floor(((lon - w_) / (e_ - w_)) * pix.w), y = Math.floor(((mercY(n_) - mercY(lat)) / (mercY(n_) - mercY(s_))) * pix.h);
  if (x < 0 || y < 0 || x >= pix.w || y >= pix.h) return null;
  const o = (y * pix.w + x) * 4, [r, g, b, a] = [pix.data[o], pix.data[o + 1], pix.data[o + 2], pix.data[o + 3]];
  if (a < 20) return null;
  let best = null, bd = Infinity;
  for (const [v, R2, G, B] of L_.lut) { const d = (R2 - r) ** 2 + (G - g) ** 2 + (B - b) ** 2; if (d < bd) { bd = d; best = v; } }
  return bd < 2500 ? best : null;
}

// ---------------- dates ----------------
const iso = (d) => d.toISOString().slice(0, 10);
const seasonDays = (y) => { const out = []; for (let d = new Date(Date.UTC(y, 2, 1)); d <= new Date(Date.UTC(y, 9, 31)); d.setUTCDate(d.getUTCDate() + 1)) out.push(iso(d)); return out; };
const fmtDay = (s, opt = { day: "numeric", month: "long", year: "numeric" }) => new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { ...opt, timeZone: "UTC" });
const fmtShort = (s) => fmtDay(s, { day: "numeric", month: "short", year: "numeric" });
const addDays = (s, n) => { const d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return iso(d); };

// ---------------- map ----------------
const map = L.map("map", { zoomControl: false, minZoom: 5, maxZoom: 13, worldCopyJump: false, zoomSnap: 0.5 })
  .setView([64.95, -19.0], 6.5);
const esriOcean = (n) => `https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/${n}/MapServer/tile/{z}/{y}/{x}`;
map.createPane("labels").style.zIndex = 450;
map.getPane("labels").style.pointerEvents = "none";
L.tileLayer(esriOcean("World_Ocean_Base"), { maxZoom: 13, attribution: "Esri, GEBCO · Copernicus Marine · MAST" }).addTo(map);
L.tileLayer(esriOcean("World_Ocean_Reference"), { maxZoom: 13, pane: "labels" }).addTo(map);
const HOME = [[63.2, -25.2], [66.8, -12.8]];

let overlay = null;
const fjordOverlays = {};
const markers = {};
let polygons = null;

function overlayKey() {
  if (S.mode === "data") return S.layer;
  if (S.mode === "past") return "bloom";
  return null;
}

function updateOverlay() {
  const key = overlayKey();
  const L_ = key && S.cfg.layers[key];
  const has = L_ && S.layerDates[key]?.has(S.date);
  if (has) {
    const url = `tiles/${key}/${S.date}.png`;
    if (!overlay) {
      overlay = L.imageOverlay(url, L_.bounds, { opacity: 0.95, interactive: false, className: "fade-edge" }).addTo(map);
      overlay.on("load", () => grabPixels(overlay.getElement(), overlay._url));
    } else { overlay.setBounds(L.latLngBounds(L_.bounds)); overlay.setUrl(url); }
    overlay.setOpacity(0.95);
  } else if (overlay) overlay.setOpacity(0);
  // 300 m fjord detail on top of chlorophyll
  const F = S.cfg.chl300;
  const showFjord = S.mode === "data" && S.fjord && (key === "chl" || key === "chl_obs");
  for (const [slug, info] of Object.entries(F?.sites || {})) {
    const ok = showFjord && info.dateSet.has(S.date);
    const u = `tiles/chl300/${slug}/${S.date}.png`;
    if (ok) {
      if (!fjordOverlays[slug]) fjordOverlays[slug] = L.imageOverlay(u, info.bounds, { interactive: false, className: "fade-edge fade-small" }).addTo(map);
      else fjordOverlays[slug].setUrl(u).setOpacity(1);
    } else if (fjordOverlays[slug]) fjordOverlays[slug].setOpacity(0);
  }
  renderLegend(key, has);
  preloadNext(key);
}

function preloadNext(key) {
  if (!key) return;
  const i = S.days.indexOf(S.date);
  for (const d of S.days.slice(i + 1, i + 4)) if (S.layerDates[key]?.has(d)) new Image().src = `tiles/${key}/${d}.png`;
}

// ---------------- farm markers ----------------
function farmIcon(f, cls) {
  return L.divIcon({ className: "", iconSize: [14, 14], iconAnchor: [7, 7],
    html: `<div class="farm ${f.kind} ${cls}"><span class="ring"></span><span class="core"></span></div>` });
}

function farmStateClass(f) {
  const d = S.day[f.name] || {};
  if (S.mode === "past") return d.milky && !d.bloom ? "milky" : d.bloom ? "bloom" : d.data ? "" : "nodata";
  if (S.mode === "forecast") {
    const lv = d[`level_${S.horizon}d`];
    return lv ? `risk-${lv}` : "nodata";
  }
  return "";
}

function farmTip(f) {
  const d = S.day[f.name] || {};
  let line = "";
  if (S.mode === "past") line = d.bloom ? `<span style="color:var(--bloom)">● In a bloom</span>${d.ratio ? ` · ${d.ratio.toFixed(1)}× normal` : ""}`
    : d.milky ? `<span style="color:var(--milky)">● Milky-water bloom</span>` : d.data ? "No bloom" : "No satellite data";
  if (S.mode === "forecast") line = d[`risk_${S.horizon}d`] != null
    ? `Bloom risk next ${S.horizon} days: <b style="color:${LEVEL_COLOR[d[`level_${S.horizon}d`]]}">${pct(d[`risk_${S.horizon}d`])}</b>` : "No forecast this day";
  return `<b>${esc(f.farm)}</b><br><span class="m">${[f.company, f.area].filter(Boolean).map(esc).join(" · ")}</span>${line ? `<br>${line}` : ""}`;
}

function drawFarms() {
  for (const f of S.farms) {
    const m = L.marker([f.lat, f.lon], { icon: farmIcon(f, ""), keyboard: false, riseOnHover: true, zIndexOffset: 500 }).addTo(map);
    m.bindTooltip(() => farmTip(f), { className: "farmtip", direction: "top", offset: [0, -8], opacity: 1 });
    m.on("click", () => openFarm(f.name));
    markers[f.name] = m;
  }
  const feats = S.farms.filter((f) => f.geometry).map((f) => ({ type: "Feature", geometry: f.geometry, properties: { name: f.name } }));
  polygons = L.geoJSON({ type: "FeatureCollection", features: feats }, {
    style: { color: "#ffffff", weight: 1.2, opacity: 0.8, fillColor: "#ffffff", fillOpacity: 0.08 },
    onEachFeature: (ft, layer) => layer.on("click", () => openFarm(ft.properties.name)),
  });
  const syncPolys = () => (map.getZoom() >= 10 ? polygons.addTo(map) : polygons.remove());
  map.on("zoomend", syncPolys);
  syncPolys();
}

function updateMarkers() {
  for (const f of S.farms) {
    const m = markers[f.name];
    if (!shown(f)) { if (map.hasLayer(m)) m.remove(); continue; }   // farm-type filter
    if (!map.hasLayer(m)) m.addTo(map);
    const el = m.getElement()?.firstElementChild;
    if (!el) continue;
    const cls = farmStateClass(f);
    const wasHit = S.prevDay[f.name]?.bloom || S.prevDay[f.name]?.milky;
    const isHit = S.mode === "past" && (S.day[f.name]?.bloom || S.day[f.name]?.milky);
    el.className = `farm ${f.kind} ${cls} ${S.farm === f.name ? "selected" : ""} ${isHit && !wasHit ? "hit" : ""}`;
    m.setZIndexOffset(cls.includes("high") || cls === "bloom" ? 900 : S.farm === f.name ? 1000 : 500);
    if (m.isTooltipOpen()) m.setTooltipContent(farmTip(f));
  }
}

// ---------------- legend ----------------
function renderLegend(key, has) {
  const box = $("#legend");
  if (S.mode === "forecast") {
    box.innerHTML = `<div class="lt"><span>Bloom risk, next ${S.horizon} days</span></div>
      <div class="items"><span><i class="keydot" style="background:var(--high)"></i>High · over ${pct(S.cfg.risk.high)}</span>
      <span><i class="keydot" style="background:var(--medium)"></i>Medium · ${pct(S.cfg.risk.low)}–${pct(S.cfg.risk.high)}</span>
      <span><i class="keydot" style="background:var(--low)"></i>Low · under ${pct(S.cfg.risk.low)}</span>
      <span><i class="keydot" style="background:#8a95a3"></i>No forecast (no data)</span></div>`;
    return;
  }
  const L_ = S.cfg.layers[key];
  if (!L_) { box.innerHTML = ""; return; }
  const p = (v) => (L_.log ? (Math.log(v) - Math.log(L_.min)) / (Math.log(L_.max) - Math.log(L_.min)) : (v - L_.min) / (L_.max - L_.min));
  const grad = L_.stops.map((s, i) => `${s.c} ${(i / (L_.stops.length - 1)) * 100}%`).join(",");
  const month = +S.date.slice(5, 7);
  const why = month >= 11 || month <= 2 ? "Polar night: satellites can't see the sea." : "No satellite data this day (cloud or gap).";
  const title = S.mode === "past" ? "Bloom strength" : L_.title;
  box.innerHTML = `<div class="lt"><span>${esc(title)}</span><span>${esc(L_.units)}</span></div>
    <div class="bar" style="background:linear-gradient(90deg,${grad})"></div>
    <div class="ticks">${L_.ticks.map((t) => `<span style="left:${p(t) * 100}%">${t}</span>`).join("")}</div>
    ${S.mode === "past" ? `<div class="items" style="margin-top:8px"><span><i class="keydot" style="background:var(--bloom)"></i>Farm in a confirmed bloom</span><span><i class="keydot" style="background:var(--milky)"></i>Farm in milky water (coccolithophores)</span></div>` : ""}
    ${has ? "" : `<div class="nodata">${why}</div>`}`;
}

// ---------------- farm type filter ----------------
const shown = (f) => S.kindFilter === "all" || f.kind === S.kindFilter;
function filterBar() {
  const n = (k) => S.farms.filter((f) => k === "all" || f.kind === k).length;
  return `<div class="filter" role="group" aria-label="Farm type">${[["all", "All"], ["sea", "Sea pens"], ["shellfish", "Shellfish"]]
    .map(([k, t]) => `<button data-kind="${k}" class="${S.kindFilter === k ? "on" : ""}">${t} <small>${n(k)}</small></button>`).join("")}</div>`;
}
function setKind(k) {
  S.kindFilter = k;
  updateMarkers(); renderLeft();
}

// ---------------- left panel ----------------
function renderLeft() {
  const box = $("#left");
  if (S.mode === "data") return renderLeftData(box);
  if (S.mode === "past") return renderLeftPast(box);
  return renderLeftForecast(box);
}

function renderLeftData(box) {
  const L_ = S.cfg.layers[S.layer];
  box.innerHTML = `
    <h2>Ocean data</h2>
    <p class="sub">Pick one input. The map shows only that, for the chosen day. These are the inputs the forecast reads.</p>
    ${filterBar()}
    <div class="layergrid">${LAYER_ORDER.filter((k) => S.cfg.layers[k]).map((k) => `
      <button data-layer="${k}" class="${k === S.layer ? "on" : ""}">${icon(k)}${esc(S.cfg.layers[k].title)}</button>`).join("")}</div>
    <div class="infobox">
      <h3>${esc(L_.title)}</h3><p>${esc(L_.description)}</p>
      <div class="role"><svg viewBox="0 0 24 24"><path d="M3 17l5-5 4 3 8-8"/></svg><span>In the forecast: ${esc(L_.role)}</span></div>
    </div>
    ${S.layer === "chl" || S.layer === "chl_obs" ? `<div class="toggle"><span>Fjord detail (300 m, clear days)</span>
      <button class="switch" id="fjordSwitch" role="switch" aria-checked="${S.fjord}" aria-label="Fjord detail"></button></div>` : ""}
    <div class="keyrow"><span><i class="keydot" style="background:#fff;box-shadow:0 0 0 2px #0a141e"></i>Sea-pen farm</span>
      <span><i class="keydot" style="background:#ffe1cf;border-radius:3px;box-shadow:0 0 0 2px #0a141e"></i>Shellfish farm</span></div>`;
  $$("[data-layer]", box).forEach((b) => b.onclick = () => setLayer(b.dataset.layer));
  $$("[data-kind]", box).forEach((b) => b.onclick = () => setKind(b.dataset.kind));
  const sw = $("#fjordSwitch", box);
  if (sw) sw.onclick = () => { S.fjord = !S.fjord; sw.setAttribute("aria-checked", S.fjord); updateOverlay(); };
}

function renderLeftPast(box) {
  const hit = S.farms.filter((f) => shown(f) && (S.day[f.name]?.bloom || S.day[f.name]?.milky));
  hit.sort((a, b) => (S.day[b.name].ratio || 0) - (S.day[a.name].ratio || 0));
  const busiest = (S.timeline?.days || []).filter((d) => d.bloom + d.milky > 0)
    .sort((a, b) => b.bloom + b.milky - (a.bloom + a.milky)).slice(0, 5);
  const row = (f) => { const d = S.day[f.name];
    return `<button class="row ${S.farm === f.name ? "selected" : ""}" data-farm="${esc(f.name)}"><span class="t">${esc(f.farm)}</span>
      <span class="v" style="color:${d.bloom ? "var(--bloom)" : "var(--milky)"}">${d.bloom ? (d.ratio ? `${d.ratio.toFixed(1)}×` : "bloom") : "milky"}</span>
      <span class="s">${esc(f.area)} · ${f.kind === "sea" ? "sea pens" : "shellfish"}</span></button>`; };
  box.innerHTML = `
    <h2>Past blooms</h2>
    <p class="sub">Blooms our detector found, day by day. A farm pulses when a bloom reaches it.</p>
    ${filterBar()}
    <div class="hero"><span class="num" style="color:${hit.length ? "var(--bloom)" : "var(--text)"}">${hit.length}</span>
      <span class="lab">farm${hit.length === 1 ? "" : "s"} in a bloom<br>on ${fmtShort(S.date)}</span></div>
    ${hit.length ? `<div class="list">${hit.map(row).join("")}</div>`
      : `<div class="empty-note">No farm is inside a bloom on this day.${busiest.length ? " Try one of the busiest days below, or press play." : ""}</div>`}
    ${busiest.length ? `<div class="section-title">Busiest days in ${S.year}${S.kindFilter === "all" ? "" : " · all farm types"}</div>
      <div class="list">${busiest.map((d) => `<button class="row" data-jump="${d.date}"><span class="t">${fmtShort(d.date)}</span>
        <span class="v" style="color:var(--bloom)">${d.bloom + d.milky} farms</span></button>`).join("")}</div>` : `<div class="empty-note">No blooms at farms in ${S.year}.</div>`}
    <button class="linkbtn" data-how>How do we decide it's a bloom? →</button>`;
  bindLeft(box);
}

function renderLeftForecast(box) {
  const h = S.horizon;
  const rows = S.farms.filter(shown).map((f) => ({ f, d: S.day[f.name] || {} })).filter((x) => x.d[`risk_${h}d`] != null)
    .sort((a, b) => b.d[`risk_${h}d`] - a.d[`risk_${h}d`]);
  const nHigh = rows.filter((x) => x.d[`level_${h}d`] === "high").length;
  const nMed = rows.filter((x) => x.d[`level_${h}d`] === "medium").length;
  box.innerHTML = `
    <h2>Forecast</h2>
    <p class="sub">Chance of a bloom at each farm, as issued on ${fmtShort(S.date)}.</p>
    ${filterBar()}
    <div class="seg" role="group" aria-label="Horizon">
      <button data-h="3" class="${h === 3 ? "on" : ""}">Next 3 days</button><button data-h="7" class="${h === 7 ? "on" : ""}">Next 7 days</button></div>
    ${rows.length ? `<div class="hero"><span class="num" style="color:${nHigh ? "var(--high)" : nMed ? "var(--medium)" : "var(--low)"}">${nHigh || nMed}</span>
      <span class="lab">farm${(nHigh || nMed) === 1 ? "" : "s"} at ${nHigh ? "high" : "medium"} risk<br>${nHigh && nMed ? `+ ${nMed} at medium` : nHigh || nMed ? "" : "all farms low risk"}</span></div>
      <div class="list">${rows.slice(0, 14).map(({ f, d }) => `
        <button class="row ${S.farm === f.name ? "selected" : ""}" data-farm="${esc(f.name)}"><span class="t">${esc(f.farm)}</span>
        <span class="v" style="color:${LEVEL_COLOR[d[`level_${h}d`]]}">${pct(d[`risk_${h}d`])}</span>
        <span class="riskbar"><i style="width:${Math.max(2, d[`risk_${h}d`] * 100)}%;background:${LEVEL_COLOR[d[`level_${h}d`]]}"></i></span>
        <span class="s">${esc(f.area)}</span></button>`).join("")}</div>
      ${rows.length > 14 ? `<p class="muted" style="margin:-6px 0 0;font-size:12px">+ ${rows.length - 14} more farms at lower risk</p>` : ""}`
      : `<div class="empty-note">${+S.date.slice(5, 7) >= 11 || +S.date.slice(5, 7) <= 2 ? "No forecast in winter: satellites can't see the sea in the polar night." : "No forecast for this day."}</div>`}`;
  bindLeft(box);
}

function bindLeft(box) {
  $$("[data-farm]", box).forEach((b) => b.onclick = () => openFarm(b.dataset.farm, true));
  $$("[data-jump]", box).forEach((b) => b.onclick = () => setDate(b.dataset.jump));
  $$("[data-h]", box).forEach((b) => b.onclick = () => setHorizon(+b.dataset.h));
  $$("[data-how]", box).forEach((b) => b.onclick = openHow);
  $$("[data-kind]", box).forEach((b) => b.onclick = () => setKind(b.dataset.kind));
}

// ---------------- farm card ----------------
async function openFarm(name, fly = false) {
  S.farm = name;
  const f = S.byName[name];
  if (fly && f) map.flyTo([f.lat, f.lon], Math.max(map.getZoom(), 8.5), { duration: 0.8 });
  document.body.classList.add("card-open");
  updateMarkers();
  await loadFarm();
  syncUrl();
}

async function loadFarm() {
  if (!S.farm) return;
  try { S.farmData = await farmDetails(S.farm, S.date); }
  catch { S.farmData = null; }
  renderCard();
}

function closeFarm() {
  S.farm = null; S.farmData = null;
  $("#card").hidden = true;
  document.body.classList.remove("card-open");
  updateMarkers(); renderLeft(); syncUrl();
}

function renderCard() {
  const box = $("#card");
  const D = S.farmData;
  if (!D) { box.hidden = true; return; }
  const I = D.info;
  const head = `
    <div class="head"><div>
      <span class="chip ${I.kind}">${I.kind === "sea" ? "Sea pens" : "Shellfish"}</span>
      <h2>${esc(I.farm)}</h2>
      <div class="who">${esc(I.company || "")}${I.company ? " · " : ""}${esc(I.area)}</div>
    </div><button class="close" id="cardClose" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <div class="meta">${I.species?.length ? `<span><b>${esc(I.species.join(", "))}</b></span>` : ""}
      ${I.biomass_limit_t ? `<span>Max <b>${Number(I.biomass_limit_t).toLocaleString("en")} t</b></span>` : ""}
      ${I.licence ? `<span>Licence <b>${esc(I.licence)}</b></span>` : ""}
      ${I.in_use === false ? `<span><b>Not in use now</b></span>` : ""}</div>`;
  let body = "";
  if (S.mode === "forecast") body = cardForecast(D);
  else if (S.mode === "past") body = cardPast(D);
  else body = cardData(D);
  // shellfish: toxins are the main risk, so they come first
  box.innerHTML = I.kind === "shellfish" ? head + toxicNote(D) + body : head + body;
  box.hidden = false;
  $("#cardClose").onclick = closeFarm;
  $$("[data-jump]", box).forEach((b) => b.onclick = () => setDate(b.dataset.jump));
  $$("[data-h]", box).forEach((b) => b.onclick = () => setHorizon(+b.dataset.h));
  $$("[data-maplayer]", box).forEach((b) => b.onclick = () => setLayer(b.dataset.maplayer));
  $$("[data-how]", box).forEach((b) => b.onclick = openHow);
}

function toxicNote(D) {
  const T = D.toxic;
  if (!T || (!T.month && !T.station)) return "";
  const M = T.month, St = T.station, mon = fmtDay(D.date, { month: "long" }), m0 = +D.date.slice(5, 7) - 1;
  const row = (k, name, what) => { const v = M?.[k] ?? 0;
    return `<div class="toxrow"><span class="nm"><b>${name}</b> <span>${what}</span></span><span class="tb"><i style="width:${Math.min(100, v * 250)}%"></i></span><span class="pc">${pct(v)}</span></div>`; };
  const mx = Math.max(0.05, ...(St?.months || []).map((v) => v || 0));
  return `<div class="toxcard">
    <h3>Toxic algae · the main risk for shellfish</h3>
    <div class="sub2">Can't be seen from space. Share of Hafró water samples above the safe limit in ${mon} (all stations, 2005–2019):</div>
    ${M ? `<div class="toxrows">${row("dsp", "DSP", "Dinophysis · diarrhoea")}${row("psp", "PSP", "Alexandrium · paralysis")}${row("asp", "ASP", "Pseudo-nitzschia · never found in Icelandic shellfish")}</div>` : ""}
    ${St ? `<div class="st">Nearest Hafró station: <b>${esc(St.name)}</b>, ${St.km} km away · ${St.samples} samples ${St.years} · toxic algae above the limit in <b>${pct(St.share_above)}</b>.
      Last sample ${fmtShort(St.last_date)}: ${St.last_above ? "<b>above the limit</b>" : "below the limit"}.</div>
      <div class="months12">${St.months.map((v, i) => `<div class="${v == null ? "none" : ""} ${i === m0 ? "cur" : ""}" style="height:${v == null ? 6 : Math.max(4, (v / mx) * 100)}%" title="${v == null ? "no samples" : pct(v)}"></div>`).join("")}</div>
      <div class="mlabels">${["J","F","M","A","M","J","J","A","S","O","N","D"].map((x) => `<span>${x}</span>`).join("")}</div>` : ""}
    <div class="st">Check MAST before harvesting.</div></div>`;
}

function gauge(h, F) {
  const r = 42, C = 2 * Math.PI * r, x = F?.risk ?? 0, lv = F?.level || "low";
  return `<div class="gauge ${S.horizon === h ? "on" : ""}" data-h="${h}">
    <svg viewBox="0 0 104 104"><circle class="gtrack" cx="52" cy="52" r="${r}"/>
      <circle class="gval" cx="52" cy="52" r="${r}" stroke="${LEVEL_COLOR[lv]}" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - x)}" transform="rotate(-90 52 52)"/>
      <text class="pct" x="52" y="60" text-anchor="middle">${F ? pct(x) : "–"}</text></svg>
    <div class="glab">Bloom in the next ${h} days</div>${F ? `<div class="lvl ${lv}">${lv} risk</div>` : ""}</div>`;
}

function cardForecast(D) {
  const F = D.forecast;
  if (!F) return `<div class="status"><div class="icon">${icon("anomaly")}</div><div><div class="big">No forecast this day</div>
    <div class="small">Forecasts run March–October, when satellites can see the sea.</div></div></div>`;
  const h = S.horizon, X = F[`${h}d`];
  const happened = X.happened == null ? "" : `<div class="happened">${X.happened ? `<span style="color:var(--bloom)">●</span> <span>What really happened: <b>a bloom came</b> within ${h} days.</span>`
    : `<span style="color:var(--low)">●</span> <span>What really happened: <b>no bloom</b> within ${h} days.</span>`}</div>`;
  return `
    <div class="gauges">${gauge(3, F["3d"])}${gauge(7, F["7d"])}</div>
    ${happened}
    <div class="block"><h3>Risk over time <span class="muted">past 60 days</span></h3>${riskChart(F.series, D.date)}
      <div class="chartkey"><span><i style="background:var(--accent)"></i>3-day risk</span><span><i style="background:var(--medium)"></i>7-day risk</span>
      <span><i class="band" style="background:rgba(255,55,95,.35)"></i>Confirmed bloom</span></div></div>
    <p class="muted" style="font-size:11.5px;margin:14px 0 0">Model trained only on data up to ${F.trained_until}, so this is what it would really have said on that day. <button class="linkbtn" data-how>How it works</button></p>`;
}

function cardPast(D) {
  const C = D.current, K = D.checks;
  let status;
  if (C && C.status === "confirmed" && C.kind === "coccolithophore") status = `<div class="status milky"><div class="icon">${icon("rrs555")}</div><div>
    <div class="big">Milky-water bloom</div><div class="small">Coccolithophores · day ${C.day} of ${C.days} · started ${fmtShort(C.start)}</div></div></div>`;
  else if (C && C.status === "confirmed") status = `<div class="status bloom"><div class="icon">${icon("chl")}</div><div>
    <div class="big">In a bloom · day ${C.day} of ${C.days}</div><div class="small">Peak ${C.peak_chl} mg/m³ (${C.peak_ratio}× normal) · ${esc(C.type)}</div></div></div>`;
  else if (K) status = `<div class="status"><div class="icon">${icon("chl")}</div><div><div class="big">No bloom</div>
    <div class="small">${C ? "A bloom-like signal, but it didn't pass every check." : "Algae at normal levels for this time of year."}</div></div></div>`;
  else status = `<div class="status"><div class="icon">${icon("chl_obs")}</div><div><div class="big">No satellite data</div>
    <div class="small">Cloud, a data gap, or the polar night.</div></div></div>`;
  const isMilky = C && C.kind === "coccolithophore" && D.milky_checks;
  const T = isMilky ? D.milky_checks : K;
  const onWhen = !isMilky && K && K.on !== D.date ? `on its peak day, ${fmtDay(K.on, { day: "numeric", month: "short" })}` : "on this day";
  const checks = T ? `<div class="block"><h3>How we know <span class="muted">${isMilky ? "our milky-water test" : "our bloom test"}, ${onWhen}</span></h3>
    <ul class="checks">${T.steps.map((s) => `<li class="${s.ok ? "ok" : "no"}"><span class="mark">${s.ok ? "✓" : "–"}</span><span>${esc(s.label)}</span><span class="val">${esc(s.value)}</span></li>`).join("")}</ul>
    <div class="verdict">${isMilky ? (C.status === "confirmed" ? "Bright, milky water on several clear days: a <b>coccolithophore bloom</b>. Clouds can hide it on single days." : "Not yet seen on enough clear days to confirm.")
      : K.bloom ? "All six checks pass, so we call it a <b>confirmed bloom</b>." : "A bloom needs all six checks."}
      <button class="linkbtn" data-how>Why these rules?</button></div></div>` : "";
  const hist = D.history.length ? `<div class="block"><h3>Bloom history <span class="muted">${D.history.length} confirmed since 1998</span></h3>
    <div class="hist">${D.history.slice(0, 8).map((e) => `<button data-jump="${e.peak}"><i class="dot ${e.kind === "coccolithophore" ? "milky" : ""}"></i>
      <span>${fmtShort(e.start)} · ${e.days} days</span><span class="r">${e.kind === "coccolithophore" ? "milky water" : `${e.peak_ratio}× normal`}</span></button>`).join("")}</div></div>`
    : `<div class="block"><h3>Bloom history</h3><div class="empty-note">No confirmed blooms at this farm since 1998.</div></div>`;
  return status + checks + hist;
}

function cardData(D) {
  const map_ = { chl: "chl", anomaly: "anomaly", sst: "sst", salinity: "salinity", mixed_layer: "mixed_layer", currents: "currents", waves: "waves" };
  const fmt = (x) => (x.value == null ? "–" : x.key === "anomaly" ? x.value.toFixed(1) : Math.abs(x.value) < 1 ? x.value.toFixed(2) : x.value.toFixed(x.value > 100 ? 0 : 1));
  return `<div class="block"><h3>Conditions at this farm <span class="muted">${fmtShort(D.date)} · last 60 days</span></h3>
    <div class="tiles">${D.inputs.map((x) => `<button class="tile ${map_[x.key] === S.layer ? "on" : ""}" ${map_[x.key] ? `data-maplayer="${map_[x.key]}"` : ""}>
      <div class="k">${esc(x.label)}</div><div class="v">${fmt(x)}<small>${x.value == null ? "" : esc(x.unit)}</small></div>${spark(x.spark)}</button>`).join("")}</div>
    <p class="muted" style="font-size:11.5px;margin:10px 0 0">Averages over the sea within 10 km of the farm. Tap a tile to show it on the map.</p></div>`;
}

// ---------------- tiny SVG charts ----------------
function spark(vals, color = "var(--accent)") {
  const v = vals.map((x) => (x == null ? null : +x));
  const ok = v.filter((x) => x != null);
  if (ok.length < 2) return `<svg class="spark" viewBox="0 0 100 26"></svg>`;
  const lo = Math.min(...ok), hi = Math.max(...ok), span = hi - lo || 1;
  let d = "", pen = false;
  v.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    const X = (i / (v.length - 1)) * 100, Y = 24 - ((x - lo) / span) * 22;
    d += `${pen ? "L" : "M"}${X.toFixed(1)},${Y.toFixed(1)}`; pen = true;
  });
  return `<svg class="spark" viewBox="0 0 100 26" preserveAspectRatio="none"><path d="${d}" stroke="${color}" vector-effect="non-scaling-stroke"/></svg>`;
}

function riskChart(Sr, today) {
  const n = Sr.date.length;
  if (n < 2) return "";
  const W = 320, H = 110, top = 6, bot = 92;
  const x = (i) => (i / (n - 1)) * W, y = (v) => bot - v * (bot - top);
  const line = (arr) => { let d = "", pen = false; arr.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; }); return d; };
  const bw = W / (n - 1);
  const bands = Sr.bloom.map((b, i) => (b ? `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${top}" width="${bw.toFixed(1)}" height="${bot - top}" fill="rgba(255,55,95,.3)" stroke="none"/>` : "")).join("");
  const ti = Sr.date.indexOf(today);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${[0, 0.5, 1].map((g) => `<line x1="0" x2="${W}" y1="${y(g)}" y2="${y(g)}" stroke="rgba(255,255,255,.07)" stroke-width="1"/>`).join("")}
    ${bands}
    <path d="${line(Sr.risk_7d)}" stroke="var(--medium)" vector-effect="non-scaling-stroke"/>
    <path d="${line(Sr.risk_3d)}" stroke="var(--accent)" vector-effect="non-scaling-stroke"/>
    ${ti >= 0 ? `<line x1="${x(ti)}" x2="${x(ti)}" y1="${top}" y2="${bot}" stroke="#fff" stroke-dasharray="3 3" stroke-width="1" vector-effect="non-scaling-stroke"/>` : ""}
    <text class="axis" x="0" y="${H - 2}">${fmtDay(Sr.date[0], { day: "numeric", month: "short" })}</text>
    <text class="axis" x="${W}" y="${H - 2}" text-anchor="end">${fmtDay(Sr.date[n - 1], { day: "numeric", month: "short" })}</text>
    <text class="axis" x="${W - 2}" y="${top + 8}" text-anchor="end">100%</text></svg></div>`;
}

// ---------------- time bar ----------------
function buildTimebar() {
  S.days = seasonDays(S.year);
  const sl = $("#slider");
  sl.max = S.days.length - 1;
  $("#year").textContent = S.year;
  const yrs = S.cfg.years;
  $("#yPrev").disabled = S.year <= yrs[0];
  $("#yNext").disabled = S.year >= yrs[1];
  $("#months").innerHTML = S.days.map((d, i) => (d.endsWith("-01") ? `<span style="left:${(i / (S.days.length - 1)) * 100}%">${fmtDay(d, { month: "short" })}</span>` : "")).join("");
  drawStrip();
}

function drawStrip() {
  const svg = $("#strip");
  const n = S.days.length;
  svg.setAttribute("viewBox", `0 0 ${n} 22`);
  if (!S.timeline || S.mode === "data") { svg.innerHTML = ""; return; }
  const by = Object.fromEntries(S.timeline.days.map((d) => [d.date, d]));
  const val = (d) => (S.mode === "past" ? (d?.bloom || 0) + (d?.milky || 0) : d?.high || 0);
  const mx = Math.max(1, ...S.days.map((d) => val(by[d])));
  const color = S.mode === "past" ? "#ff375f" : "#ff453a";
  svg.innerHTML = S.days.map((d, i) => { const v = val(by[d]); if (!v) return "";
    const h = 3 + (v / mx) * 17; return `<rect x="${i}" y="${20 - h}" width="0.8" height="${h}" fill="${color}" opacity=".85"/>`; }).join("");
}

async function setYear(y, keepMonthDay = true) {
  const yrs = S.cfg.years;
  y = Math.max(yrs[0], Math.min(yrs[1], y));
  const md = S.date ? S.date.slice(5) : "06-01";
  S.year = y;
  try { S.timeline = await once(`tl${y}`, () => getJSON(`data/timeline/${y}.json`)); } catch { S.timeline = null; }
  buildTimebar();
  await setDate(`${y}-${keepMonthDay ? md : "06-01"}`);
}

let dateToken = 0;
async function setDate(d) {
  if (!S.days.includes(d)) {
    const y = +d.slice(0, 4);
    if (y !== S.year && y >= S.cfg.years[0] && y <= S.cfg.years[1]) { S.date = d; return setYear(y); }
    d = d < S.days[0] ? S.days[0] : S.days[S.days.length - 1];
  }
  S.date = d;
  const tok = ++dateToken;
  $("#slider").value = S.days.indexOf(d);
  $("#dateBig").textContent = fmtDay(d, { weekday: "short", day: "numeric", month: "short" });
  $("#dateSub").textContent = d.slice(0, 4);
  updateOverlay();
  let day = {};
  try { day = await dayStatus(d); } catch { day = {}; }
  if (tok !== dateToken) return;
  S.prevDay = S.day; S.day = day;
  const by = S.timeline?.days.find((x) => x.date === d);
  if (by && S.mode === "past") $("#dateSub").textContent = `${d.slice(0, 4)} · ${by.bloom + by.milky} farms in a bloom`;
  if (by && S.mode === "forecast") $("#dateSub").textContent = `${d.slice(0, 4)} · ${by.high || 0} high-risk farms`;
  updateMarkers();
  renderLeft();
  if (S.farm) loadFarm();
  syncUrl();
}

function step(n) {
  const i = S.days.indexOf(S.date) + n;
  if (i >= 0 && i < S.days.length) setDate(S.days[i]);
  else if (S.playing) togglePlay();
}

function togglePlay() {
  if (S.playing) { clearInterval(S.playing); S.playing = null; document.body.classList.remove("playing"); return; }
  document.body.classList.add("playing");
  S.playing = setInterval(() => step(1), 450 / S.speed);
}

// ---------------- modes, layers, horizon ----------------
function setMode(m) {
  S.mode = m;
  document.body.dataset.mode = m;
  $$(".modes button").forEach((b) => b.setAttribute("aria-selected", b.dataset.mode === m));
  drawStrip();
  updateOverlay();
  updateMarkers();
  renderLeft();
  if (S.farmData) renderCard();
  setDate(S.date);
}

function setLayer(k) {
  S.layer = k;
  if (S.mode !== "data") return setMode("data");
  updateOverlay(); renderLeft();
  if (S.farmData) renderCard();
  syncUrl();
}

function setHorizon(h) {
  S.horizon = h;
  updateMarkers(); renderLeft(); renderLegend(null, true);
  if (S.farmData) renderCard();
}

// ---------------- cursor read-out ----------------
let hoverTimer = null, lastHover = "";
const dms = (v, pos, neg) => { const a = Math.abs(v), d = Math.floor(a), m = Math.round((a - d) * 60); return `${d}°${String(m).padStart(2, "0")}′${v >= 0 ? pos : neg}`; };
map.on("mousemove", (e) => {
  const { lat, lng } = e.latlng;
  $("#cursorPos").textContent = `${dms(lat, "N", "S")}  ${dms(lng, "E", "W")}`;
  const key = overlayKey();
  if (!key || !S.layerDates[key]?.has(S.date)) { $("#cursorVal").textContent = ""; return; }
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(async () => {
    const id = `${key}|${S.date}|${lat.toFixed(2)}|${lng.toFixed(2)}`;
    if (id === lastHover) return;
    lastHover = id;
    const v = pixelValue(key, lat, lng);
    const title = key === "bloom" ? "Chlorophyll" : S.cfg.layers[key].title;
    $("#cursorVal").textContent = v == null ? "" : `${title} ${FMT[key](v)}`;
  }, 90);
});
map.on("mouseout", () => { $("#cursorVal").textContent = ""; });

// ---------------- search ----------------
function initSearch() {
  const inp = $("#search"), ul = $("#searchResults");
  let active = 0, items = [];
  const render = () => {
    const q = inp.value.trim().toLowerCase();
    items = !q ? [] : S.farms.filter((f) => `${f.farm} ${f.company || ""} ${f.area}`.toLowerCase().includes(q)).slice(0, 8);
    ul.innerHTML = items.map((f, i) => `<li class="${i === active ? "active" : ""}" data-i="${i}"><span>${esc(f.farm)}</span><span>${esc(f.area)}</span></li>`).join("");
    ul.classList.toggle("open", items.length > 0);
    $$("li", ul).forEach((li) => li.onmousedown = (e) => { e.preventDefault(); pick(+li.dataset.i); });
  };
  const pick = (i) => { const f = items[i]; if (!f) return; inp.value = ""; render(); inp.blur(); openFarm(f.name, true); };
  inp.oninput = () => { active = 0; render(); };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); render(); e.preventDefault(); }
    if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
    if (e.key === "Enter") pick(active);
    if (e.key === "Escape") { inp.value = ""; render(); inp.blur(); }
  };
  inp.onblur = () => setTimeout(() => ul.classList.remove("open"), 100);
}

// ---------------- how it works ----------------
async function openHow() {
  const dlg = $("#how");
  if (!S.conf) { try { S.conf = await getJSON("data/confidence.json"); } catch { S.conf = {}; } }
  const R = S.cfg.rule, C = S.conf, V = C.validation || {};
  const [y0, y1] = S.cfg.years ? [1998, S.cfg.years[1]] : [1998, 2026];
  const nSea = S.farms.filter((f) => f.kind === "sea").length, nShell = S.farms.length - nSea;
  const docs = V.documented_events || [];
  const milky = docs.find((e) => e.kind === "coccolithophore"), spring = docs.find((e) => e.kind === "high-biomass");
  const ph = V.phenology_vs_literature, tox = V.hafro_toxic_vs_satellite;
  const data = [
    ["chl", "Chlorophyll from space", `Copernicus ocean colour, daily, ${y0}–${y1}. 4 km everywhere, 300 m in the fjords.`],
    ["sst", "Sea temperature", "OSTIA satellite analysis, daily."],
    ["mixed_layer", "Salinity, surface layer, currents", "GLORYS ocean model, daily."],
    ["waves", "Waves and weather", "Copernicus wave model; wind, sunlight, air temperature and rain from ERA5."],
    ["rrs555", "Milky water", "Green-light reflectance from the same satellites."],
    ["pin", "Farm locations", `MAST licence register: ${nSea} sea-pen and ${nShell} shellfish sites.`],
  ];
  const checks = [
    `Algae above <b>${R.min_chl} mg/m³</b>`,
    `At least <b>${R.ratio}×</b> the normal level for that place and date`,
    `Rarer than <b>${R.percentile}%</b> of days on that date (${R.baseline[0]}–${R.baseline[1]})`,
    `Covers <b>${Math.round(R.area * 100)}%</b> or more of the sea within ${R.radius_km} km of the farm`,
    `Lasts <b>3 days</b> or more`,
    `Seen by the satellite on at least one <b>clear day</b>`,
  ];
  const verified = [
    milky && [milky.result.startsWith("DETECTED"), "Westfjords milky-water bloom, September 2022",
      `Hafró sampled it on 5–7 Sep (4.7–6.3 million cells per litre). We found it at ${milky.detected_at.length} Arnarfjörður farms from ${fmtShort(milky.first_detection)}.`],
    spring && [spring.result.startsWith("DETECTED"), "Spring 2021 bloom linked to salmon losses",
      `MAST reported algae-related losses in spring 2021. We found a bloom at Arnarfjörður farms from ${fmtShort(spring.first_detection)}. The reports did not name the fjords.`],
    ph && [true, "Timing of the yearly spring bloom", `Our median peak is ${ph.median_peak}; research on Icelandic waters says it usually peaks in May.`],
    tox && [true, "What satellites cannot see", `Toxic algae found by Hafró did not show up as chlorophyll blooms (${pct(tox.share_toxic_samples_with_satellite_bloom)} vs ${pct(tox.share_clean_samples_with_satellite_bloom)}), so we don't claim to detect them.`],
  ].filter(Boolean);
  dlg.querySelector("#howSteps").innerHTML = `
    <div class="step"><div class="n">1</div><h2>The data we use</h2>
      <ul class="datalist">${data.map(([k, t, d]) => `<li>${icon(k)}<div><b>${t}</b><span>${d}</span></div></li>`).join("")}</ul>
      <p class="hint">Clouds hide the sea on about half the days, and the polar night blinds the satellites from November to February.</p></div>
    <div class="step"><div class="n">2</div><h2>What counts as a bloom</h2>
      <p>Chlorophyll is the green pigment in algae. A farm is in a bloom when <b>all six</b> are true:</p>
      <ol class="rulelist">${checks.map((c) => `<li><span>${c}</span></li>`).join("")}</ol>
      <p class="hint">Milky-water (coccolithophore) blooms are found separately: very bright green light, at least 3× normal, on 2 or more clear days.</p></div>
    <div class="step"><div class="n">3</div><h2>What we verified</h2>
      <ul class="vlist">${verified.map(([ok, t, d]) => `<li><span class="vmark ${ok ? "ok" : ""}">${ok ? "✓" : "–"}</span><div><b>${t}</b><span>${d}</span></div></li>`).join("")}</ul></div>`;
  dlg.showModal();
}
$("#howBtn").onclick = openHow;
$("#howClose").onclick = () => $("#how").close();
$("#how").addEventListener("click", (e) => { if (e.target.id === "how") e.target.close(); });

// ---------------- url ----------------
function syncUrl() {
  const u = new URL(location);
  u.searchParams.set("mode", S.mode);
  u.searchParams.set("date", S.date || "");
  if (S.mode === "data") u.searchParams.set("layer", S.layer); else u.searchParams.delete("layer");
  if (S.farm) u.searchParams.set("farm", S.farm); else u.searchParams.delete("farm");
  history.replaceState(null, "", u);
}

// ---------------- wiring ----------------
$$(".modes button").forEach((b) => b.onclick = () => setMode(b.dataset.mode));
$("#slider").oninput = (e) => setDate(S.days[+e.target.value]);
$("#prev").onclick = () => step(-1);
$("#next").onclick = () => step(1);
$("#play").onclick = togglePlay;
$("#speed").onclick = () => {
  S.speed = S.speed === 1 ? 2 : S.speed === 2 ? 4 : 1;
  $("#speed").textContent = `${S.speed}×`;
  if (S.playing) { togglePlay(); togglePlay(); }
};
$("#yPrev").onclick = () => setYear(S.year - 1);
$("#yNext").onclick = () => setYear(S.year + 1);
$("#zoomIn").onclick = () => map.zoomIn();
$("#zoomOut").onclick = () => map.zoomOut();
$("#home").onclick = () => map.flyToBounds(HOME, { duration: 0.8 });
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#search").focus(); return; }
  if (e.target.tagName === "INPUT" || $("#how").open) return;
  if (e.key === "ArrowRight") step(1);
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === " ") { e.preventDefault(); togglePlay(); }
  if (e.key === "Escape" && S.farm) closeFarm();
});

// ---------------- boot ----------------
(async function boot() {
  [S.cfg, S.farms] = await Promise.all([getJSON("data/config.json"), getJSON("data/farms.json")]);
  S.byName = Object.fromEntries(S.farms.map((f) => [f.name, f]));
  for (const [k, v] of Object.entries(S.cfg.layers)) S.layerDates[k] = expand(v.dates);
  for (const site of Object.values(S.cfg.chl300?.sites || {})) site.dateSet = expand(site.dates);
  const q = new URLSearchParams(location.search);
  if (["data", "past", "forecast"].includes(q.get("mode"))) S.mode = q.get("mode");
  if (S.cfg.layers[q.get("layer")]) S.layer = q.get("layer");
  if (q.get("view")) { const [la, lo, z] = q.get("view").split(",").map(Number); map.setView([la, lo], z); }
  else map.fitBounds(HOME);
  document.body.dataset.mode = S.mode;
  $$(".modes button").forEach((b) => b.setAttribute("aria-selected", b.dataset.mode === S.mode));
  drawFarms();
  initSearch();
  const want = q.get("date");
  const DEFAULT_YEAR = 2025;   // a year with many blooms: a good first view
  S.year = want ? +want.slice(0, 4) : Math.min(Math.max(DEFAULT_YEAR, S.cfg.years[0]), S.cfg.years[1]);
  try { S.timeline = await once(`tl${S.year}`, () => getJSON(`data/timeline/${S.year}.json`)); } catch { S.timeline = null; }
  buildTimebar();
  let start = want;
  if (!start || !S.days.includes(start)) {   // open on the busiest day of the latest year
    const best = (S.timeline?.days || []).slice().sort((a, b) => (b.high || 0) + b.bloom - ((a.high || 0) + a.bloom))[0];
    start = best?.date || `${S.year}-06-01`;
  }
  await setDate(start);
  if (q.get("farm") && S.byName[q.get("farm")]) openFarm(q.get("farm"));
  if (q.get("how")) openHow();
  if (["sea", "shellfish"].includes(q.get("kind"))) setKind(q.get("kind"));
})();
