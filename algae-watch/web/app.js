// AlgaeWatch Iceland: algal blooms at Iceland's licensed fish and shellfish farms.
// Modes: Ocean data (one input on the map) · Past blooms (detected blooms, farms pulse when hit)
//        · Forecast (bloom risk per farm, why, and how sure we are).
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const api = (path) => fetch(path).then((r) => { if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json(); });

const S = {
  mode: "forecast", layer: "chl", horizon: 3, fjord: true,
  cfg: null, farms: [], byName: {}, year: null, date: null, days: [],
  day: {}, prevDay: {}, timeline: null, farm: null, farmData: null, conf: null,
  playing: null, speed: 1, layerDates: {},
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
};
const icon = (k, cls = "") => `<svg viewBox="0 0 24 24" class="${cls}">${ICONS[k] || ""}</svg>`;
const FMT = {
  chl: (v) => `${v.toFixed(v < 1 ? 2 : 1)} mg/m³`, chl_obs: (v) => `${v.toFixed(v < 1 ? 2 : 1)} mg/m³`,
  anomaly: (v) => `${v.toFixed(1)}× normal`, bloom: (v) => `${v.toFixed(1)}× normal`, sst: (v) => `${v.toFixed(1)} °C`,
  salinity: (v) => `${v.toFixed(2)} PSU`, mixed_layer: (v) => `${Math.round(v)} m deep`, currents: (v) => `${v.toFixed(2)} m/s`,
  waves: (v) => `${v.toFixed(1)} m`, rrs555: (v) => `${v.toFixed(4)} sr⁻¹${v >= 0.008 ? " · milky" : ""}`,
};
const LEVEL_COLOR = { low: "var(--low)", medium: "var(--medium)", high: "var(--high)" };

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
    const url = `/tiles/${key}/${S.date}.png`;
    if (!overlay) overlay = L.imageOverlay(url, L_.bounds, { opacity: 0.95, interactive: false }).addTo(map);
    else { overlay.setBounds(L.latLngBounds(L_.bounds)); overlay.setUrl(url); }
    overlay.setOpacity(0.95);
  } else if (overlay) overlay.setOpacity(0);
  // 300 m fjord detail on top of chlorophyll
  const F = S.cfg.chl300;
  const showFjord = S.mode === "data" && S.fjord && (key === "chl" || key === "chl_obs");
  for (const [slug, info] of Object.entries(F?.sites || {})) {
    const ok = showFjord && info.dates.includes(S.date);
    const u = `/tiles/chl300/${slug}/${S.date}.png`;
    if (ok) {
      if (!fjordOverlays[slug]) fjordOverlays[slug] = L.imageOverlay(u, info.bounds, { interactive: false }).addTo(map);
      else fjordOverlays[slug].setUrl(u).setOpacity(1);
    } else if (fjordOverlays[slug]) fjordOverlays[slug].setOpacity(0);
  }
  renderLegend(key, has);
  preloadNext(key);
}

function preloadNext(key) {
  if (!key) return;
  const i = S.days.indexOf(S.date);
  for (const d of S.days.slice(i + 1, i + 4)) if (S.layerDates[key]?.has(d)) new Image().src = `/tiles/${key}/${d}.png`;
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
  return `<b>${esc(f.farm)}</b><br><span class="m">${esc(f.company || "")} · ${esc(f.area)}</span>${line ? `<br>${line}` : ""}`;
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
  const sw = $("#fjordSwitch", box);
  if (sw) sw.onclick = () => { S.fjord = !S.fjord; sw.setAttribute("aria-checked", S.fjord); updateOverlay(); };
}

function renderLeftPast(box) {
  const hit = S.farms.filter((f) => S.day[f.name]?.bloom || S.day[f.name]?.milky);
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
    <div class="hero"><span class="num" style="color:${hit.length ? "var(--bloom)" : "var(--text)"}">${hit.length}</span>
      <span class="lab">farm${hit.length === 1 ? "" : "s"} in a bloom<br>on ${fmtShort(S.date)}</span></div>
    ${hit.length ? `<div class="list">${hit.map(row).join("")}</div>`
      : `<div class="empty-note">No farm is inside a bloom on this day.${busiest.length ? " Try one of the busiest days below, or press play." : ""}</div>`}
    ${busiest.length ? `<div class="section-title">Busiest days in ${S.year}</div>
      <div class="list">${busiest.map((d) => `<button class="row" data-jump="${d.date}"><span class="t">${fmtShort(d.date)}</span>
        <span class="v" style="color:var(--bloom)">${d.bloom + d.milky} farms</span></button>`).join("")}</div>` : `<div class="empty-note">No blooms at farms in ${S.year}.</div>`}
    <button class="linkbtn" data-how>How do we decide it's a bloom? →</button>`;
  bindLeft(box);
}

function renderLeftForecast(box) {
  const h = S.horizon;
  const rows = S.farms.map((f) => ({ f, d: S.day[f.name] || {} })).filter((x) => x.d[`risk_${h}d`] != null)
    .sort((a, b) => b.d[`risk_${h}d`] - a.d[`risk_${h}d`]);
  const nHigh = rows.filter((x) => x.d[`level_${h}d`] === "high").length;
  const nMed = rows.filter((x) => x.d[`level_${h}d`] === "medium").length;
  box.innerHTML = `
    <h2>Forecast</h2>
    <p class="sub">Chance of a bloom at each farm, as issued on ${fmtShort(S.date)}.</p>
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
      : `<div class="empty-note">${+S.date.slice(5, 7) >= 11 || +S.date.slice(5, 7) <= 2 ? "No forecast in winter: satellites can't see the sea in the polar night." : "No forecast for this day."}</div>`}
    <button class="linkbtn" data-how>How sure are we? →</button>`;
  bindLeft(box);
}

function bindLeft(box) {
  $$("[data-farm]", box).forEach((b) => b.onclick = () => openFarm(b.dataset.farm, true));
  $$("[data-jump]", box).forEach((b) => b.onclick = () => setDate(b.dataset.jump));
  $$("[data-h]", box).forEach((b) => b.onclick = () => setHorizon(+b.dataset.h));
  $$("[data-how]", box).forEach((b) => b.onclick = openHow);
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
  try { S.farmData = await api(`/api/farm?name=${encodeURIComponent(S.farm)}&date=${S.date}`); }
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
  box.innerHTML = head + body + toxicNote(D);
  box.hidden = false;
  $("#cardClose").onclick = closeFarm;
  $$("[data-jump]", box).forEach((b) => b.onclick = () => setDate(b.dataset.jump));
  $$("[data-h]", box).forEach((b) => b.onclick = () => setHorizon(+b.dataset.h));
  $$("[data-maplayer]", box).forEach((b) => b.onclick = () => setLayer(b.dataset.maplayer));
  $$("[data-how]", box).forEach((b) => b.onclick = openHow);
}

function toxicNote(D) {
  if (!D.toxic) return "";
  return `<div class="note"><b>Toxic algae can't be seen from space.</b> In ${fmtDay(D.date, { month: "long" })},
    ${pct(D.toxic.month_share)} of Hafró's shellfish-area samples (2005–2019) had toxic algae above the safe limit. Check MAST before harvesting.</div>`;
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
  const h = S.horizon, X = F[`${h}d`], c = X.confidence;
  const sure = c ? `When we say <b>${c.range}</b>, a bloom followed <b>${pct(c.observed)}</b> of the time
    <span class="muted">(${c.n.toLocaleString("en")} past forecasts, ${c.years})</span>.` : "";
  const happened = X.happened == null ? "" : `<div class="happened">${X.happened ? `<span style="color:var(--bloom)">●</span> <span>What really happened: <b>a bloom came</b> within ${h} days.</span>`
    : `<span style="color:var(--low)">●</span> <span>What really happened: <b>no bloom</b> within ${h} days.</span>`}</div>`;
  return `
    <div class="gauges">${gauge(3, F["3d"])}${gauge(7, F["7d"])}</div>
    <div class="block"><h3>How sure are we?</h3><div class="sure">${sure || "Not enough past forecasts at this level."}</div></div>
    <div class="block"><h3>Why <span class="muted">what pushed the ${h}-day risk up</span></h3>
      <ul class="why">${X.factors.length ? X.factors.slice(0, 4).map((x) => `<li><i></i><span>${esc(x.text)}</span></li>`).join("")
        : "<li><i style='background:var(--low)'></i><span>Nothing unusual: conditions look like a normal day for this farm.</span></li>"}</ul>
      ${happened}</div>
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
  try { S.timeline = await api(`/api/timeline?year=${y}`); } catch { S.timeline = null; }
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
  try { day = await api(`/api/day?date=${d}`); } catch { day = {}; }
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
    try {
      const r = await api(`/api/value?layer=${key}&date=${S.date}&lat=${lat.toFixed(3)}&lon=${lng.toFixed(3)}`);
      if (id !== lastHover) return;
      const title = key === "bloom" ? "Chlorophyll" : S.cfg.layers[key].title;
      $("#cursorVal").textContent = r.value == null ? "" : `${title} ${FMT[key](r.value)}`;
    } catch { $("#cursorVal").textContent = ""; }
  }, 90);
});
map.on("mouseout", () => { $("#cursorVal").textContent = ""; });

// ---------------- search ----------------
function initSearch() {
  const inp = $("#search"), ul = $("#searchResults");
  let active = 0, items = [];
  const render = () => {
    const q = inp.value.trim().toLowerCase();
    items = !q ? [] : S.farms.filter((f) => `${f.farm} ${f.company} ${f.area}`.toLowerCase().includes(q)).slice(0, 8);
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
  if (!S.conf) { try { S.conf = await api("/api/confidence"); } catch { S.conf = {}; } }
  const R = S.cfg.rule, C = S.conf, cal = C.calibration?.["3d"], B = C.bloom?.["3d"];
  const doc = (C.validation?.documented_events || []).find((e) => e.kind === "coccolithophore");
  dlg.querySelector("#howSteps").innerHTML = `
    <div class="step"><div class="n">1</div><h2>We watch the sea from space</h2>
      <p>Every day, Copernicus satellites measure <b>chlorophyll</b>, the green pigment in algae. More chlorophyll means more algae.</p>
      <ul><li>Daily since 1998, at ${S.farms.length} licensed farms (MAST register)</li>
      <li>Plus sea temperature, salinity, the surface layer, currents, waves and weather</li>
      <li>Clouds hide the sea about half the days, and the polar night all winter</li></ul>
      <div class="stat"><b>±40%</b><span>typical error of one satellite chlorophyll value, so we compare each place with its own normal</span></div></div>
    <div class="step"><div class="n">2</div><h2>We call it a bloom when…</h2>
      <ul><li>algae are above <b>${R.min_chl} mg/m³</b></li>
      <li>at least <b>${R.ratio}×</b> the normal level for that place and date</li>
      <li>rarer than <b>${R.percentile}%</b> of days on that date (${R.baseline[0]}–${R.baseline[1]})</li>
      <li>covering <b>≥${Math.round(R.area * 100)}%</b> of the sea within ${R.radius_km} km of the farm</li>
      <li>lasting <b>3+ days</b>, and seen on at least one <b>clear day</b></li></ul>
      <p style="margin-top:8px">Milky-water (coccolithophore) blooms are found separately, from very bright green light.</p>
      ${doc ? `<div class="stat"><b>✓ Verified</b><span>${esc(doc.event.split("(")[0].trim())}: found from ${fmtShort(doc.first_detection)}, before Hafró's first water sample</span></div>` : ""}</div>
    <div class="step"><div class="n">3</div><h2>We forecast 3–7 days ahead</h2>
      <p>A model learned from 1998–2018 which conditions come before a bloom: recent clear-sky chlorophyll, how unusual it is, the season, sea temperature and the surface layer.</p>
      ${cal ? `<div class="stat"><b>${pct(cal.high_precision)}</b><span>of "high risk" forecasts were followed by a bloom within 3 days (${cal.years}, out of sample)</span></div>
      <div class="stat"><b>${pct(cal.caught_medium_or_high)}</b><span>of blooms got at least a medium-risk warning 3 days before</span></div>` : ""}
      ${B ? `<p style="margin-top:10px">Tested on ${B.test_years}, years it never saw: much better than "a bloom will continue" (ROC-AUC ${B.model.roc_auc.toFixed(2)} vs ${B.baseline_persistence.roc_auc.toFixed(2)}) or the usual rate for the season (${B.baseline_seasonal.roc_auc.toFixed(2)}).</p>` : ""}</div>`;
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
  [S.cfg, S.farms] = await Promise.all([api("/api/config"), api("/api/farms")]);
  S.byName = Object.fromEntries(S.farms.map((f) => [f.name, f]));
  for (const [k, v] of Object.entries(S.cfg.layers)) S.layerDates[k] = new Set(v.dates);
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
  S.year = want ? +want.slice(0, 4) : S.cfg.years[1];
  try { S.timeline = await api(`/api/timeline?year=${S.year}`); } catch { S.timeline = null; }
  buildTimebar();
  let start = want;
  if (!start || !S.days.includes(start)) {   // open on the busiest day of the latest year
    const best = (S.timeline?.days || []).slice().sort((a, b) => (b.high || 0) + b.bloom - ((a.high || 0) + a.bloom))[0];
    start = best?.date || `${S.year}-06-01`;
  }
  await setDate(start);
  if (q.get("farm") && S.byName[q.get("farm")]) openFarm(q.get("farm"));
  if (q.get("how")) openHow();
})();
