import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const srcDir = path.join(root, "src");

const failures = [];

function fail(message) {
  failures.push(message);
}

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist" || entry.name === ".git") continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath, files);
    } else if (/\.(ts|tsx|md)$/.test(entry.name)) {
      files.push(fullPath);
    }
  }
  return files;
}

function relative(filePath) {
  return path.relative(root, filePath).replaceAll("\\", "/");
}

const requiredFiles = [
  "src/game-data/buildings.ts",
  "src/game-data/facilities.ts",
  "src/game-data/job-buildings.ts",
  "src/game-data/job-equipment.ts",
  "src/game-data/job-skills.ts",
  "src/game-data/job-normalization.ts",
  "src/game-data/job-marriage.ts",
  "src/game-data/job-surveys.ts",
  "src/game-data/job-profile.ts",
  "src/game-data/relationship-checks.ts",
  "src/design-system/category-styles.ts",
  "src/app/app-shell.tsx",
  "src/app/global-search.ts",
  "src/app/navigation.ts",
  "src/app/seo.ts",
  "src/app/site-header.tsx",
];

for (const file of requiredFiles) {
  if (!fs.existsSync(path.join(root, file))) {
    fail(`Missing required architecture file: ${file}`);
  }
}

const sourceFiles = walk(srcDir);

for (const file of sourceFiles) {
  const rel = relative(file);
  const text = fs.readFileSync(file, "utf8");

  if (/Indoor slots/i.test(text)) {
    fail(`Found stale house terminology "Indoor slots" in ${rel}`);
  }

  if (/Survey Corps HQ\s*\(Royal\)/i.test(text)) {
    fail(`Found incorrect Survey Corps HQ Royal label in ${rel}`);
  }

  if (/const\s+KNOWN_JOB_SHOPS|export\s+const\s+KNOWN_JOB_SHOPS/.test(text) && rel !== "src/game-data/job-buildings.ts") {
    fail(`Do not define KNOWN_JOB_SHOPS outside src/game-data/job-buildings.ts: ${rel}`);
  }
}

const appText = read("src/App.tsx");
const forbiddenAppPatterns = [
  ["const navSections", "navigation belongs in src/app/navigation.ts"],
  ["const ROUTE_SEO", "SEO metadata belongs in src/app/seo.ts"],
  ["FURNITURE_SEARCH_ROWS", "global search data belongs in src/app/global-search.ts"],
  ["function SiteHeader", "header UI belongs in src/app/site-header.tsx"],
];

for (const [pattern, reason] of forbiddenAppPatterns) {
  if (appText.includes(pattern)) {
    fail(`App.tsx contains "${pattern}"; ${reason}.`);
  }
}

const jobBuildingsText = read("src/game-data/job-buildings.ts");
const expectedSurveyOwners = ["Carpenter", "Farmer", "Merchant", "Mover", "Rancher"];

for (const owner of expectedSurveyOwners) {
  const re = new RegExp(`${owner}:\\s*\\[[^\\]]*Survey Corps HQ \\(Rank B\\+\\)`, "s");
  if (!re.test(jobBuildingsText)) {
    fail(`Survey Corps HQ expected owner missing or wrong rank note: ${owner}`);
  }
}

if (/Royal:\s*\[[^\]]*Survey Corps HQ/s.test(jobBuildingsText)) {
  fail("Royal must not be listed as a Survey Corps HQ owner.");
}

const relationshipChecksText = read("src/game-data/relationship-checks.ts");
for (const owner of expectedSurveyOwners) {
  if (!relationshipChecksText.includes(`"${owner}"`)) {
    fail(`relationship-checks.ts is missing expected Survey Corps HQ owner: ${owner}`);
  }
}

if (failures.length > 0) {
  console.error("Architecture checks failed:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log("Architecture checks passed.");
