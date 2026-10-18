"use strict";

const form = document.getElementById("search-form");
const profile = document.getElementById("profile");
const customField = document.getElementById("custom-field");
const threshold = document.getElementById("threshold");
const results = document.getElementById("results");
const button = document.getElementById("search-button");
const panel = document.getElementById("collection-panel");
const view = document.getElementById("view");
const target = document.getElementById("target");
const objective = document.getElementById("objective");
const locationFilter = document.getElementById("location");
const changeNotice = document.getElementById("search-change-notice");
const selectedSource = document.getElementById("source");
const sourceSelection = document.getElementById("source-selection");
let sourceMetadataVersion = 0;

function updateReference() {
  const custom = profile.value === "custom";
  customField.hidden = !custom;
  threshold.disabled = !custom;
  threshold.required = custom;
}

function updateSettings() {
  sourceSelection.hidden = selectedSource.value !== "combined";
  for (const checkbox of sourceSelection.querySelectorAll("input")) checkbox.disabled = sourceSelection.hidden;
  const sourceBoxes = [...sourceSelection.querySelectorAll("input")];
  sourceBoxes[0].setCustomValidity(selectedSource.value === "combined" && !sourceBoxes.some(item => item.checked)
    ? "Select at least one source store." : "");
  for (const group of locationFilter.querySelectorAll("optgroup")) {
    const active = group.dataset.view === view.value;
    group.hidden = !active;
    group.disabled = !active;
    if (!active && [...group.querySelectorAll("option")].some(option => option.selected)) {
      locationFilter.value = "";
    }
  }
  const descriptions = {
    average: "Lowest pollution means cleaner air. Blank dates compare the latest common reported hour. Historical dates compare averages.",
    reference_days: "Ranks by the highest share of valid days within your daily reference; lower mean breaks ties. Historical dates are required.",
    improvement: "Ranks percentage reduction against earlier stored history using the same station cohort. This does not mean lowest absolute pollution. Historical dates are required.",
  };
  document.getElementById("definition-help").textContent = target.value === "locations"
    ? descriptions[objective.value]
    : "Finds the lowest measured day/hour within your dates. Choose lowest pollution as the definition. Hourly searches cannot reconstruct hours from daily-only history.";
  button.textContent = selectedSource.value === "combined" ? "Compare sources \u2192"
    : objective.value === "improvement" ? "Compare improvement \u2192"
    : objective.value === "reference_days" ? "Compare reference days \u2192"
    : "Find cleanest air \u2192";
}

function searchParameters() {
  const params = new URLSearchParams(new FormData(form));
  for (const [name, value] of [...params.entries()]) {
    if (value === "") params.delete(name);
  }
  return params;
}

profile.addEventListener("change", updateReference);
view.addEventListener("change", updateSettings);
target.addEventListener("change", updateSettings);
objective.addEventListener("change", updateSettings);
selectedSource.addEventListener("change", async () => {
  const version = ++sourceMetadataVersion;
  locationFilter.value = "";
  updateSettings();
  const requestedSource = selectedSource.value;
  try {
    const response = await fetch(`/api/status?source=${encodeURIComponent(requestedSource)}`, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`Source state unavailable (${response.status}).`);
    const status = await response.json();
    if (version !== sourceMetadataVersion || selectedSource.value !== requestedSource) return;
    const stationGroup = locationFilter.querySelector('[data-view="stations"]');
    const cityGroup = locationFilter.querySelector('[data-view="cities"]');
    stationGroup.replaceChildren(...(status.official_stations || []).map(item => new Option(item.name, item.id)));
    cityGroup.replaceChildren(...(status.cities || []).map(city => new Option(city, city)));
    const minimum = status.available_start || "", maximum = status.available_end || "";
    for (const name of ["start", "end"]) { document.getElementById(name).min = minimum; document.getElementById(name).max = maximum; }
    panel.dataset.ready = status.ready ? "true" : "false";
    document.getElementById("collection-title").textContent = `${status.source_name}: ${status.ready ? "local cache available" : "waiting for measurements"}`;
    document.getElementById("collection-message").textContent = status.reason || status.collection?.message || "Inspect Monitoring for this source's own coverage.";
  } catch (error) {
    if (version !== sourceMetadataVersion) return;
    console.error("Source metadata could not be loaded:", error);
    document.getElementById("collection-message").textContent = error.message;
  }
});
sourceSelection.addEventListener("change", updateSettings);
updateReference();
updateSettings();

form.addEventListener("input", () => { changeNotice.hidden = false; });
form.addEventListener("change", () => { changeNotice.hidden = false; });

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const params = searchParameters();
  button.disabled = true;
  button.textContent = "Comparing stored measurements...";
  results.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/results?${params}`, {
      headers: { Accept: "text/html" },
      signal: AbortSignal.timeout(30000),
    });
    const content = await response.text();
    if (!response.headers.get("content-type")?.includes("text/html")) {
      throw new Error(`The local server returned ${response.status}. Check application logs.`);
    }
    results.innerHTML = content;
    changeNotice.hidden = searchParameters().toString() === params.toString();
    window.history.replaceState(null, "", `/?${params}`);
    results.focus({ preventScroll: true });
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    console.error("Local comparison failed:", error);
    const card = document.createElement("section");
    card.className = "error-card";
    card.setAttribute("role", "alert");
    const title = document.createElement("h2");
    title.textContent = "The local application could not complete this request.";
    const description = document.createElement("p");
    description.textContent = `${error.message} Make sure the application is running, then retry.`;
    card.append(title, description);
    results.replaceChildren(card);
  } finally {
    button.disabled = false;
    updateSettings();
    results.setAttribute("aria-busy", "false");
  }
});

async function updateStatus() {
  const requestedSource = selectedSource.value;
  const version = sourceMetadataVersion;
  try {
    const response = await fetch(`/api/status?source=${encodeURIComponent(requestedSource)}`, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`Status request failed (${response.status}).`);
    const status = await response.json();
    if (version !== sourceMetadataVersion || selectedSource.value !== requestedSource) {
      window.setTimeout(updateStatus, 15000);
      return;
    }
    if (status.ready && panel.dataset.ready !== "true" && changeNotice.hidden) {
      window.location.reload();
      return;
    }
    const lastSuccess = String(status.last_success || 0);
    const showingCurrent = document.getElementById("start").value === ""
      && document.getElementById("end").value === ""
      && target.value === "locations" && objective.value === "average";
    if (status.ready && showingCurrent && changeNotice.hidden
        && lastSuccess !== panel.dataset.lastSuccess && lastSuccess !== "0") {
      window.location.reload();
      return;
    }
    panel.dataset.lastSuccess = lastSuccess;
    const collection = status.collection;
    document.getElementById("collection-title").textContent =
      `${status.source_name}: ${status.ready ? "local cache available" : "waiting for source measurements"}`;
    const message = status.reason || (status.collection_enabled
      ? (collection?.message || "Waiting for collection to begin.")
      : ("Automatic updates are disabled. " + (status.ready
        ? "Using this source's stored history."
        : "Enable collection or run the sync command to import real history.")
        + (collection?.status === "failed" ? ` Last collection failed: ${collection.message}` : "")));
    document.getElementById("collection-message").textContent = message
      + (status.source_stale ? " The newest stored measurement is stale." : "");
    panel.classList.toggle("attention", status.source_stale || collection?.status === "failed");
  } catch (error) {
    console.error("Cannot check local collection status:", error);
    panel.classList.add("attention");
    document.getElementById("collection-message").textContent =
      "Cannot check collection status. Ensure the local application is still running.";
  }
  window.setTimeout(updateStatus, 15000);
}

window.setTimeout(updateStatus, 15000);
