#!/usr/bin/env node

import {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const expectedRegistry = "https://packagefeedproxy.microsoft.io/npm/";
const scriptDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDir, "..");

function fail(message) {
  throw new Error(message);
}

function parseKeyValueFile(path) {
  const result = new Map();
  for (const rawLine of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const separator = line.indexOf("=");
    result.set(line.slice(0, separator).trim(), line.slice(separator + 1).trim());
  }
  return result;
}

const npmrc = parseKeyValueFile(join(projectRoot, ".npmrc"));
if (npmrc.get("registry") !== expectedRegistry) {
  fail(".npmrc must use the managed Microsoft registry");
}
if (npmrc.get("replace-registry-host") !== "always") {
  fail(".npmrc must rewrite registry hosts to the managed Microsoft registry");
}

const corepackEnv = parseKeyValueFile(join(projectRoot, ".corepack.env"));
if (corepackEnv.get("COREPACK_ENABLE_NETWORK") !== "0") {
  fail(".corepack.env must keep bare Corepack offline");
}
if (`${corepackEnv.get("COREPACK_NPM_REGISTRY")}/` !== expectedRegistry) {
  fail(".corepack.env must identify the managed Microsoft registry");
}

const packageJson = JSON.parse(
  readFileSync(join(projectRoot, "package.json"), "utf8"),
);
const packageManagerMatch = /^pnpm@([^@]+)$/.exec(packageJson.packageManager ?? "");
if (!packageManagerMatch) {
  fail("package.json must pin pnpm with packageManager");
}

for (const [scriptName, command] of Object.entries(packageJson.scripts ?? {})) {
  const withoutPnpm =
    typeof command === "string" ? command.replaceAll("pnpm run", "") : "";
  if (withoutPnpm.includes("npm run")) {
    fail(`${scriptName} must compose project scripts through pnpm, not bare npm`);
  }
}

for (const wrapperName of ["pnpm-project.sh", "pnpm-project.ps1"]) {
  const wrapper = readFileSync(join(scriptDir, wrapperName), "utf8");
  if (!wrapper.includes("find-cached-pnpm.mjs")) {
    fail(`${wrapperName} must resolve the cached pnpm without npm`);
  }
  if (
    !wrapper.includes("--registry=") ||
    !wrapper.includes("--replace-registry-host=always")
  ) {
    fail(`${wrapperName} must pin the online fallback to the managed registry`);
  }
}

const temporaryRoot = mkdtempSync(join(tmpdir(), "managed-pnpm-test-"));
try {
  const packageRoot = join(
    temporaryRoot,
    "cache",
    "_npx",
    "fixture",
    "node_modules",
    "pnpm",
  );
  mkdirSync(join(packageRoot, "bin"), { recursive: true });
  writeFileSync(
    join(packageRoot, "package.json"),
    JSON.stringify({
      name: "pnpm",
      version: packageManagerMatch[1],
      bin: { pnpm: "bin/pnpm.cjs" },
    }),
  );
  writeFileSync(
    join(packageRoot, "bin", "pnpm.cjs"),
    'process.stdout.write(`CACHE_ONLY:${process.argv.slice(2).join(" ")}`);\n',
  );

  const fakeBin = join(temporaryRoot, "bin");
  mkdirSync(fakeBin);
  const fakeNpm = join(fakeBin, "npm");
  writeFileSync(fakeNpm, "#!/usr/bin/env sh\nexit 97\n");
  chmodSync(fakeNpm, 0o755);

  const result = spawnSync(
    "bash",
    [join(scriptDir, "pnpm-project.sh"), "--version-sentinel"],
    {
      cwd: projectRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        npm_config_cache: join(temporaryRoot, "cache"),
        PATH: `${fakeBin}:${process.env.PATH ?? ""}`,
      },
    },
  );
  if (result.status !== 0) {
    fail(`cached wrapper probe failed: ${result.stderr.trim()}`);
  }
  if (result.stdout !== "CACHE_ONLY:--version-sentinel") {
    fail("cached wrapper probe did not use the exact cached pnpm entrypoint");
  }

  const emptyCache = join(temporaryRoot, "empty-cache");
  mkdirSync(emptyCache);
  writeFileSync(
    fakeNpm,
    [
      "#!/usr/bin/env sh",
      'printf "FALLBACK_ARGS:%s\\n" "$*"',
      'printf "FALLBACK_REGISTRY:%s\\n" "$npm_config_registry"',
      'printf "FALLBACK_REPLACE:%s\\n" "$npm_config_replace_registry_host"',
      "",
    ].join("\n"),
  );
  const fallback = spawnSync(
    "bash",
    [join(scriptDir, "pnpm-project.sh"), "--fallback-sentinel"],
    {
      cwd: projectRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        npm_config_cache: emptyCache,
        PATH: `${fakeBin}:${process.env.PATH ?? ""}`,
      },
    },
  );
  if (fallback.status !== 0) {
    fail(`managed fallback probe failed: ${fallback.stderr.trim()}`);
  }
  if (
    !fallback.stdout.includes(`--registry=${expectedRegistry}`) ||
    !fallback.stdout.includes("--replace-registry-host=always") ||
    !fallback.stdout.includes(`FALLBACK_REGISTRY:${expectedRegistry}`) ||
    !fallback.stdout.includes("FALLBACK_REPLACE:always")
  ) {
    fail("online fallback was not pinned to the managed Microsoft registry");
  }
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}

process.stdout.write("managed Node registry configuration: ok\n");
