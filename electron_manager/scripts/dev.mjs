import { spawn } from "node:child_process";
import net from "node:net";
import { fileURLToPath } from "node:url";
import process from "node:process";

const projectDir = fileURLToPath(new URL("..", import.meta.url));
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const startPort = 5173;
const endPort = 5199;

function isPortFree(port) {
  const check = (host) => new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen({ port, host, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
  return Promise.all([check("127.0.0.1"), check("::1")]).then(([ipv4Free, ipv6Free]) => ipv4Free && ipv6Free);
}

async function pickPort() {
  for (let port = startPort; port <= endPort; port += 1) {
    if (await isPortFree(port)) return port;
  }
  throw new Error(`无法找到可用端口 ${startPort}-${endPort}`);
}

function spawnProcess(command, args, extraEnv = {}) {
  return spawn(command, args, {
    cwd: projectDir,
    env: { ...process.env, ...extraEnv },
    stdio: "inherit",
  });
}

const port = await pickPort();
const renderer = spawnProcess(npmCommand, ["run", "dev:renderer", "--", "--port", String(port), "--strictPort"]);
let electron = null;

const waitForRenderer = async () => {
  const urls = [`http://127.0.0.1:${port}`, `http://[::1]:${port}`];
  for (let attempt = 0; attempt < 100; attempt += 1) {
    for (const url of urls) {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (response.ok) return;
      } catch {
        // Try the next address family or next retry.
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Vite 未能在端口 ${port} 启动`);
};

const shutdown = () => {
  renderer.kill("SIGTERM");
  if (electron) electron.kill("SIGTERM");
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

renderer.on("exit", (code) => {
  if (code !== 0) shutdown();
});

await waitForRenderer();

electron = spawnProcess(npmCommand, ["run", "dev:electron"], {
  VITE_DEV_SERVER_URL: `http://localhost:${port}`,
});

electron.on("exit", (code) => {
  shutdown();
});
