"use strict";
const monitorUI = window.CleanAirUI;
const monitorMessage = document.getElementById("monitor-message");
let monitoringRequestVersion = 0;
function chart(container, series) {
  container.replaceChildren();
  const values = (series[0]?.values || []).map(item => [Number(item[0]), Number(item[1])]).filter(item => Number.isFinite(item[1]));
  if (!values.length) { container.append(monitorUI.text("p", "No measured traffic/history in this window yet. Missing history is not zero latency.", "note")); return; }
  const maximum = Math.max(...values.map(item => item[1]), 0.001);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 500 130"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Actual recorded Prometheus values");
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", values.map((item, index) => `${10 + 480 * index / Math.max(1, values.length - 1)},${115 - 100 * item[1] / maximum}`).join(" "));
  line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "2");
  svg.append(line); container.append(svg, monitorUI.text("p", `Latest ${values.at(-1)[1].toFixed(3)}; peak ${maximum.toFixed(3)}.`, "note"));
}
function cityTable(cities) {
  const details = document.createElement("details"); details.className = "details-card";
  details.append(monitorUI.text("summary", "City/pollutant freshness and historical coverage"));
  const scroll = monitorUI.text("div", "", "table-scroll"), table = document.createElement("table");
  const header = document.createElement("tr");
  for (const text of ["Reported city", "Data state", "Last valid reading ends", "Best station days", "Coverage"]) header.append(monitorUI.text("th", text));
  table.append(header);
  for (const city of cities) {
    const row = document.createElement("tr");
    for (const text of [city.city, city.status, monitorUI.time(city.latest), `${city.best_station_days} / ${city.expected_days}`, `${city.coverage_percent.toFixed(1)}%`]) row.append(monitorUI.text("td", text));
    table.append(row);
  }
  scroll.append(table); details.append(scroll); return details;
}
async function loadMonitoring() {
  const version = ++monitoringRequestVersion;
  try {
    const params = new URLSearchParams({ pollutant: document.getElementById("health-pollutant").value, window: document.getElementById("monitor-window").value });
    const data = await monitorUI.request(`/api/monitoring?${params}`);
    if (version !== monitoringRequestVersion) return;
    const health = document.getElementById("source-health"); health.replaceChildren();
    for (const source of data.health.sources) {
      const card = monitorUI.text("article", "", `health-card ${source.tone}`);
      card.append(monitorUI.text("h2", source.name), monitorUI.text("strong", source.label), monitorUI.text("p", source.reason || source.collection?.message || "Inspect source coverage below."));
      card.append(monitorUI.text("p", `${source.counts.observations.toLocaleString()} stored source rows; ${source.counts.locations} physical locations.`, "note"));
      card.append(monitorUI.text("p", `Collector worker: ${source.worker_alive === true ? "running" : source.worker_alive === false ? "stopped / disabled" : "not configured"}.`, "note"));
      card.append(monitorUI.text("p", `Latest measurement: ${monitorUI.time(source.latest)}. Last complete collection: ${monitorUI.time(source.last_success)}.`, "note"));
      if (source.bundled_seed) card.append(monitorUI.text("p", `Bundled snapshot: ${monitorUI.time(source.bundled_seed.created)}. ${source.bundled_seed.notice}`, "note"));
      card.append(monitorUI.text("p", `${source.quality_last_24h.records} ${data.health.pollutant} raw records in the last 24h; ${source.quality_last_24h.excluded} source-excluded/missing/negative; ${source.quality_last_24h.future} future-timestamped.`, "note"));
      if (source.id === "openaq") card.append(monitorUI.text("p", `${source.requests_last_hour} application requests in the last hour. ${source.backfill_complete ? "Configured history collected." : "Initial history is not complete."}${source.cooldown_until ? ` Quota cooldown until ${monitorUI.time(source.cooldown_until)}.` : ""}`, "note"));
      const refresh = monitorUI.text("button", "Request source refresh");
      refresh.type = "button"; refresh.disabled = source.id === "bg_direct";
      refresh.addEventListener("click", async () => {
        try { const result = await monitorUI.request(`/api/sources/${source.id}/refresh`, "POST"); monitorUI.message(monitorMessage, result.message); }
        catch (error) { monitorUI.message(monitorMessage, error.message, true); }
      });
      card.append(refresh, cityTable(source.cities)); health.append(card);
    }
    document.getElementById("health-definitions").replaceChildren(...data.health.definitions.map(text => monitorUI.text("li", text)));
    const prom = data.prometheus;
    document.getElementById("prometheus-state").textContent = prom.available ? "Live Prometheus queries succeeded. Graphs represent recorded application traffic." : prom.reason;
    chart(document.getElementById("request-chart"), prom.charts.search_requests_per_second || []);
    chart(document.getElementById("latency-chart"), prom.charts.search_p95_ms || []);
    const alertBox = document.getElementById("platform-alerts"); alertBox.replaceChildren();
    if (!prom.available) alertBox.append(monitorUI.text("p", "Platform alert state could not be retrieved; no all-clear is assumed.", "status-message waiting"));
    else if (!prom.alerts.length) alertBox.append(monitorUI.text("p", "No active Prometheus health alerts in the retrieved result.", "note"));
    for (const alert of prom.alerts) alertBox.append(monitorUI.text("p", `${alert.labels.alertname}${alert.labels.source ? ` (${alert.labels.source})` : ""}: ${alert.state}. ${alert.annotations?.summary || ""}`, "status-message waiting"));
    renderJobs(data.jobs);
  } catch (error) {
    if (version === monitoringRequestVersion) monitorUI.message(monitorMessage, error.message, true);
  }
}
function renderJobs(jobs) {
  const target = document.getElementById("maintenance-jobs"); target.replaceChildren();
  for (const job of jobs) {
    const item = monitorUI.text("article", "", "details-card");
    item.append(monitorUI.text("strong", `#${job.id} ${job.request.source} / ${job.request.action}: ${job.status}`), monitorUI.text("p", job.message));
    if (job.result) {
      const details = document.createElement("details"); details.append(monitorUI.text("summary", "View measured outcome / diagnostic output"), monitorUI.text("pre", JSON.stringify(job.result, null, 2))); item.append(details);
    }
    if (job.status === "completed" && job.request.action === "backup") { const link = monitorUI.text("a", "Download verified backup"); link.href = `/api/jobs/${job.id}/download`; item.append(link); }
    target.append(item);
  }
}
document.getElementById("refresh-monitoring").addEventListener("click", loadMonitoring);
document.getElementById("health-pollutant").addEventListener("change", loadMonitoring);
document.getElementById("monitor-window").addEventListener("change", loadMonitoring);
document.getElementById("openaq-key-form").addEventListener("submit", async event => {
  event.preventDefault(); const field = document.getElementById("openaq-key");
  try { const data = await monitorUI.request("/api/settings/openaq-key", "POST", { api_key: field.value }); field.value = ""; monitorUI.message(monitorMessage, data.message); await loadMonitoring(); }
  catch (error) { field.value = ""; monitorUI.message(monitorMessage, error.message, true); }
});
for (const button of document.querySelectorAll("[data-job]")) button.addEventListener("click", async () => {
  try { const data = await monitorUI.request("/api/jobs", "POST", { source: document.getElementById("job-source").value, action: button.dataset.job }); monitorUI.message(monitorMessage, data.message); await loadMonitoring(); }
  catch (error) { monitorUI.message(monitorMessage, error.message, true); }
});
loadMonitoring();
window.setInterval(loadMonitoring, 15000);
