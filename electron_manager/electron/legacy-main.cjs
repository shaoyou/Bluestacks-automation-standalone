// Electron 22 starts its main process through CommonJS. Load the existing ESM
// entry point with Node's native dynamic import so it remains usable on Win7.
void import("./main.js").catch((error) => {
  console.error("Unable to start the Electron main process:", error);
  process.exitCode = 1;
});
