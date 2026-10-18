"use strict";
window.CleanAirUI = {
  async request(url, method = "GET", body = undefined) {
    const options = { method, headers: { Accept: "application/json" }, signal: AbortSignal.timeout(30000) };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    let result;
    try { result = await response.json(); }
    catch (error) { throw new Error(`The local app returned an unreadable response (${response.status}). Check its console/logs.`); }
    if (!response.ok) throw new Error(result.error?.message || `Local request failed (${response.status}).`);
    return result;
  },
  text(tag, text, className = "") {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  },
  time(value) { return value ? new Date(value * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "Not available yet"; },
  message(element, text, error = false) {
    element.textContent = text;
    element.className = `status-message ${error ? "bad" : "good"}`;
    element.hidden = false;
  },
};
