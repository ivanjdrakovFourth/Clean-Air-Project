"use strict";
const alertUI = window.CleanAirUI;
const alertForm = document.getElementById("alert-form");
const alertMessage = document.getElementById("alert-message");
const byId = id => document.getElementById(id);
let editingAlert = null;
let editingEnabled = true;
let loadedRules = [];
function alertFields() {
  const condition = byId("alert-condition").value;
  const city = ["cleaner", "dirtier", "below", "above", "city_missing"].includes(condition);
  const health = ["source_stale", "collection_failed", "source_unavailable", "city_missing"].includes(condition);
  byId("alert-city-field").hidden = !city; byId("alert-city").required = city;
  byId("alert-threshold-field").hidden = !["below", "above"].includes(condition);
  byId("alert-threshold").required = !byId("alert-threshold-field").hidden;
  byId("alert-change-field").hidden = !["cleaner", "dirtier"].includes(condition);
  byId("alert-stale-field").hidden = condition !== "source_stale";
  for (const element of document.querySelectorAll(".alert-air-field")) element.hidden = health && !(condition === "city_missing" && (element.contains(byId("alert-pollutant")) || element.contains(byId("alert-monitor-class"))));
  byId("alert-reference-field").hidden = health || byId("alert-profile").value !== "custom";
  byId("alert-reference").required = !byId("alert-reference-field").hidden;
  for (const field of alertForm.querySelectorAll(".field")) {
    for (const input of field.querySelectorAll("input,select")) input.disabled = field.hidden;
  }
}
function definition() {
  const condition = byId("alert-condition").value;
  const health = ["source_stale", "collection_failed", "source_unavailable", "city_missing"].includes(condition);
  const city = ["cleaner", "dirtier", "below", "above", "city_missing"].includes(condition);
  return {
    name: byId("alert-name").value,
    sources: [...document.querySelectorAll('input[name="alert-sources"]:checked')].map(item => item.value),
    pollutant: byId("alert-pollutant").value, condition,
    monitor_class: byId("alert-monitor-class").value,
    city: city ? byId("alert-city").value || null : null,
    objective: health ? "average" : byId("alert-objective").value,
    window_days: health ? 0 : Number(byId("alert-window").value),
    profile: health ? "who_2021" : byId("alert-profile").value,
    reference: !health && byId("alert-profile").value === "custom" ? Number(byId("alert-reference").value) : null,
    threshold: ["below", "above"].includes(condition) ? Number(byId("alert-threshold").value) : null,
    minimum_change: ["cleaner", "dirtier"].includes(condition) ? Number(byId("alert-change").value || 0) : 0,
    stale_hours: condition === "source_stale" ? Number(byId("alert-stale").value || 48) : 48,
    desktop: byId("alert-desktop").checked, notify_initial: byId("alert-initial").checked,
    enabled: editingEnabled,
  };
}
function editAlert(rule) {
  editingAlert = rule.id; editingEnabled = rule.enabled; const value = rule.definition;
  const fields = { "alert-name": "name", "alert-condition": "condition", "alert-pollutant": "pollutant", "alert-monitor-class": "monitor_class", "alert-city": "city", "alert-objective": "objective", "alert-window": "window_days", "alert-profile": "profile", "alert-reference": "reference", "alert-threshold": "threshold", "alert-change": "minimum_change", "alert-stale": "stale_hours" };
  for (const [id, key] of Object.entries(fields)) byId(id).value = value[key] ?? "";
  for (const item of document.querySelectorAll('input[name="alert-sources"]')) item.checked = value.sources.includes(item.value);
  byId("alert-desktop").checked = value.desktop; byId("alert-initial").checked = value.notify_initial;
  byId("alert-form-title").textContent = `Edit alert #${rule.id}`; byId("save-alert").textContent = "Save alert changes"; byId("cancel-alert-edit").hidden = false;
  alertFields(); alertForm.scrollIntoView({ behavior: "smooth" });
}
function resetAlert() {
  editingAlert = null; editingEnabled = true; alertForm.reset(); byId("alert-form-title").textContent = "Create an alert"; byId("save-alert").textContent = "Create alert"; byId("cancel-alert-edit").hidden = true; alertFields();
}
async function loadAlerts() {
  try {
    const data = await alertUI.request("/api/alerts"); loadedRules = data.rules;
    const selection = byId("alert-city"), old = selection.value;
    const cities = [...new Set(Object.values(data.source_status).flatMap(source => source.cities || []))].sort();
    selection.replaceChildren(new Option("Choose a source-reported city", ""));
    for (const city of cities) selection.add(new Option(city, city));
    if (old && !cities.includes(old)) selection.add(new Option(`${old} (not currently in the source catalog)`, old));
    selection.value = old;
    const target = byId("alert-rules"); target.replaceChildren();
    if (!data.rules.length) target.append(alertUI.text("p", "No rules yet. Create one above; no baseline or change is invented.", "note"));
    for (const rule of data.rules) {
      const card = alertUI.text("article", "", "details-card");
      card.append(alertUI.text("h3", `${rule.definition.name} - ${rule.enabled ? "enabled" : "paused"}`), alertUI.text("p", `${rule.definition.condition}; ${rule.definition.pollutant}; ${rule.definition.sources.join(", ")}${rule.definition.city ? `; ${rule.definition.city}` : ""}`));
      for (const [source, state] of Object.entries(rule.state)) card.append(alertUI.text("p", `${source}: ${state.error || "Baseline/last result recorded."} Checked ${alertUI.time(state.last_checked)}.`, state.error ? "note" : "note"));
      const buttons = alertUI.text("div", "", "toolbar");
      const edit = alertUI.text("button", "Edit"); edit.type = "button"; edit.addEventListener("click", () => editAlert(rule));
      const toggle = alertUI.text("button", rule.enabled ? "Pause" : "Enable"); toggle.type = "button";
      toggle.addEventListener("click", () => changeAlert(`/api/alerts/${rule.id}/enabled`, "POST", { enabled: !rule.enabled }));
      const remove = alertUI.text("button", "Delete rule"); remove.type = "button";
      remove.addEventListener("click", () => changeAlert(`/api/alerts/${rule.id}`, "DELETE"));
      buttons.append(edit, toggle, remove); card.append(buttons); target.append(card);
    }
    const events = byId("alert-events"); events.replaceChildren();
    if (!data.events.length) events.append(alertUI.text("p", "No triggered events yet. The first valid result normally establishes a baseline.", "note"));
    for (const event of data.events) {
      const card = alertUI.text("article", "", `details-card ${event.unread ? "unread-event" : ""}`);
      card.append(alertUI.text("h3", event.title), alertUI.text("p", `${alertUI.time(event.created)} - ${event.source} - Windows: ${event.desktop_status}`, "note"), alertUI.text("p", event.message));
      const details = document.createElement("details"); details.append(alertUI.text("summary", "Source, time window and assessment details"), alertUI.text("pre", JSON.stringify(event.details, null, 2))); card.append(details);
      if (event.unread) { const button = alertUI.text("button", "Mark read"); button.type = "button"; button.addEventListener("click", () => changeAlert(`/api/alerts/events/${event.id}/read`, "POST")); card.append(button); }
      events.append(card);
    }
  } catch (error) { alertUI.message(alertMessage, error.message, true); }
}
async function changeAlert(url, method, body) {
  try { const result = await alertUI.request(url, method, body); alertUI.message(alertMessage, result.message); await loadAlerts(); }
  catch (error) { alertUI.message(alertMessage, error.message, true); }
}
alertForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!alertForm.reportValidity()) return;
  try {
    const result = await alertUI.request(editingAlert ? `/api/alerts/${editingAlert}` : "/api/alerts", editingAlert ? "PUT" : "POST", definition());
    alertUI.message(alertMessage, result.message); resetAlert(); await loadAlerts();
  } catch (error) { alertUI.message(alertMessage, error.message, true); }
});
byId("alert-condition").addEventListener("change", alertFields); byId("alert-profile").addEventListener("change", alertFields); byId("cancel-alert-edit").addEventListener("click", resetAlert);
byId("test-desktop").addEventListener("click", () => changeAlert("/api/alerts/test-desktop", "POST", {}));
alertFields(); loadAlerts(); window.setInterval(loadAlerts, 15000);
