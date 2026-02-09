#!/usr/bin/env node
/**
 * verify-build.js
 *
 * Firebase Hosting predeploy hook.
 * Verifies that the built bundle contains the correct Firebase API key
 * from .env.production, preventing stale dist/ deployments.
 */
import { readFileSync, readdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendDir = join(__dirname, "..");

// 1. Read expected key from .env.production
const envPath = join(frontendDir, ".env.production");
let envContent;
try {
  envContent = readFileSync(envPath, "utf-8");
} catch {
  console.error("ERROR: .env.production not found. Cannot verify build.");
  process.exit(1);
}

const expectedKey = envContent
  .split("\n")
  .find((line) => line.startsWith("VITE_FIREBASE_API_KEY="))
  ?.split("=")[1]
  ?.trim();

if (!expectedKey) {
  console.error("ERROR: VITE_FIREBASE_API_KEY not found in .env.production");
  process.exit(1);
}

// 2. Find the built JS bundle
const distAssetsDir = join(frontendDir, "dist", "assets");
let jsFiles;
try {
  jsFiles = readdirSync(distAssetsDir).filter((f) => f.endsWith(".js"));
} catch {
  console.error("ERROR: dist/assets/ not found. Run npm run build first.");
  process.exit(1);
}

// 3. Check each JS file for the API key
let foundKey = null;
for (const file of jsFiles) {
  const content = readFileSync(join(distAssetsDir, file), "utf-8");
  const match = content.match(/AIzaSy[A-Za-z0-9_-]{30,40}/g);
  if (match) {
    foundKey = match[0];
    break;
  }
}

if (!foundKey) {
  console.error("ERROR: No Firebase API key found in built bundle.");
  process.exit(1);
}

// 4. Compare
if (foundKey !== expectedKey) {
  console.error("========================================");
  console.error("DEPLOY BLOCKED: Firebase API key mismatch!");
  console.error(`  .env.production: ${expectedKey}`);
  console.error(`  dist/ bundle:    ${foundKey}`);
  console.error("  The build may be stale. Run: npm run build");
  console.error("========================================");
  process.exit(1);
}

console.log(`verify-build: OK (API key matches .env.production)`);
