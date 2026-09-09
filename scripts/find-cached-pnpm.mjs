#!/usr/bin/env node

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

const packageManager = process.argv[2] ?? "";
const match = /^pnpm@([^@]+)$/.exec(packageManager);
if (!match) {
  process.exit(1);
}

const expectedVersion = match[1];
const cacheRoots = [];
const configuredCache =
  process.env.npm_config_cache ?? process.env.NPM_CONFIG_CACHE;
if (configuredCache) {
  cacheRoots.push(resolve(configuredCache));
} else if (process.platform === "win32" && process.env.LOCALAPPDATA) {
  cacheRoots.push(join(process.env.LOCALAPPDATA, "npm-cache"));
} else {
  cacheRoots.push(join(homedir(), ".npm"));
}

for (const cacheRoot of new Set(cacheRoots)) {
  const execCache = join(cacheRoot, "_npx");
  if (!existsSync(execCache)) {
    continue;
  }

  for (const entry of readdirSync(execCache, { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }

    const packageRoot = join(execCache, entry.name, "node_modules", "pnpm");
    const packageJsonPath = join(packageRoot, "package.json");
    if (!existsSync(packageJsonPath)) {
      continue;
    }

    try {
      const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8"));
      if (packageJson.version !== expectedVersion) {
        continue;
      }
      const relativeBin =
        typeof packageJson.bin === "string"
          ? packageJson.bin
          : packageJson.bin?.pnpm;
      if (typeof relativeBin !== "string") {
        continue;
      }
      const entrypoint = resolve(packageRoot, relativeBin);
      if (existsSync(entrypoint)) {
        process.stdout.write(entrypoint);
        process.exit(0);
      }
    } catch {
      // Ignore incomplete cache entries and continue looking for an exact match.
    }
  }
}

process.exit(1);
