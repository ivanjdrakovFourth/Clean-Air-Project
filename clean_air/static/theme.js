"use strict";
(() => {
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let saved = null;
  try { saved = localStorage.getItem("clean-air-theme"); }
  catch (error) { console.warn("Theme preference cannot be read in this browser:", error.name); }
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.setAttribute("aria-pressed", String(theme === "dark"));
      button.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
      button.title = button.getAttribute("aria-label");
    }
  };
  apply(saved === "dark" || saved === "light" ? saved : system.matches ? "dark" : "light");
  document.addEventListener("DOMContentLoaded", () => {
    apply(document.documentElement.dataset.theme);
    const button = document.getElementById("theme-toggle");
    if (button) button.addEventListener("click", () => {
      const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      apply(theme);
      try { localStorage.setItem("clean-air-theme", theme); }
      catch (error) {
        console.warn("Theme changed but the preference could not be saved:", error.name);
        button.title = "Theme changed for this page; browser storage is unavailable.";
      }
    });
  });
})();
