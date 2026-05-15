#!/usr/bin/env node
import { spawnSync } from "child_process";
import { existsSync, readFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(__dirname, "..");
const repoRoot = resolve(frontendDir, "..");

const expected = {
  repository: "github.com/Naofumi-1000ri/douga",
  projectId: "douga-2f6f8",
  hostingTarget: "douga",
  hostingSite: "douga-2f6f8",
  branch: "main",
};

const args = process.argv.slice(2);
const allowedArgs = new Set(["--dry-run", "--check-only", "--allow-non-main", "--allow-dirty"]);
const unknownArgs = args.filter((arg) => !allowedArgs.has(arg));
if (unknownArgs.length > 0) {
  fail(`unknown argument(s): ${unknownArgs.join(", ")}`);
}

const dryRun = args.includes("--dry-run") || process.env.DRY_RUN === "1";
const checkOnly = args.includes("--check-only");
const allowNonMain =
  dryRun &&
  (args.includes("--allow-non-main") || process.env.ALLOW_NON_MAIN_DEPLOY === "1");
const allowDirty =
  dryRun &&
  (args.includes("--allow-dirty") || process.env.ALLOW_DIRTY_DEPLOY === "1");

if (checkOnly && !dryRun) {
  fail("--check-only requires --dry-run");
}

function fail(message) {
  console.error(`deploy-production: ${message}`);
  process.exit(1);
}

function run(command, commandArgs, options = {}) {
  const result = spawnSync(command, commandArgs, {
    cwd: options.cwd ?? frontendDir,
    encoding: "utf-8",
    stdio: options.capture ? "pipe" : "inherit",
  });

  if (result.error) {
    fail(`failed to run ${command}: ${result.error.message}`);
  }

  if (result.status !== 0) {
    const detail = options.capture ? result.stderr.trim() || result.stdout.trim() : "";
    fail(`${command} ${commandArgs.join(" ")} failed${detail ? `: ${detail}` : ""}`);
  }

  return options.capture ? result.stdout.trim() : "";
}

function git(commandArgs) {
  return run("git", commandArgs, { cwd: repoRoot, capture: true });
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch (error) {
    fail(`failed to read ${path}: ${error.message}`);
  }
}

function normalizeRemote(remoteUrl) {
  return remoteUrl
    .replace(/^git@github\.com:/, "https://github.com/")
    .replace(/^https:\/\/github\.com\//, "github.com/")
    .replace(/\.git$/, "");
}

function assertEqual(name, actual, expectedValue) {
  if (actual !== expectedValue) {
    fail(`${name} mismatch: expected ${expectedValue}, got ${actual || "<empty>"}`);
  }
}

function verifyRepo() {
  const topLevel = git(["rev-parse", "--show-toplevel"]);
  assertEqual("git top-level", topLevel, repoRoot);

  const remote = normalizeRemote(git(["remote", "get-url", "origin"]));
  assertEqual("git remote", remote, expected.repository);

  const branch = git(["branch", "--show-current"]);
  if (branch !== expected.branch && !allowNonMain) {
    fail(
      `branch mismatch: expected ${expected.branch}, got ${branch || "<detached>"}. ` +
        "Use --dry-run --allow-non-main only for local guard verification.",
    );
  }

  const status = git(["status", "--porcelain"]);
  if (status && !allowDirty) {
    fail("working tree is dirty. Commit or discard changes before production deploy.");
  }

  if (branch === expected.branch) {
    git(["fetch", "origin", "main"]);
    const head = git(["rev-parse", "HEAD"]);
    const originMain = git(["rev-parse", "origin/main"]);
    assertEqual("HEAD vs origin/main", head, originMain);
  }
}

function verifyFirebaseConfig() {
  const firebaseRcPath = resolve(frontendDir, ".firebaserc");
  const firebaseJsonPath = resolve(frontendDir, "firebase.json");

  if (!existsSync(firebaseRcPath)) {
    fail(".firebaserc is missing");
  }

  if (!existsSync(firebaseJsonPath)) {
    fail("firebase.json is missing");
  }

  const firebaseRc = readJson(firebaseRcPath);
  const firebaseJson = readJson(firebaseJsonPath);

  assertEqual("Firebase default project", firebaseRc.projects?.default, expected.projectId);
  assertEqual("Firebase hosting target", firebaseJson.hosting?.target, expected.hostingTarget);
  assertEqual("Firebase hosting public directory", firebaseJson.hosting?.public, "dist");

  const configuredSites =
    firebaseRc.targets?.[expected.projectId]?.hosting?.[expected.hostingTarget] ?? [];
  if (
    !Array.isArray(configuredSites) ||
    configuredSites.length !== 1 ||
    configuredSites[0] !== expected.hostingSite
  ) {
    fail(
      `Firebase hosting target ${expected.hostingTarget} must map only to site ` +
        `${expected.hostingSite} in .firebaserc`,
    );
  }

  const predeploy = firebaseJson.hosting?.predeploy ?? [];
  if (!predeploy.includes("npm run build") || !predeploy.includes("node scripts/verify-build.js")) {
    fail("firebase.json hosting.predeploy must run build and scripts/verify-build.js");
  }
}

function runBuildVerification() {
  run("npm", ["run", "build"]);
  run("node", ["scripts/verify-build.js"]);
}

function deploy() {
  const deployArgs = [
    "deploy",
    "--only",
    `hosting:${expected.hostingTarget}`,
    "--project",
    expected.projectId,
  ];

  if (dryRun) {
    console.log(`deploy-production: dry run, not executing: firebase ${deployArgs.join(" ")}`);
    return;
  }

  run("firebase", deployArgs);
}

verifyRepo();
verifyFirebaseConfig();
if (checkOnly) {
  console.log("deploy-production: check-only OK");
  process.exit(0);
}
runBuildVerification();
deploy();
