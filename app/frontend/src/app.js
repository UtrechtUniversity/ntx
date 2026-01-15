// Frontend entrypoint: registers Alpine components and Plotly helpers to power
// the report UI, then starts Alpine on the page.
// Alpine provides lightweight reactivity for the report view.
import Alpine from "alpinejs";
// Side-effect import: register the global `x-plotly` directive once.
// Keeping this separate avoids per-component Plotly lifecycle code;
// prevents render/clear actions being needed across the app.
import "./plotly.directive.js";

// Plotly is loaded as a static vendor script (not bundled), so validate it
// before we try to render charts and show an error if missing.
function hasPlotly() {
  return (
    typeof window !== "undefined" &&
    window.Plotly &&
    typeof window.Plotly.react === "function" &&
    window.Plotly.Plots &&
    typeof window.Plotly.Plots.resize === "function"
  );
}

// Alpine component factory for the report page; provides state + actions.
function projectReport(options = {}) {
  // Default report parameters if the page does not specify any.
  const defaultParams = ["number_of_spikes", "isi_coefficient_of_variation"];

  // Component state and methods that Alpine binds to the report view.
  return {
    apiUrl: options.apiUrl, // Base API endpoint injected by the template.
    params:
      Array.isArray(options.params) && options.params.length > 0
        ? options.params
        : defaultParams, // Parameters sent to the report API.
    cards: [], // Plot card data returned by the API.
    loading: false, // UI flag for showing the loading state.
    requestId: 0, // Counter for ignoring stale responses.
    warnings: [], // Non-blocking warnings to show to the user.
    errorMessage: "", // User-facing error message to display.

    init() {
      // Fail fast if the component is misconfigured.
      if (!this.apiUrl) {
        this.errorMessage = "Missing report API URL.";
        return;
      }

      // Load the report once the component initializes.
      this.load();
    },

    setError(message) {
      // Centralized error setter so callers can keep logic concise.
      this.errorMessage = message;
    },

    clearMessages() {
      // Reset per-request messages when starting a new request.
      this.warnings = [];
      this.errorMessage = "";
    },

    buildUrl() {
      // Build the report URL with the chosen parameter list.
      const url = new URL(this.apiUrl, window.location.origin);
      const paramList =
        Array.isArray(this.params) && this.params.length > 0 ? this.params : defaultParams;
      url.searchParams.set("params", paramList.join(","));
      return url;
    },

    async load() {
      // Track this request so slow responses do not overwrite newer state.
      const requestId = ++this.requestId;
      this.loading = true;
      this.clearMessages();

      // Ensure loading is cleared only for the latest pending request.
      const finish = () => {
        if (requestId === this.requestId) {
          this.loading = false;
        }
      };

      if (!hasPlotly()) {
        // Plotly dependency is required for rendering charts.
        this.setError(
          "Plotly.js not loaded. Run `npm install` + `npm run dev` in `app/frontend`."
        );
        finish();
        return;
      }

      // Fetch the report data from the backend API.
      const url = this.buildUrl();

      let response;
      try {
        response = await fetch(url.toString(), { headers: { Accept: "application/json" } });
      } catch (err) {
        // Network-level errors should not clear newer requests.
        if (requestId === this.requestId) {
          this.setError("Failed to fetch report JSON.");
        }
        finish();
        return;
      }

      let payload;
      try {
        payload = await response.json();
      } catch (err) {
        // Guard against non-JSON responses while keeping UI state consistent.
        if (requestId === this.requestId) {
          this.setError("Report response was not valid JSON.");
        }
        finish();
        return;
      }

      if (requestId !== this.requestId) {
        // Ignore stale responses if a newer request has started.
        finish();
        return;
      }

      if (!response.ok) {
        // Prefer API-provided errors but keep a fallback message.
        this.setError(payload.error || "Report request failed.");
        finish();
        return;
      }

      // Normalize response payload so the template can render safely.
      this.warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      this.cards = Array.isArray(payload.cards) ? payload.cards : [];
      if (!this.cards.length) {
        // Let users know the API returned an empty report.
        this.setError("No plots available in the report payload.");
      }
      finish();
    },
  };
}

// Register the Alpine component factory for use in templates.
Alpine.data("projectReport", projectReport);

// Expose Alpine for debugging and extensions in the browser console.
window.Alpine = Alpine;

// Start Alpine once the script loads to hydrate the page.
Alpine.start();
