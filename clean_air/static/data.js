"use strict";
const dataUI = window.CleanAirUI;
const dataForm = document.getElementById("data-form");
let dataPage = 1;
let dataRequestVersion = 0;
let locationRequestVersion = 0;
const dataMessage = document.getElementById("data-message");
function dataParams() {
  const params = new URLSearchParams(new FormData(dataForm));
  for (const [key, value] of [...params.entries()]) if (!value) params.delete(key);
  return params;
}
function updateDataFilters() {
  const table = document.getElementById("data-table").value;
  const measurement = ["observations", "station_days"].includes(table);
  const location = ["observations", "station_days", "stations", "station_metadata", "sampling_points"].includes(table);
  for (const block of document.querySelectorAll(".measurement-filter")) { block.hidden = !measurement; block.querySelector("input,select").disabled = !measurement; }
  for (const block of document.querySelectorAll(".location-filter")) { block.hidden = !location; block.querySelector("input,select").disabled = !location; }
}
function dataCell(value, key) {
  if (value === null) return "Not supplied";
  if (["started", "ended", "created", "finished", "result_time", "last_success"].includes(key)) return dataUI.time(value);
  if (["coverage", "data_capture"].includes(key)) return `${(value * 100).toFixed(1)}%`;
  if (key === "pollutant") return ({ 6001: "PM2.5", 5: "PM10", 8: "NO2", 7: "O3", 1: "SO2", 10: "CO" })[value] || String(value);
  return String(value);
}
async function loadData() {
  const version = ++dataRequestVersion;
  updateDataFilters(); const params = dataParams(); params.set("page", dataPage);
  try {
    const data = await dataUI.request(`/api/data?${params}`);
    if (version !== dataRequestVersion) return;
    document.getElementById("data-caption").textContent = `${data.table_name} - ${data.source_name}`;
    document.getElementById("data-summary").textContent = `${data.total.toLocaleString()} matching records. ${data.notice}`;
    const table = document.createElement("table"), head = document.createElement("tr");
    for (const column of data.columns) head.append(dataUI.text("th", data.column_labels[column]));
    table.append(head);
    for (const record of data.rows) {
      const row = document.createElement("tr");
      for (const column of data.columns) row.append(dataUI.text("td", dataCell(record[column], column)));
      table.append(row);
    }
    const target = document.getElementById("data-rows"); target.replaceChildren();
    if (data.rows.length) target.append(table); else target.append(dataUI.text("p", "No records from this source match these filters. Another source is not substituted.", "status-message waiting"));
    document.getElementById("page-label").textContent = `Page ${data.page} of ${data.pages}`;
    document.getElementById("previous-page").disabled = data.page <= 1;
    document.getElementById("next-page").disabled = data.page >= data.pages;
    const exportParams = dataParams(); document.getElementById("export-data").href = `/api/data/export?${exportParams}`;
  } catch (error) {
    if (version === dataRequestVersion) dataUI.message(dataMessage, error.message, true);
  }
}
async function loadDataLocations() {
  const version = ++locationRequestVersion;
  try {
    const source = document.getElementById("data-source").value;
    const cities = document.getElementById("data-city"), stations = document.getElementById("data-station");
    cities.replaceChildren(new Option("All reported city groupings", ""));
    stations.replaceChildren(new Option("All source locations", ""));
    const status = await dataUI.request(`/api/status?source=${encodeURIComponent(source)}`);
    if (version !== locationRequestVersion) return false;
    for (const city of status.cities || []) cities.add(new Option(city, city));
    for (const station of status.official_stations || []) stations.add(new Option(station.name, station.id));
    const data = await dataUI.request(`/api/data?source=${encodeURIComponent(source)}&table=archives&page_size=100`);
    if (version !== locationRequestVersion) return false;
    const target = document.getElementById("archive-list"); target.replaceChildren();
    if (!data.rows.length) target.append(dataUI.text("p", "No cold-storage archives exist for this source yet. That is expected before data exceeds 36 months.", "note"));
    for (const archive of data.rows) {
      const row = dataUI.text("div", "", "details-card"); row.append(dataUI.text("strong", archive.name), dataUI.text("p", `Older than ${archive.cutoff}; ${archive.observations} source records. SHA-256: ${archive.sha256}`));
      const button = dataUI.text("button", "Restore verified source archive"); button.type = "button";
      button.addEventListener("click", () => submitDataJob("restore", archive.name)); row.append(button); target.append(row);
    }
    return true;
  } catch (error) {
    if (version === locationRequestVersion) dataUI.message(dataMessage, error.message, true);
    return false;
  }
}
async function submitDataJob(action, artifact = undefined) {
  try {
    const payload = { source: document.getElementById("data-source").value, action };
    if (artifact) payload.artifact = artifact;
    const result = await dataUI.request("/api/jobs", "POST", payload);
    dataUI.message(dataMessage, `${result.message} Job #${result.id}; inspect Monitoring for the verified outcome.`);
  } catch (error) { dataUI.message(dataMessage, error.message, true); }
}
dataForm.addEventListener("submit", event => { event.preventDefault(); dataPage = 1; loadData(); });
document.getElementById("data-table").addEventListener("change", () => { dataPage = 1; loadData(); });
document.getElementById("data-source").addEventListener("change", async () => {
  dataPage = 1; dataRequestVersion++;
  document.getElementById("data-rows").replaceChildren(dataUI.text("p", "Loading the selected source's own records...", "note"));
  document.getElementById("data-summary").textContent = "Changing source; previous rows are not reused.";
  if (await loadDataLocations()) await loadData();
});
document.getElementById("export-data").addEventListener("click", event => {
  updateDataFilters();
  event.currentTarget.href = `/api/data/export?${dataParams()}`;
});
document.getElementById("previous-page").addEventListener("click", () => { dataPage--; loadData(); });
document.getElementById("next-page").addEventListener("click", () => { dataPage++; loadData(); });
document.getElementById("data-backup").addEventListener("click", () => submitDataJob("backup"));
document.getElementById("data-archive").addEventListener("click", () => submitDataJob("archive"));
loadDataLocations().then(current => { if (current) loadData(); });
