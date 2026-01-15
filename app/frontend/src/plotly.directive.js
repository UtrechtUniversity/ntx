import Alpine from "alpinejs";

document.addEventListener("alpine:init", () => {
  // Centralize Plotly lifecycle management in one directive:
  // - Template only renders DOM and binds figure objects.
  // - Plotly follows via `Plotly.react` (in-place update), no manual Plotly handling in the templates.
  Alpine.directive("plotly", (el, { expression }, { evaluateLater, effect, cleanup }) => {
    const evaluate = evaluateLater(expression);

    let rafId = null;
    const scheduleResize = () => {
      // Avoid resize thrash during rapid layout changes by collapsing calls into
      // a single animation frame.
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
      rafId = requestAnimationFrame(() => {
        if (window.Plotly && window.Plotly.Plots && typeof window.Plotly.Plots.resize === "function") {
          try {
            window.Plotly.Plots.resize(el);
          } catch (err) {}
        }
      });
    };

    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => scheduleResize())
        : null;
    if (ro) {
      // One observer per plot keeps resizing responsive as cards/grid change size.
      ro.observe(el);
    }

    effect(() => {
      evaluate((fig) => {
        if (!fig || !window.Plotly) {
          return;
        }

        // The expression must evaluate to a PlotlyCard:
        // { status:"ok", figure:{data,layout,frames?}, config:{...} }
        if (fig.status && fig.status !== "ok") {
          if (typeof window.Plotly.purge === "function") {
            try {
              window.Plotly.purge(el);
            } catch (err) {}
          }
          return;
        }
        const figure = fig.figure && typeof fig.figure === "object" ? fig.figure : null;
        const data = Array.isArray(figure?.data) ? figure.data : [];
        const layout = figure?.layout && typeof figure.layout === "object" ? figure.layout : {};
        const config = fig.config && typeof fig.config === "object" ? fig.config : {};

        // Plotly.react updates an existing graph div (or mounts on first call).
        window.Plotly.react(el, data, layout, config)
          .then(() => scheduleResize())
          .catch(() => {});
      });
    });

    cleanup(() => {
      if (ro) {
        ro.disconnect();
      }
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
      // Purge removes event handlers and releases memory when Alpine removes the node.
      if (window.Plotly && typeof window.Plotly.purge === "function") {
        try {
          window.Plotly.purge(el);
        } catch (err) {}
      }
    });
  });
});
