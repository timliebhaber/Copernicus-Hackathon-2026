// AlgaeWatch Iceland: data explorer (Step 2) + blooms (Step 3)
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const $ = (id) => document.getElementById(id);

const state = {
  manifest: null, sites: [], dates: [], idx: 0, layer: "chl",
  site: null, series: null, charts: [], playing: null, snapshot: {}, window: "season", windowRange: [0, 0],
  tab: "data", events: [], phenology: [], calendar: null, horizon: 3, fc: {}, fcSeries: null, metrics: null,
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
  const order = { blooms: ["bloom", "anomaly", "chl"], forecast: ["anomaly", "bloom", "chl", "sst"],
                  model: ["bloom", "anomaly", "chl"] }[state.tab] || ["chl", "chl_obs", "sst", "rrs555"];
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
const kindColor = (k) => css(k === "salmon" ? "--series-1" : k === "shellfish" ? "--series-2" : "--series-3");
const LABEL_DIR = { "Reyðarfjörður": "left", "Fáskrúðsfjörður": "right", "Dýrafjörður": "top", "Patreksfjörður": "left",
  "Seyðisfjörður": "top", "Mjóifjörður eystri": "right", "Breiðafjörður": "top", "Breiðafjörður (Kiðey)": "bottom",
  "Hvalfjörður (Eyrarkot)": "left", "Steingrímsfjörður": "right", "Stakksfjörður": "bottom" };
function drawSites() {
  for (const s of state.sites) {
    const color = kindColor(s.kind);
    const m = L.circleMarker([s.lat, s.lon], {
      radius: s.kind === "hafro" ? 6 : 8, color: css("--marker-ring"), weight: 2, fillColor: color, fillOpacity: 1,
    });
    if (s.kind !== "hafro") m.addTo(map);
    const dir = LABEL_DIR[s.name] || "right";
    const off = { right: [8, 0], left: [-8, 0], bottom: [0, 8], top: [0, -8] }[dir];
    m.bindTooltip(s.name, { permanent: true, direction: dir, offset: off, className: "sitelabel" });
    m.on("click", () => openSite(s));
    m.on("mouseover", () => {
      const v = state.snapshot[s.name] || {};
      const f = (x, d = 2) => (x == null ? "–" : x.toFixed(d));
      m.unbindPopup();
      const status = v.is_bloom ? `<b style="color:${css("--critical")}">● Bloom detected</b> (${f(v.chl_anomaly_ratio, 1)}× normal)<br>`
        : v.is_cocco ? `<b style="color:${css("--critical")}">● Milky water (coccolithophores)</b><br>` : "";
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
    if (s.kind === "hafro") {  // Hafró stations on the Blooms and Forecast tabs
      const show = state.tab === "blooms" || state.tab === "forecast";
      if (show && !map.hasLayer(m)) m.addTo(map);
      if (!show && map.hasLayer(m)) map.removeLayer(m);
    }
    if (state.tab === "forecast") {
      const f = state.fc[s.name];
      const r0 = s.kind === "hafro" ? 6 : 8;
      if (!f) { m.setStyle({ fillColor: css("--axis"), radius: r0 }); m.setTooltipContent(s.name); continue; }
      const risk = f[`risk_${state.horizon}d`];
      const lv = f[`level_${state.horizon}d`];
      const tox = s.kind === "hafro" && f.toxic_risk_7d != null ? ` · toxic ${Math.round(f.toxic_risk_7d * 100)}%` : "";
      m.setStyle({ fillColor: css(`--lvl-${lv}`), radius: r0 + (lv === "high" ? 4 : lv === "medium" ? 2 : 0) });
      m.setTooltipContent(`${s.name} · ${Math.round(risk * 100)}%${tox}`);
      continue;
    }
    const v = state.snapshot[s.name] || {};
    const bloom = state.tab === "blooms" && v.is_bloom;
    const cocco = state.tab === "blooms" && v.is_cocco;
    const r0 = s.kind === "hafro" ? 6 : 8;
    m.setStyle({ fillColor: bloom || cocco ? css("--critical") : kindColor(s.kind), radius: bloom || cocco ? r0 + 3 : r0 });
    m.setTooltipContent(bloom ? `${s.name} · bloom` : cocco ? `${s.name} · milky water` : s.name);
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
  const label = { salmon: "Salmon farming fjord", shellfish: "Shellfish area", hafro: "Hafró toxic-algae monitoring station" }[s.kind];
  $("siteMeta").textContent = `${label} · ${s.lat.toFixed(2)}°N ${Math.abs(s.lon).toFixed(2)}°W · approximate location`;
  const q = encodeURIComponent(s.name);
  [state.series, state.events, state.phenology, state.toxic] = await Promise.all([
    fetch(`/api/timeseries?site=${q}`).then((r) => r.json()),
    fetch(`/api/events?site=${q}`).then((r) => r.json()),
    fetch(`/api/phenology?site=${q}`).then((r) => r.json()),
    s.kind === "hafro" ? fetch(`/api/toxic?station=${q}`).then((r) => r.json()) : Promise.resolve([]),
  ]);
  state.fcSeries = await fetch(`/api/forecast_series?site=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  renderPanel();
}

function renderPanel() {
  if (!state.site) {
    if (state.tab === "forecast") renderForecastList();
    else if (state.tab === "model") renderModelInfo();
    else renderOverview();
    return;
  }
  if (state.tab === "forecast" || state.tab === "model") { renderForecastSite(); return; }
  renderCharts();
  renderStats();
  if (state.tab === "blooms") { renderSiteCalendar(); renderEvents(); renderPhenology(); renderToxic(); }
}

document.querySelectorAll("[data-window]").forEach((b) => b.onclick = () => {
  state.window = b.dataset.window;
  document.querySelectorAll("[data-window]").forEach((x) => x.classList.toggle("on", x === b));
  if (state.series) renderCharts();
});

$("closePanel").onclick = () => {
  state.site = null; $("panelEmpty").hidden = false; $("panelSite").hidden = true; renderPanel();
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
  { title: "Chlorophyll vs normal", sub: "mg/m³, log scale; red bands = confirmed bloom, blue bands = milky water (coccolithophores)", log: true, bands: true, series: [
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
    const o = chart.options.plugins.bloomBands || {};
    if (!o.flags && !o.cocco) return;
    const { top, bottom } = chart.chartArea;
    const x = chart.scales.x;
    const w = Math.max(1, (x.getPixelForValue(1) - x.getPixelForValue(0)));
    const ctx = chart.ctx;
    ctx.save();
    for (const [arr, col] of [[o.flags, "--critical-wash"], [o.cocco, "--cocco-wash"]]) {
      if (!arr) continue;
      ctx.fillStyle = css(col);
      arr.forEach((f, i) => { if (f === 1) ctx.fillRect(x.getPixelForValue(i) - w / 2, top, w, bottom - top); });
    }
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
          bloomBands: { flags: spec.bands && S.is_bloom ? S.is_bloom.slice(lo, hi) : null,
                        cocco: spec.bands && S.is_cocco ? S.is_cocco.slice(lo, hi) : null },
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
  if (state.tab === "forecast" || state.tab === "model") { await loadForecast(); renderPanel(); }
  else renderStats();
}

// ---------- tabs ----------
function setTab(tab, keepLayer = false) {
  state.tab = tab;
  if (!keepLayer) state.layer = { blooms: "bloom", forecast: "anomaly", model: "bloom" }[tab] || "chl";
  document.body.dataset.tab = tab;
  document.querySelectorAll(".tab[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  buildLayerSelect();
  renderLegend();
  updateMap();
  $("panel").dataset.tab = tab;
  if (tab === "forecast" || tab === "model") loadForecast().then(() => { refreshMarkers(); renderPanel(); });
  else renderPanel();
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
  const all = state.events.filter((e) => e.start >= state.dates[0]);
  const ev = state.showCandidates ? all : all.filter((e) => e.status === "confirmed");
  const nCand = all.length - all.filter((e) => e.status === "confirmed").length;
  const f = (x, d = 1) => (x == null ? "–" : (+x).toFixed(d));
  $("eventList").innerHTML = `<h3>Bloom events since ${state.dates[0].slice(0, 4)} (${ev.length})</h3>
    <div class="sub">Confirmed = lasted ≥3 days and was seen on a clear day (milky water: ≥2 clear days). Click a row to jump to the peak.</div>
    <label class="check small"><input type="checkbox" id="candToggle" ${state.showCandidates ? "checked" : ""}> also show ${nCand} unconfirmed candidates</label>
    <div class="tablewrap"><table class="events"><thead><tr><th>Start</th><th>Kind</th><th>Days</th><th>Peak</th><th>×normal</th><th>Type (estimate)</th><th>Status</th></tr></thead>
    <tbody>${ev.map((e) => `<tr data-jump="${e.peak_date}" title="${e.reason}"><td class="nowrap">${shortDate(e.start)}</td>
      <td>${e.kind === "coccolithophore" ? "milky water" : "chlorophyll"}</td><td>${e.days}</td>
      <td>${f(e.peak_chl)}</td><td>${f(e.peak_ratio)}</td><td>${e.type}</td>
      <td><span class="conf ${e.status === "confirmed" ? "high" : "medium"}">${e.status}</span></td></tr>`).join("") || `<tr><td colspan="7">No confirmed events</td></tr>`}</tbody></table></div>`;
  $("eventList").querySelectorAll("[data-jump]").forEach((tr) => tr.onclick = () => jumpTo(tr.dataset.jump));
  $("candToggle").onchange = (e) => { state.showCandidates = e.target.checked; renderEvents(); };
}

function renderToxic() {
  const box = $("toxicInfo");
  if (state.site?.kind !== "hafro" || !state.toxic?.length) { box.innerHTML = ""; return; }
  const T = state.toxic;
  const above = T.filter((t) => t.any_above);
  const genus = (t) => ["dinophysis", "alexandrium", "pseudo_nitzschia"].filter((g) => t[`${g}_above`])
    .map((g) => ({ dinophysis: "Dinophysis (DSP)", alexandrium: "Alexandrium (PSP)", pseudo_nitzschia: "Pseudo-nitzschia (ASP)" })[g]);
  const years = [...new Set(T.map((t) => +t.date.slice(0, 4)))].sort();
  const cnt = {}; T.forEach((t) => { const k = t.date.slice(0, 7); cnt[k] = cnt[k] || [0, 0]; cnt[k][1]++; if (t.any_above) cnt[k][0]++; });
  const rows = years.map((y) => `<tr><th>${y}</th>${MONTHS.map((m, j) => {
    const k = `${y}-${String(j + 1).padStart(2, "0")}`; const c = cnt[k];
    if (!c) return `<td></td>`;
    return `<td title="${k}: ${c[0]} of ${c[1]} samples above threshold" style="background:${c[0] ? heat(c[0], 3) : "var(--grid)"}">${c[0] || "·"}</td>`; }).join("")}</tr>`).join("");
  box.innerHTML = `<h3>Toxic algae samples (Hafró, ${years[0]}–${years.at(-1)})</h3>
    <div class="sub">${T.length} samples, ${above.length} with a toxic genus above the shellfish-poisoning threshold. Parsed automatically from Hafró's Icelandic text, so expect a few errors. Grey = sampled, nothing above threshold.</div>
    <table class="heat"><thead><tr><th></th>${MONTHS.map((m) => `<th>${m}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
    <div class="tablewrap" style="margin-top:8px"><table class="events"><thead><tr><th>Date</th><th>Above threshold</th><th>Cells/L</th></tr></thead>
    <tbody>${above.slice(0, 60).map((t) => `<tr title="${(t.text || "").replace(/"/g, "'").slice(0, 400)}"><td class="nowrap">${shortDate(t.date)}</td>
      <td>${genus(t).join(", ")}</td><td>${[t.alexandrium_cells_per_l, t.dinophysis_cells_per_l, t.pseudo_nitzschia_cells_per_l].filter((x) => x).map((x) => Math.round(x).toLocaleString("en")).join(" / ") || "–"}</td></tr>`).join("")}</tbody></table></div>`;
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
  if (["blooms", "forecast", "model"].includes(q.get("tab"))) state.tab = q.get("tab");
  const urlLayer = q.get("layer") && state.manifest.layers[q.get("layer")];
  if (urlLayer) state.layer = q.get("layer");
  initTime();
  document.querySelector('[data-window="all"]').textContent = `All of ${state.dates[0].slice(0, 4)}–${state.dates.at(-1).slice(0, 4)}`;
  drawSites();
  setTab(state.tab, !!urlLayer);
  const start = state.dates.indexOf(q.get("date") || `${state.dates.at(-1).slice(0, 4)}-05-15`);
  await setIdx(start >= 0 ? start : 0);
  const site = state.sites.find((s) => s.name === q.get("site"));
  if (site) openSite(site);
})();


// ---------- forecast & model (Step 4-5) ----------
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const lvlBadge = (lv) => `<span class="lvl ${lv}">${lv}</span>`;

async function loadForecast() {
  try {
    const r = await fetch(`/api/forecast?date=${currentDate()}`);
    const j = r.ok ? await r.json() : { sites: [] };
    state.fc = Object.fromEntries(j.sites.map((x) => [x.site, x]));
  } catch { state.fc = {}; }
  refreshMarkers();
}

document.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => {
  state.horizon = +b.dataset.h;
  document.querySelectorAll("[data-h]").forEach((x) => x.classList.toggle("on", x === b));
  refreshMarkers(); renderPanel();
});

function renderForecastList() {
  const rows = Object.values(state.fc);
  const h = state.horizon;
  if (!rows.length) {
    const m = +currentDate().slice(5, 7);
    $("forecastList").innerHTML = `<div class="verdict">${m >= 11 || m <= 2 ? "No forecast in winter: satellites can't see the sea during the polar night (Nov–Feb)." : "No forecast for this date."}</div>`;
    return;
  }
  rows.sort((a, b) => b[`risk_${h}d`] - a[`risk_${h}d`]);
  const farms = rows.filter((r) => r.kind !== "hafro"), stations = rows.filter((r) => r.kind === "hafro");
  const tr = (r) => `<tr data-site="${r.site}"><td>${r.site}</td><td>${pct(r[`risk_${h}d`])}</td><td>${lvlBadge(r[`level_${h}d`])}</td>
    <td>${r.kind === "hafro" ? pct(r.toxic_risk_7d) : ""}</td></tr>`;
  $("forecastList").innerHTML = `
    <h3>Bloom risk, next ${h} days · issued ${fmtDate(currentDate())}</h3>
    <div class="sub">Chance of a confirmed high-biomass bloom. Levels: low &lt; ${config.low * 100}%, medium ${config.low * 100}–${config.high * 100}%, high &gt; ${config.high * 100}%. Model trained up to ${rows[0].model_trained_until}.</div>
    <table class="risklist"><thead><tr><th>Farm site</th><th>Risk</th><th>Level</th><th></th></tr></thead><tbody>${farms.map(tr).join("")}</tbody></table>
    <h3>Hafró stations</h3>
    <div class="sub">Bloom risk, plus toxic-algae risk in 7 days (experimental: not proven better than season alone).</div>
    <table class="risklist"><thead><tr><th>Station</th><th>Bloom</th><th>Level</th><th>Toxic 7d</th></tr></thead><tbody>${stations.map(tr).join("")}</tbody></table>`;
  $("forecastList").querySelectorAll("[data-site]").forEach((t) => t.onclick = () => openSite(state.sites.find((x) => x.name === t.dataset.site)));
}
const config = { low: 0.3, high: 0.6 };

let fcChart = null, toxChart = null;
function renderForecastSite() {
  const s = state.site, f = state.fc[s.name], h = state.horizon;
  const box = $("forecastSite");
  if (state.tab === "forecast") {
    $("stats").innerHTML = f ? `
      <div class="stat"><div class="k">Bloom within 3 days</div><div class="v">${pct(f.risk_3d)}</div><div class="u">${lvlBadge(f.level_3d)}</div></div>
      <div class="stat"><div class="k">Bloom within 7 days</div><div class="v">${pct(f.risk_7d)}</div><div class="u">${lvlBadge(f.level_7d)}</div></div>
      <div class="stat"><div class="k">Toxic algae in 7 days</div><div class="v">${s.kind === "hafro" ? pct(f.toxic_risk_7d) : "–"}</div><div class="u">${s.kind === "hafro" ? "experimental" : "Hafró stations only"}</div></div>`
      : `<div class="stat" style="grid-column:1/-1"><div class="k">No forecast for ${fmtDate(currentDate())}</div><div class="u">Forecasts cover Mar–Oct, 2005–2025.</div></div>`;
  } else $("stats").innerHTML = "";
  const factors = f ? f[`factors_${h}d`] : [];
  const outcome = f && f[`bloom_in_${h}d`] != null
    ? `What actually happened (known only afterwards): ${f[`bloom_in_${h}d`] ? "<b>a bloom occurred</b>" : "no bloom"} within ${h} days.` : "";
  const isModel = state.tab === "model";
  box.innerHTML = (isModel ? `<h3>Forecast vs reality at this site</h3><div class="sub">7-day bloom risk issued each day (out of sample) against actual confirmed bloom days (red bands). Test years 2019–2025 were never used for training the scored model.</div>`
    : `<h3>Why this risk (${h} days)</h3><div class="sub">Factors pushing the risk up most, from the model's feature contributions.</div>
       <ul class="factors">${factors.length ? factors.map((x) => `<li>${x.feature}</li>`).join("") : "<li>nothing stands out: risk is close to the normal level</li>"}</ul>
       <div class="outcome">${outcome}</div>
       <h3>Risk over time</h3><div class="sub">Forecast issued each day (out of sample) vs actual confirmed bloom days (red bands).</div>`) +
    `<div class="lgd" style="display:flex;gap:10px;font-size:11px;color:var(--text-2)"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--series-1);margin-right:4px"></i>3-day risk</span><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--series-2);margin-right:4px"></i>7-day risk</span></div>
     <div class="canvas" style="position:relative;height:170px"><canvas id="fcCanvas"></canvas></div>
     ${s.kind === "hafro" ? `<h3>Toxic-algae risk (7 days ahead)</h3><div class="sub">Experimental. Grey dots: Hafró samples above threshold.</div><div class="canvas" style="position:relative;height:130px"><canvas id="toxCanvas"></canvas></div>` : ""}`;
  drawForecastChart(isModel);
}

function drawForecastChart(isModel) {
  if (fcChart) fcChart.destroy();
  if (toxChart) { toxChart.destroy(); toxChart = null; }
  const S = state.fcSeries;
  if (!S || !S.date.length) return;
  let lo, hi;
  if (isModel) { lo = S.date.findIndex((d) => d >= "2019-01-01"); hi = S.date.length; if (lo < 0) lo = 0; }
  else {
    const c = S.date.findIndex((d) => d >= currentDate());
    const ci = c < 0 ? S.date.length - 1 : c;
    lo = Math.max(0, ci - 120); hi = Math.min(S.date.length, ci + 60);
  }
  const labels = S.date.slice(lo, hi);
  const opts = (ymax) => ({
    animation: false, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, bloomBands: { flags: S.is_bloom_today.slice(lo, hi) },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--text"), bodyColor: css("--text-2"), borderColor: css("--axis"), borderWidth: 1,
        callbacks: { title: (it) => fmtDate(it[0].label), label: (it) => `${it.dataset.label}: ${pct(it.raw)}` } } },
    scales: {
      x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--muted"), font: { size: 10 }, maxRotation: 0, autoSkip: false,
        callback(v) { const d = labels[v]; if (!d) return null; const yearStart = d.slice(5) === "03-01";
          if (isModel) return yearStart ? d.slice(0, 4) : null;
          return d.endsWith("-01") ? new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" }) : null; } } },
      y: { min: 0, max: ymax, grid: { color: css("--grid") }, border: { display: false }, ticks: { color: css("--muted"), font: { size: 10 }, maxTicksLimit: 4, callback: (v) => pct(v) } },
    },
  });
  fcChart = new Chart($("fcCanvas"), {
    type: "line",
    data: { labels, datasets: [
      { label: "3-day risk", data: S.risk_3d.slice(lo, hi), borderColor: css("--series-1"), borderWidth: isModel ? 1 : 2, pointRadius: 0, tension: 0, spanGaps: false },
      { label: "7-day risk", data: S.risk_7d.slice(lo, hi), borderColor: css("--series-2"), borderWidth: isModel ? 1 : 2, pointRadius: 0, tension: 0, spanGaps: false },
    ]},
    plugins: [bloomBands, dateLine],
    options: opts(1),
  });
  if (state.site.kind === "hafro" && $("toxCanvas")) {
    const hits = {};
    (state.toxic || []).forEach((t) => { if (t.any_above) hits[t.date] = 1; });
    toxChart = new Chart($("toxCanvas"), {
      type: "line",
      data: { labels, datasets: [
        { label: "Toxic risk", data: S.toxic_risk_7d.slice(lo, hi), borderColor: css("--series-3"), borderWidth: 2, pointRadius: 0, tension: 0 },
        { label: "Hafró: above threshold", data: labels.map((d) => (hits[d] ? 0.97 : null)), showLine: false, pointRadius: 3, borderColor: css("--muted"), backgroundColor: css("--muted") },
      ]},
      plugins: [dateLine],
      options: { ...opts(1), plugins: { ...opts(1).plugins, bloomBands: {} } },
    });
  }
}

async function renderModelInfo() {
  if (!state.metrics) state.metrics = await (await fetch("/api/metrics")).json();
  const M = state.metrics;
  const f3 = (x) => (x == null ? "–" : x.toFixed(2));
  const ci = (c) => `[${c[0] >= 0 ? "+" : ""}${c[0].toFixed(2)}, ${c[1] >= 0 ? "+" : ""}${c[1].toFixed(2)}]`;
  const sig = (c) => (c[0] > 0 ? "<b>significantly better</b>" : c[1] < 0 ? "<b>significantly worse</b>" : "not significantly different");
  const table = (r, rows) => `<table class="mtable"><thead><tr><th>Method</th><th>ROC-AUC</th><th>PR-AUC</th><th>Precision</th><th>Recall</th></tr></thead><tbody>
    ${rows.map(([k, name]) => r[k] ? `<tr class="${k === "model" || k === "lightgbm" ? "best" : ""}"><td>${name}</td><td>${f3(r[k].roc_auc)}</td><td>${f3(r[k].pr_auc)}</td><td>${f3(r[k].precision)}</td><td>${f3(r[k].recall)}</td></tr>` : "").join("")}</tbody></table>`;
  let html = `<h3>How good are the forecasts?</h3><div class="sub">All scores are on years the model never saw during training. ROC-AUC: 0.5 = coin flip, 1 = perfect. PR-AUC: compare with the share of positives (what random guessing would score).</div>`;
  if (M.bloom) {
    for (const [k, r] of Object.entries(M.bloom)) {
      html += `<h3>Bloom within ${r.horizon_days} days</h3><div class="sub">Test ${r.test_years}, ${r.n_test.toLocaleString("en")} site-days, ${pct(r.positive_rate_test)} with a bloom.</div>
        ${table(r, [["model", "Model (LightGBM)"], ["baseline_persistence", "Persistence: bloom seen last week"], ["baseline_seasonal", "Seasonal: usual rate for site & month"]])}
        <div class="verdict">vs persistence: ${sig(r.auc_gain_vs_persistence_ci95)} (AUC gain ${ci(r.auc_gain_vs_persistence_ci95)}); vs seasonal: ${sig(r.auc_gain_vs_seasonal_ci95)} ${ci(r.auc_gain_vs_seasonal_ci95)}.<br>
        <b>New blooms only</b> (none seen the week before, ${pct(r.onset.positive_rate)} positive): AUC ${f3(r.onset.model.roc_auc)} vs seasonal ${f3(r.onset.baseline_seasonal.roc_auc)}, PR-AUC ${f3(r.onset.model.pr_auc)}, ${sig(r.onset.auc_gain_vs_seasonal_ci95)}.</div>`;
    }
    const imp = Object.entries(M.bloom["7d"].importance).slice(0, 8);
    const mx = imp[0][1];
    html += `<h3>What drives the 7-day bloom forecast</h3><div class="sub">Share of the model's total gain.</div>` +
      imp.map(([k, v]) => `<div class="barrow"><span class="lab" title="${k}">${k}</span><div class="bar" style="width:${(v / mx) * 100}%"></div><span class="num">${pct(v)}</span></div>`).join("");
  }
  if (M.toxic) {
    html += `<h3>Toxic algae in 7 days (Hafró stations)</h3><div class="sub">Trained on 2005–2014 Hafró samples, tested on 2015–2019 (194 samples).</div>`;
    const names = { any_above: "Any toxic genus", alexandrium_above: "Alexandrium (PSP)", dinophysis_above: "Dinophysis (DSP)", pseudo_nitzschia_above: "Pseudo-nitzschia (ASP)" };
    html += `<table class="mtable"><thead><tr><th>Target</th><th>Model AUC</th><th>Seasonal AUC</th><th>Verdict</th></tr></thead><tbody>` +
      M.toxic.map((r) => `<tr><td>${names[r.target]}</td><td>${f3(r.lightgbm.roc_auc)}</td><td>${f3(r.baseline_seasonal.roc_auc)}</td><td>${r.auc_gain_vs_best_baseline.ci95[0] > 0 ? "better" : r.auc_gain_vs_best_baseline.ci95[1] < 0 ? "worse" : "not proven"}</td></tr>`).join("") +
      `</tbody></table><div class="verdict">Conditions add at most a little over "it's July at this station". The toxic forecast is shown as <b>experimental</b>.</div>`;
  }
  const V = M.validation;
  if (V) {
    html += `<h3>Detector checked against independent evidence</h3><ul class="plainlist">` +
      V.documented_events.map((e) => `<li>${e.result.startsWith("DETECTED") ? "✅" : "⚠️"} ${e.event}: ${e.result}${e.first_detection ? " from " + e.first_detection : ""}</li>`).join("") +
      (V.hafro_toxic_vs_satellite ? `<li>ℹ️ Satellite blooms near toxic Hafró samples: ${pct(V.hafro_toxic_vs_satellite.share_toxic_samples_with_satellite_bloom)} vs ${pct(V.hafro_toxic_vs_satellite.share_clean_samples_with_satellite_bloom)} near clean ones, so toxic algae are invisible to chlorophyll.</li>` : "") +
      (V.phenology_vs_literature ? `<li>ℹ️ Spring bloom peak (median ${V.phenology_vs_literature.median_peak}) vs literature: peak usually in May.</li>` : "") + `</ul>`;
  }
  html += `<h3>Honest caveats</h3><ul class="plainlist">
    <li>• Chlorophyll from space is ±40% and blind under cloud (about half the days) and in the polar night.</li>
    <li>• A "bloom" here means high biomass; it says nothing about toxicity.</li>
    <li>• Gap-filled chlorophyll is used with a 5-day delay, because the archive may fill a day using later images.</li>
    <li>• Site points are approximate placeholders, not licensed farm positions.</li></ul>`;
  $("modelInfo").innerHTML = html;
}
