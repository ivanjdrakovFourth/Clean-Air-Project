"use strict";

const form = document.getElementById("search-form");
const profile = document.getElementById("profile");
const customField = document.getElementById("custom-field");
const threshold = document.getElementById("threshold");
const results = document.getElementById("results");
const button = document.getElementById("search-button");
const panel = document.getElementById("collection-panel");

function updateReference() {
  const custom = profile.value === "custom";
  customField.hidden = !custom;
  threshold.disabled = !custom;
  threshold.required = custom;
}

profile.addEventListener("change", updateReference);
updateReference();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const params = new URLSearchParams(new FormData(form));
  const originalLabel = button.textContent;
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
    button.textContent = originalLabel;
    results.setAttribute("aria-busy", "false");
  }
});

async function updateStatus() {
  try {
    const response = await fetch("/api/status", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`Status request failed (${response.status}).`);
    const status = await response.json();
    if (status.ready && panel.dataset.ready !== "true") {
      window.location.reload();
      return;
    }
    const collection = status.collection;
    document.getElementById("collection-title").textContent = status.ready
      ? "Your local history is ready"
      : "Preparing your real-data history";
    const message = status.collection_enabled
      ? (collection?.message || "Waiting for collection to begin.")
      : ("Automatic updates are disabled. " + (status.ready
        ? "Using your stored history."
        : "Enable collection or run the sync command to import real history.")
        + (collection?.status === "failed" ? ` Last collection failed: ${collection.message}` : ""));
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
