// Frontend dev runner:
// Spawns Tailwind and esbuild watch processes and shuts them
// down together so local development stays in sync and exits cleanly.
import { spawn } from "node:child_process";

// Use platform-specific npx entrypoint so the script should work on Windows and Linux
// TODO: only tested on Linux.
const npxCmd = process.platform === "win32" ? "npx.cmd" : "npx";

function spawnCommand(name, args) {
  // Run the tool via npx and stream output directly to this process.
  const child = spawn(npxCmd, args, { stdio: "inherit" });

  child.on("exit", (code) => {
    if (shuttingDown) {
      return;
    }
    // If any child exits, stop the rest and propagate the exit code.
    shuttingDown = true;
    shutdownChildren(name);
    process.exit(code ?? 1);
  });

  return child;
}

// Track lifecycle so we only trigger shutdown once.
let shuttingDown = false;
// Keep handles so we can stop both watchers together.
const children = [];

function shutdownChildren(reason) {
  for (const child of children) {
    if (child && !child.killed) {
      // Ask watchers to terminate gracefully before we exit.
      child.kill("SIGTERM");
    }
  }

  if (reason) {
    // Give reason for why the dev watchers stopped.
    console.log(`Stopped frontend dev processes (${reason}).`);
  }
}

process.on("SIGINT", () => {
  if (shuttingDown) {
    return;
  }
  // Handle Ctrl+C locally so both watchers stop together.
  shuttingDown = true;
  shutdownChildren("SIGINT");
  process.exit(130);
});

process.on("SIGTERM", () => {
  if (shuttingDown) {
    return;
  }
  // Handle termination signals from the OS or parent process.
  shuttingDown = true;
  shutdownChildren("SIGTERM");
  process.exit(0);
});

children.push(
  spawnCommand("tailwind", [
    // Tailwind CLI in watch mode builds the CSS output from the input file.
    "@tailwindcss/cli",
    "-i",
    "./src/input.css",
    "-o",
    "../static/css/tailwind.css",
    "--watch",
  ])
);

children.push(
  spawnCommand("esbuild", [
    // esbuild bundles the app JS for the browser with sourcemaps.
    "esbuild",
    "./src/app.js",
    "--bundle",
    "--sourcemap",
    "--outfile=../static/ntx/app.js",
    "--platform=browser",
    "--watch",
  ])
);
