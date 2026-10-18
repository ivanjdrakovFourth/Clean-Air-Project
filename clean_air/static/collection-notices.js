"use strict";
(() => {
  const ui = window.CleanAirUI;
  const dialog = document.getElementById("startup-sync-dialog");
  const region = document.getElementById("collection-notification-region");
  const preference = document.getElementById("collection-desktop");
  let currentSession = null;
  let startupEvent = null;
  let after = 0;
  let acknowledged = null;
  let loading = false;
  let savingPreference = false;
  try { acknowledged = localStorage.getItem("clean-air-startup-ack"); }
  catch (error) { console.warn("Startup acknowledgement cannot be read:", error.name); }

  async function markRead(identity) {
    if (identity) await ui.request(`/api/alerts/events/${identity}/read`, "POST");
  }
  dialog.addEventListener("cancel", event => event.preventDefault());
  document.getElementById("startup-sync-ok").addEventListener("click", async () => {
    acknowledged = currentSession;
    dialog.close();
    try { localStorage.setItem("clean-air-startup-ack", acknowledged); }
    catch (error) { console.warn("Startup acknowledgement is remembered only on this page:", error.name); }
    try { await markRead(startupEvent); }
    catch (error) { region.append(ui.text("p", `The notice was acknowledged, but its history could not be marked read: ${error.message}`, "status-message waiting")); }
  });
  function showEvent(event) {
    if (event.source === "application") return;
    const card = ui.text("article", "", "collection-notice");
    const phase = event.details.phase;
    card.classList.add(phase === "completed" ? "good" : phase === "failed" ? "bad" : "waiting");
    card.append(ui.text("strong", event.title), ui.text("p", event.message));
    const button = ui.text("button", "OK"); button.type = "button";
    button.addEventListener("click", async () => {
      try { await markRead(event.id); card.remove(); }
      catch (error) { card.append(ui.text("p", error.message, "status-message bad")); }
    });
    card.append(button); region.append(card);
  }
  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await ui.request(`/api/collection-notices?after=${after}`);
      document.getElementById("collection-status-error")?.remove();
      if (currentSession !== data.session) { currentSession = data.session; after = 0; region.replaceChildren(); }
      startupEvent = data.startup_event;
      if (acknowledged !== data.session && !dialog.open) {
        document.getElementById("startup-sync-title").textContent = data.collection_enabled ? "Checking your data sources" : "Offline saved-data mode";
        document.getElementById("startup-sync-message").textContent = data.collection_enabled
          ? "Enabled sources are being checked automatically. Missing dates are replenished in the background, and new records are saved while the app runs. OK only acknowledges this notice; collection does not wait for it."
          : "The application was explicitly started in offline mode. Saved history remains searchable; no network collection is attempted. Start normally to resume automatic catch-up.";
        document.getElementById("startup-sync-sources").replaceChildren(...data.sources.map(source =>
          ui.text("li", `${source.name}: ${source.enabled ? "automatic check and gap catch-up" : source.reason}`)));
        dialog.showModal();
      }
      for (const event of data.events) {
        showEvent(event);
        after = Math.max(after, event.id);
      }
      if (preference && !savingPreference) {
        preference.checked = data.desktop_enabled;
        preference.disabled = !data.desktop_available;
      }
    } catch (error) {
      if (!document.getElementById("collection-status-error")) {
        const message = ui.text("p", `Automatic-update status could not be read: ${error.message}`, "status-message waiting");
        message.id = "collection-status-error"; region.append(message);
      }
    } finally { loading = false; }
  }
  if (preference) preference.addEventListener("change", async () => {
    savingPreference = true;
    try {
      const result = await ui.request("/api/settings/collection-desktop", "POST", { enabled: preference.checked });
      ui.message(document.getElementById("collection-preference-message"), result.message);
    } catch (error) {
      preference.checked = !preference.checked;
      ui.message(document.getElementById("collection-preference-message"), error.message, true);
    } finally { savingPreference = false; }
  });
  load();
  window.setInterval(load, 10000);
})();
