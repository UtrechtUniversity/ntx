// Copies the Plotly.js bundle from node_modules into the static vendor directory
// so the frontend can load it without a bundler.
import { copyFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolve paths relative to this script so it works regardless of CWD.
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, "..");
// Source Plotly bundle provided by the Plotly distribution package.
const src = path.join(projectRoot, "node_modules", "plotly.js-dist-min", "plotly.min.js");
// Destination folder under static assets served by the app.
const destDir = path.join(projectRoot, "..", "static", "vendor", "plotly");
// Full destination path for the copied bundle.
const dest = path.join(destDir, "plotly.min.js");

// Ensure the vendor directory exists, then copy the bundle into place.
await mkdir(destDir, { recursive: true });
await copyFile(src, dest);

// Confirm the relative output path for quick feedback in scripts/logs.
console.log(`Copied Plotly.js to ${path.relative(projectRoot, dest)}`);
