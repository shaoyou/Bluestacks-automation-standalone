const base = require("./package.json").build;

const legacyResources = base.extraResources.map((resource) => (
  resource.to === "runtime/windows/x64"
    ? { ...resource, from: "vendor/windows/win7-x64" }
    : resource
));

module.exports = {
  ...base,
  appId: "local.playground.bsmanager.win7legacy",
  productName: "熊熊乐园小助手 Win7兼容版",
  electronVersion: "22.3.27",
  artifactName: "${productName}-${version}-win7-x64.${ext}",
  directories: {
    ...base.directories,
    output: "release/win7-legacy",
  },
  extraResources: legacyResources,
  extraMetadata: {
    main: "dist-electron/legacy-main.cjs",
  },
  // This legacy build intentionally does not share the modern updater feed.
  publish: null,
};
