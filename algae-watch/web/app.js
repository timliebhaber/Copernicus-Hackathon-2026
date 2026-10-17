// AlgaeWatch Iceland: data explorer (Step 2) + blooms (Step 3)
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const $ = (id) => document.getElementById(id);

const state = {
  manifest: null, sites: [], dates: [], idx: 0, layer: "chl",
  site: null, series: null, charts: [], playing: null, snapshot: {}, window: "season", windowRange: [0, 0],
  tab: "data", events: [], phenology: [], calendar: null,
};

// ---------- map ----------
const map = L.map("map", { zoomControl: true, attributionControl: true }).setView([65.0, -19.0], 6);
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const esri = (n) => `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${n}/MapServer/tile/{z}/{y}/{x}`;
L.tileLayer(esri(dark ? "World_Dark_Gray_Base" : "World_Light_Gray_Base"), {
  maxZoom: 16, attribution: "Tiles &copy; Esri | Data: Copernicus Marine, Open-Meteo",
}).addTo(map);
L.tileLayer(esri(dark ? "World_Dark_Gray_Reference" : "World_Light_Gray_Reference"), { maxZoom: 16, pane: "shadowPane" }).addTo(map);

let regionOverlay = null;
const fjordOverlays = {};
const markers = {};

function allDates() {
  const out = [];
  const [y0, y1] = state.manifest.years || [2023, 2024];
  const d = new Date(Date.UTC(y0, 0, 1));
  const end = new Date(Date.UTC(y1, 11, 31));
  for (; d <= end; d.setUTCDate(d.getUTCDate() + 1)) out.push(d.toISOString().slice(0, 10));
  return out;
}

function currentDate() { return state.dates[state.idx]; }

function fmtDate(s) {
  return new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

// ---------- layers ----------
function buildLayerSelect() {
  const sel = $("layerSelect");
  const order = state.tab === "blooms" ? ["bloom", "anomaly", "chl"] : ["chl", "chl_obs", "sst", "rrs555"];
  if (!order.includes(state.layer)) state.layer = order[0];
  sel.innerHTML = order.filter((k) => state.manifest.layers[k])
    .map((k) => `<option value="${k}">${state.manifest.layers[k].label}</option>`).join("");
  sel.value = state.layer;
  sel.onchange = () => { state.layer = sel.value; renderLegend(); updateMap(); };
  $("fjordToggle").onchange = updateMap;
}

function renderLegend() {
  const L_ = state.manifest.layers[state.layer];
  const pos = (v) => L_.log
    ? (Math.log(v) - Math.log(L_.min)) / (Math.log(L_.max) - Math.log(L_.min))
    : (v - L_.min) / (L_.max - L_.min);
  const grad = L_.stops.map((s, i) => `${s.c} ${(i / (L_.stops.length - 1)) * 100}%`).join(",");
  $("legend").innerHTML = `
    <div class="title">${L_.label} <span class="muted">(${L_.units})</span></div>
    <div class="bar" style="background:linear-gradient(90deg,${grad})"></div>
    <div class="ticks">${L_.ticks.map((t) => `<span style="left:${pos(t) * 100}%">${t}</span>`).join("")}</div>`;
}

function updateMap() {
  const d = currentDate();
  const L_ = state.manifest.layers[state.layer];
  const has = L_.dates.includes(d);
  const url = `/tiles/${state.layer}/${d}.png`;
  if (has) {
    if (!regionOverlay) regionOverlay = L.imageOverlay(url, L_.bounds, { opacity: 1 }).addTo(map);
    else { regionOverlay.setBounds(L.latLngBounds(L_.bounds)); regionOverlay.setUrl(url); }
    regionOverlay.setOpacity(1);
  } else if (regionOverlay) regionOverlay.setOpacity(0);

  const month = +d.slice(5, 7);
  const note = $("layerNote");
  if (!has) {
    note.className = "note warn";
    note.textContent = (month >= 11 || month <= 1) && state.layer !== "sst"
      ? "No ocean-colour data: polar night (Nov–Jan), satellites can't see the sea."
      : "No data for this day.";
  } else {
    note.className = "note";
    note.textContent = state.layer === "chl_obs" || state.layer === "rrs555"
      ? "Observed pixels only. Gaps are clouds."
      : state.layer === "bloom" ? "Coloured pixels are blooms. Empty sea means no bloom (data present)." : "";
  }

  // 300 m fjord detail on top (chlorophyll layers only)
  const showFjord = $("fjordToggle").checked && state.layer.startsWith("chl");
  refreshMarkers();
  const F = state.manifest.layers.chl300;
  for (const [slug, info] of Object.entries(F ? F.sites : {})) {
    const ok = showFjord && info.dates.includes(d);
    const u = `/tiles/chl300/${slug}/${d}.png`;
    if (ok) {
      if (!fjordOverlays[slug]) fjordOverlays[slug] = L.imageOverlay(u, info.bounds, { opacity: 1 }).addTo(map);
      else fjordOverlays[slug].setUrl(u).setOpacity(1);
    } else if (fjordOverlays[slug]) fjordOverlays[slug].setOpacity(0);
  }
  preload(state.idx + 1);
}

function preload(i) {
  const d = state.dates[i];
  if (!d) return;
  if (state.manifest.layers[state.layer].dates.includes(d)) new Image().src = `/tiles/${state.layer}/${d}.png`;
}

// ---------- sites ----------
const LABEL_DIR = { "Reyðarfjörður": "left", "Fáskrúðsfjörður": "right", "Dýrafjörður": "top", "Patreksfjörður": "left" };
function drawSites() {
  for (const s of state.sites) {
    const color = s.kind === "salmon" ? css("--series-1") : css("--series-2");
    const m = L.circleMarker([s.lat, s.lon], {
      radius: 8, color: css("--marker-ring"), weight: 2, fillColor: color, fillOpacity: 1,
    }).addTo(map);
    const dir = LABEL_DIR[s.name] || "right";
    const off = { right: [8, 0], left: [-8, 0], bottom: [0, 8], top: [0, -8] }[dir];
    m.bindTooltip(s.name, { permanent: true, direction: dir, offset: off, className: "sitelabel" });
    m.on("click", () => openSite(s));
    m.on("mouseover", () => {
      const v = state.snapshot[s.name] || {};
      const f = (x, d = 2) => (x == null ? "–" : x.toFixed(d));
      m.unbindPopup();
      const status = v.is_bloom ? `<b style="color:${css("--critical")}">● Bloom detected</b> (${f(v.chl_anomaly_ratio, 1)}× normal)<br>` : "";
      m.bindPopup(`<b>${s.name}</b> · ${fmtDate(currentDate())}<br>${status}
        Chlorophyll: ${f(v.chl)} mg/m³ (300 m: ${f(v.chl_300m)})<br>
        Sea temp: ${f(v.sst, 1)} °C`, { closeButton: false, autoPan: false }).openPopup();
    });
    m.on("mouseout", () => m.closePopup());
    markers[s.name] = m;
  }
}

function refreshMarkers() {
  for (const s of state.sites) {
    const m = markers[s.name];
    if (!m) continue;
    const bloom = state.tab === "blooms" && state.snapshot[s.name]?.is_bloom;
    const base = s.kind === "salmon" ? css("--series-1") : css("--series-2");
    m.setStyle({ fillColor: bloom ? css("--critical") : base, radius: bloom ? 11 : 8 });
    m.setTooltipContent(bloom ? `${s.name} · bloom` : s.name);
  }
}

async function loadSnapshot() {
  const d = currentDate();
  try { state.snapshot = await (await fetch(`/api/snapshot?date=${d}`)).json(); } catch { state.snapshot = {}; }
  refreshMarkers();
}

// ---------- panel ----------
async function openSite(s) {
  state.site = s;
  $("panelEmpty").hidden = true;
  $("panelSite").hidden = false;
  $("siteName").textContent = s.name;
  $("siteMeta").textContent = `${s.kind === "salmon" ? "Salmon farming fjord" : "Shellfish area"} · ${s.lat.toFixed(2)}°N ${Math.abs(s.lon).toFixed(2)}°W · approximate placeholder`;
  const q = encodeURIComponent(s.name);
  [state.series, state.events, state.phenology] = await Promise.all([
    fetch(`/api/timeseries?site=${q}`).then((r) => r.json()),
    fetch(`/api/events?site=${q}`).then((r) => r.json()),
    fetch(`/api/phenology?site=${q}`).then((r) => r.json()),
  ]);
  renderPanel();
}

function renderPanel() {
  if (!state.site) { renderOverview(); return; }
  renderCharts();
  renderStats();
  if (state.tab === "blooms") { renderSiteCalendar(); renderEvents(); renderPhenology(); }
}

document.querySelectorAll("[data-window]").forEach((b) => b.onclick = () => {
  state.window = b.dataset.window;
  document.querySelectorAll("[data-window]").forEach((x) => x.classList.toggle("on", x === b));
  if (state.series) renderCharts();
});

$("closePanel").onclick = () => {
  state.site = null; $("panelEmpty").hidden = false; $("panelSite").hidden = true; renderOverview();
};

function renderStats() {
  if (!state.series) return;
  const i = state.series.date.indexOf(currentDate());
  const g = (k, d = 2) => { const v = i >= 0 && state.series[k] ? state.series[k][i] : null; return v == null ? "–" : v.toFixed(d); };
  if (state.tab === "blooms") {
    const bloom = i >= 0 && state.series.is_bloom?.[i] === 1;
    const seen = i >= 0 && state.series.observed_bloom?.[i] === 1;
    $("stats").innerHTML = `
      <div class="stat"><div class="k">Status ${fmtDate(currentDate())}</div>
        <div class="v ${bloom ? "crit" : ""}">${bloom ? "● Bloom" : g("chl") === "–" ? "No data" : "Normal"}</div>
        <div class="u">${bloom ? (seen ? "confirmed by observation" : "gap-filled data only") : "&nbsp;"}</div></div>
      <div class="stat"><div class="k">vs normal</div><div class="v">${g("chl_anomaly_ratio", 1)}×</div><div class="u">normal ${g("chl_clim")} mg/m³</div></div>
      <div class="stat"><div class="k">Bloom area</div><div class="v">${i >= 0 && state.series.bloom_fraction?.[i] != null ? Math.round(state.series.bloom_fraction[i] * 100) : "–"}%</div><div class="u">of pixels within 10 km</div></div>`;
    return;
  }
  $("stats").innerHTML = `
    <div class="stat"><div class="k">Chlorophyll</div><div class="v">${g("chl")}</div><div class="u">mg/m³ · gap-free</div></div>
    <div class="stat"><div class="k">Fjord 300 m</div><div class="v">${g("chl_300m")}</div><div class="u">mg/m³ · if cloud-free</div></div>
    <div class="stat"><div class="k">Sea temp</div><div class="v">${g("sst", 1)}</div><div class="u">°C</div></div>`;
}

const CHARTS = [
  { title: "Chlorophyll-a", sub: "mg/m³, log scale", log: true, series: [
    { key: "chl", name: "Gap-free 4 km", type: "line", c: "--series-1" },
    { key: "chl_300m", name: "Fjord 300 m (cloud-free days)", type: "dot", c: "--series-2" },
    { key: "chl_obs", name: "Observed 4 km", type: "dot", c: "--series-3" },
  ]},
  { title: "Algae groups (satellite estimate)", sub: "chlorophyll from each group, mg/m³, cloud-free days only", log: true, series: [
    { key: "diatoms", name: "Diatoms", type: "dot", c: "--series-1" },
    { key: "haptophytes", name: "Haptophytes (incl. Chrysochromulina, coccolithophores)", type: "dot", c: "--series-2" },
    { key: "dinoflagellates", name: "Dinoflagellates", type: "dot", c: "--series-3" },
  ]},
  { title: "Milky water (reflectance 555 nm)", sub: "sr⁻¹; high values suggest coccolithophore blooms", series: [
    { key: "rrs555", name: "RRS 555", type: "dot", c: "--series-1" }]},
  { title: "Sea surface temperature", sub: "°C", series: [{ key: "sst", name: "SST", type: "line", c: "--series-1" }] },
  { title: "Mixed layer depth", sub: "m; shallow layer = stratified water, favours blooms", reverse: true, series: [
    { key: "mixed_layer", name: "Mixed layer", type: "line", c: "--series-1" }]},
  { title: "Sunlight", sub: "shortwave radiation, MJ/m² per day", series: [
    { key: "shortwave_radiation_sum", name: "Radiation", type: "line", c: "--series-1" }]},
  { title: "Wind speed", sub: "10 m daily mean, m/s", series: [
    { key: "wind_speed_10m_mean", name: "Wind", type: "line", c: "--series-1" }]},
];

const BLOOM_CHARTS = [
  { title: "Chlorophyll vs normal", sub: "mg/m³, log scale; red bands = detected bloom days", log: true, bands: true, series: [
    { key: "chl", name: "Chlorophyll (gap-free)", type: "line", c: "--series-1" },
    { key: "chl_clim", name: "Normal for the date (multi-year median)", type: "line", c: "--muted", dash: true },
    { key: "chl_300m", name: "Fjord 300 m observations", type: "dot", c: "--series-2" },
  ]},
  { title: "Algae groups (satellite estimate)", sub: "what kind of algae is blooming; cloud-free days only", log: true, series: [
    { key: "diatoms", name: "Diatoms", type: "dot", c: "--series-1" },
    { key: "haptophytes", name: "Haptophytes (incl. Chrysochromulina, coccolithophores)", type: "dot", c: "--series-2" },
    { key: "dinoflagellates", name: "Dinoflagellates", type: "dot", c: "--series-3" },
  ]},
  { title: "Sea surface temperature", sub: "°C (OSTIA satellite)", series: [{ key: "sst", name: "SST", type: "line", c: "--series-1" }] },
  { title: "Mixed layer depth", sub: "m; shallow = stratified water (key bloom driver)", reverse: true, series: [
    { key: "mixed_layer", name: "Mixed layer", type: "line", c: "--series-1" }]},
];

const bloomBands = {
  id: "bloomBands",
  beforeDatasetsDraw(chart, args, opts) {
    const flags = chart.options.plugins.bloomBands?.flags;
    if (!flags) return;
    const { top, bottom } = chart.chartArea;
    const x = chart.scales.x;
    const w = Math.max(1, (x.getPixelForValue(1) - x.getPixelForValue(0)));
    const ctx = chart.ctx;
    ctx.save(); ctx.fillStyle = css("--critical-wash");
    flags.forEach((f, i) => { if (f === 1) ctx.fillRect(x.getPixelForValue(i) - w / 2, top, w, bottom - top); });
    ctx.restore();
  },
};

const dateLine = {
  id: "dateLine",
  afterDatasetsDraw(chart) {
    const i = chart.data.labels.indexOf(currentDate());
    if (i < 0) return;
    const x = chart.scales.x.getPixelForValue(i);
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save(); ctx.strokeStyle = css("--text"); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
  },
};

function renderCharts() {
  state.charts.forEach((c) => c.destroy());
  state.charts = [];
  const box = $("charts");
  box.innerHTML = "";
  const S = state.series;
  let lo = 0, hi = S.date.length;
  if (state.window !== "all") {
    const c = Math.max(0, S.date.indexOf(currentDate()));
    lo = Math.max(0, c - 90); hi = Math.min(S.date.length, lo + 181); lo = Math.max(0, hi - 181);
  }
  state.windowRange = [lo, hi];
  const labels = S.date.slice(lo, hi);
  for (const spec of state.tab === "blooms" ? BLOOM_CHARTS : CHARTS) {
    const div = document.createElement("div");
    div.className = "chartbox";
    const legend = spec.series.length > 1
      ? `<div class="lgd">${spec.series.map((s) => `<span><i style="background:${css(s.c)}"></i>${s.name}</span>`).join("")}</div>` : "";
    div.innerHTML = `<h3>${spec.title}</h3><div class="sub">${spec.sub}</div>${legend}<div class="canvas"><canvas></canvas></div>`;
    box.appendChild(div);
    const datasets = spec.series.map((s) => ({
      label: s.name,
      data: S[s.key].slice(lo, hi).map((v) => (spec.log && v != null && v <= 0 ? null : v)),
      borderColor: css(s.c), backgroundColor: css(s.c),
      borderWidth: s.type === "line" ? 2 : 0, borderDash: s.dash ? [4, 3] : [],
      pointRadius: s.type === "dot" ? 2 : 0, pointHoverRadius: 5,
      showLine: s.type === "line", spanGaps: false, tension: 0,
    }));
    const chart = new Chart(div.querySelector("canvas"), {
      type: "line",
      data: { labels, datasets },
      plugins: [bloomBands, dateLine],
      options: {
        animation: false, maintainAspectRatio: false, responsive: true,
        interaction: { mode: "index", intersect: false },
        onClick: (e, els, ch) => {
          const i = ch.scales.x.getValueForPixel(e.x);
          if (i != null && i >= 0) setIdx(state.dates.indexOf(labels[Math.round(i)]));
        },
        plugins: {
          bloomBands: { flags: spec.bands && S.is_bloom ? S.is_bloom.slice(lo, hi) : null },
          legend: { display: false },
          tooltip: {
            backgroundColor: css("--surface"), titleColor: css("--text"), bodyColor: css("--text-2"),
            borderColor: css("--axis"), borderWidth: 1, filter: (it) => it.raw != null,
            callbacks: { title: (it) => fmtDate(it[0].label) },
          },
        },
        scales: {
          x: {
            grid: { display: false }, border: { color: css("--axis") },
            ticks: {
              color: css("--muted"), autoSkip: false, maxRotation: 0, font: { size: 10 },
              callback(v) {
                const d = labels[v];
                const months = state.window === "all" ? ["01", "04", "07", "10"] : null;
                if (d.endsWith("-01") && (!months || months.includes(d.slice(5, 7))))
                  return new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { month: "short", ...(state.window === "all" || d.slice(5, 7) === "01" ? { year: "2-digit" } : {}), timeZone: "UTC" });
                return null;
              },
            },
          },
          y: {
            type: spec.log ? "logarithmic" : "linear", reverse: !!spec.reverse,
            grid: { color: css("--grid") }, border: { display: false },
            ticks: { color: css("--muted"), font: { size: 10 }, maxTicksLimit: 4 },
          },
        },
      },
    });
    state.charts.push(chart);
  }
}

// ---------- time controls ----------
async function setIdx(i) {
  state.idx = Math.max(0, Math.min(state.dates.length - 1, i));
  $("dateSlider").value = state.idx;
  $("datePicker").value = currentDate();
  updateMap();
  if (state.series && state.window !== "all") {
    const [lo, hi] = state.windowRange;
    const c = state.series.date.indexOf(currentDate());
    if (c < lo + 15 || c > hi - 15) renderCharts(); else state.charts.forEach((ch) => ch.draw());
  } else state.charts.forEach((c) => c.draw());
  await loadSnapshot();
  renderStats();
}

// ---------- tabs ----------
function setTab(tab, keepLayer = false) {
  state.tab = tab;
  if (!keepLayer) state.layer = tab === "blooms" ? "bloom" : "chl";
  document.querySelectorAll(".tab[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  buildLayerSelect();
  renderLegend();
  updateMap();
  $("panel").dataset.tab = tab;
  if (state.site) renderPanel(); else renderOverview();
  const u = new URL(location); u.searchParams.set("tab", tab); history.replaceState(null, "", u);
}
document.querySelectorAll(".tab[data-tab]").forEach((b) => b.onclick = () => setTab(b.dataset.tab));
const shortDate = (s) => new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit", timeZone: "UTC" });

const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

function heat(v, max) {
  if (!v) return "transparent";
  const t = Math.min(1, Math.sqrt(v / max));
  return `color-mix(in oklab, var(--critical) ${Math.round(20 + 80 * t)}%, var(--surface))`;
}

function jumpTo(dateStr) {
  let i = state.dates.indexOf(dateStr);
  if (i < 0) { alert(`Map layers cover ${state.dates[0].slice(0, 4)}–${state.dates.at(-1).slice(0, 4)} only.`); return; }
  setIdx(i);
}

async function renderOverview() {
  if (state.tab !== "blooms") { $("overview").innerHTML = ""; return; }
  if (!state.calendar) state.calendar = await (await fetch("/api/calendar")).json();
  document.querySelectorAll(".baselineYears").forEach((el) => el.textContent = state.calendar.baseline.join("–"));
  const [y0, y1] = state.calendar.years;
  const years = []; for (let y = y0; y <= y1; y++) years.push(y);
  const by = {}; let max = 1;
  for (const r of state.calendar.by_year) { by[`${r.site}|${r.year}`] = r.days; max = Math.max(max, r.days); }
  const rows = state.sites.map((s) => `<tr><th>${s.name}</th>${years.map((y) => {
    const v = by[`${s.name}|${y}`] || 0;
    return `<td title="${s.name} ${y}: ${v} bloom days" style="background:${heat(v, max)}">${v || ""}</td>`; }).join("")}</tr>`).join("");
  $("overview").innerHTML = `
    <h3>Bloom days per year, ${y0}–${y1}</h3>
    <div class="sub">Days a site had a detected bloom (≥2.5× normal and ≥3 mg/m³ on ≥10% of nearby sea). Darker = more days.</div>
    <div class="heatwrap"><table class="heat years"><thead><tr><th></th>${years.map((y) => `<th>${String(y).slice(2)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderSiteCalendar() {
  const years = [...new Set(state.series.date.map((d) => +d.slice(0, 4)))];
  const cnt = {}; let max = 1;
  state.series.date.forEach((d, i) => {
    if (state.series.is_bloom[i] === 1) { const k = d.slice(0, 7); cnt[k] = (cnt[k] || 0) + 1; max = Math.max(max, cnt[k]); }
  });
  const rows = years.map((y) => `<tr><th>${y}</th>${MONTHS.map((m, j) => {
    const k = `${y}-${String(j + 1).padStart(2, "0")}`; const v = cnt[k] || 0;
    return `<td data-jump="${k}-15" title="${k}: ${v} bloom days" style="background:${heat(v, max)}">${v || ""}</td>`; }).join("")}</tr>`).join("");
  $("siteCalendar").innerHTML = `<h3>Bloom calendar</h3><div class="sub">Bloom days per month. Click a cell to jump there.</div>
    <table class="heat"><thead><tr><th></th>${MONTHS.map((m) => `<th>${m}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
  $("siteCalendar").querySelectorAll("[data-jump]").forEach((td) => td.onclick = () => jumpTo(td.dataset.jump));
}

function renderEvents() {
  const ev = state.events.filter((e) => e.start >= state.dates[0]);
  const f = (x, d = 1) => (x == null ? "–" : (+x).toFixed(d));
  $("eventList").innerHTML = `<h3>Bloom events since ${state.dates[0].slice(0, 4)} (${ev.length})</h3>
    <div class="sub">Click a row to jump to the bloom peak. Confidence: high = seen by a real (cloud-free) observation.</div>
    <div class="tablewrap"><table class="events"><thead><tr><th>Start</th><th>Days</th><th>Peak</th><th>×normal</th><th>Type</th><th>Confidence</th></tr></thead>
    <tbody>${ev.map((e) => `<tr data-jump="${e.peak_date}"><td class="nowrap">${shortDate(e.start)}</td><td>${e.days}</td>
      <td>${f(e.peak_chl)}</td><td>${f(e.peak_ratio)}</td><td>${e.type}</td>
      <td><span class="conf ${e.confidence.split(" ")[0]}">${e.confidence}</span></td></tr>`).join("") || `<tr><td colspan="6">No events detected</td></tr>`}</tbody></table></div>`;
  $("eventList").querySelectorAll("[data-jump]").forEach((tr) => tr.onclick = () => jumpTo(tr.dataset.jump));
}

let phenoChart = null;
function renderPhenology() {
  if (phenoChart) phenoChart.destroy();
  const ph = state.phenology.filter((p) => p.r2 > 0.3);
  const doyLabel = (v) => new Date(Date.UTC(2001, 0, v)).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  $("phenoNote").textContent = ph.length
    ? `Shifted-Gaussian fit to each spring (Zhai et al. 2012 method). ${ph.length} year(s) fit well (r² > 0.3). Normal start: ${doyLabel(Math.round(ph[0].start_doy_normal))}. Years with several peaks (common inshore) are left out.`
    : "No year with a single clear spring-bloom peak here. Inshore chlorophyll often has several peaks, which this method can't fit.";
  const years = ph.map((p) => p.year);
  phenoChart = new Chart($("phenoCanvas"), {
    type: "line",
    data: { labels: years, datasets: [
      { label: "Bloom start", data: ph.map((p) => p.start_doy), borderColor: css("--series-1"), backgroundColor: css("--series-1"), showLine: false, pointRadius: 4 },
      { label: "Bloom peak", data: ph.map((p) => p.peak_doy), borderColor: css("--series-2"), backgroundColor: css("--series-2"), showLine: false, pointRadius: 4 },
    ]},
    options: {
      animation: false, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false },
        tooltip: { backgroundColor: css("--surface"), titleColor: css("--text"), bodyColor: css("--text-2"), borderColor: css("--axis"), borderWidth: 1,
          callbacks: { label: (it) => `${it.dataset.label}: ${doyLabel(Math.round(it.raw))}` } } },
      scales: {
        x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--muted"), font: { size: 10 }, maxRotation: 0, autoSkip: true } },
        y: { grid: { color: css("--grid") }, border: { display: false },
          ticks: { color: css("--muted"), font: { size: 10 }, maxTicksLimit: 5, callback: (v) => doyLabel(v) } },
      },
    },
  });
}

function initTime() {
  state.dates = allDates();
  const s = $("dateSlider");
  s.max = state.dates.length - 1;
  s.oninput = () => setIdx(+s.value);
  $("datePicker").min = state.dates[0];
  $("datePicker").max = state.dates.at(-1);
  $("datePicker").onchange = (e) => { const i = state.dates.indexOf(e.target.value); if (i >= 0) setIdx(i); };
  $("prevBtn").onclick = () => setIdx(state.idx - 1);
  $("nextBtn").onclick = () => setIdx(state.idx + 1);
  $("playBtn").onclick = () => {
    if (state.playing) { clearInterval(state.playing); state.playing = null; $("playBtn").textContent = "▶"; return; }
    $("playBtn").textContent = "❚❚";
    state.playing = setInterval(() => {
      if (state.idx >= state.dates.length - 1) { $("playBtn").click(); return; }
      setIdx(state.idx + 1);
    }, 250);
  };
}

// ---------- boot ----------
(async function boot() {
  [state.manifest, state.sites] = await Promise.all([
    fetch("/api/manifest").then((r) => r.json()),
    fetch("/api/sites").then((r) => r.json()),
  ]);
  // deep links: ?date=2024-05-15&layer=chl&site=Seyðisfjörður
  const q = new URLSearchParams(location.search);
  if (q.get("view")) { const [la, lo, z] = q.get("view").split(",").map(Number); map.setView([la, lo], z); }
  if (q.get("tab") === "blooms") state.tab = "blooms";
  const urlLayer = q.get("layer") && state.manifest.layers[q.get("layer")];
  if (urlLayer) state.layer = q.get("layer");
  initTime();
  drawSites();
  setTab(state.tab, !!urlLayer);
  const start = state.dates.indexOf(q.get("date") || `${state.dates.at(-1).slice(0, 4)}-05-15`);
  await setIdx(start >= 0 ? start : 0);
  const site = state.sites.find((s) => s.name === q.get("site"));
  if (site) openSite(site);
})();
