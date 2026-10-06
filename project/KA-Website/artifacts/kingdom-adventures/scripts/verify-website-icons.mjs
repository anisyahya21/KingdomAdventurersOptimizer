import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const kaRoot = path.resolve(projectRoot, "..", "..");

const sourceIconsDir = path.join(kaRoot, "website_icons");
const publicIconsDir = path.join(projectRoot, "public", "website_icons");
const sourcePreview = path.join(sourceIconsDir, "preview.html");
const publicPreview = path.join(publicIconsDir, "preview.html");
const sourceManifestPath = path.join(sourceIconsDir, "manifest.json");
const sourceFacilitiesManifestPath = path.join(sourceIconsDir, "facilities_confirmed", "manifest.json");
const publicFacilitiesManifestPath = path.join(publicIconsDir, "facilities_confirmed", "manifest.json");
const viteConfigPath = path.join(projectRoot, "vite.config.ts");

function fail(message) {
  console.error(`ICON FAIL-SAFE: ${message}`);
  process.exit(1);
}

function readText(filePath) {
  if (!fs.existsSync(filePath)) {
    fail(`Missing required file: ${filePath}`);
  }
  return fs.readFileSync(filePath, "utf8");
}

function readJson(filePath) {
  try {
    return JSON.parse(readText(filePath));
  } catch (error) {
    fail(`Invalid JSON in ${filePath}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function resolveIconRelativePath(category, entry) {
  if (entry.path) {
    return entry.path;
  }

  const filename = entry.filename;
  if (!filename) {
    return null;
  }

  if (filename.includes("/")) {
    return filename;
  }

  if (category === "facilities") {
    return `facilities_confirmed/${filename}`;
  }

  return `${category}/${filename}`;
}

function verifyManifestFiles(manifest, iconsDir) {
  const categories = ["items", "equipment", "eggs", "attributes", "gender", "furniture", "facilities"];
  if (Array.isArray(manifest.requested)) {
    categories.push("requested");
  }

  for (const category of categories) {
    const entries = Array.isArray(manifest[category]) ? manifest[category] : [];
    for (const entry of entries) {
      const relPath = resolveIconRelativePath(category, entry);
      if (!relPath) {
        fail(`Missing filename/path for category '${category}' entry id=${entry.id ?? "?"}`);
      }
      const absolutePath = path.join(iconsDir, relPath);
      if (!fs.existsSync(absolutePath)) {
        fail(`Missing icon file: ${absolutePath}`);
      }

      if (Array.isArray(entry.variants)) {
        for (const variant of entry.variants) {
          if (!variant?.filename) continue;
          const variantPath = path.join(iconsDir, category, variant.filename);
          if (!fs.existsSync(variantPath)) {
            fail(`Missing variant icon file: ${variantPath}`);
          }
        }
      }
    }
  }
}

function verifyPreviewSections(previewHtml) {
  const requiredHeadings = [
    "Items",
    "Equipment",
    "Eggs",
    "Field Attributes",
    "Gender",
    "Furniture",
    "Facilities (Confirmed)",
    "Mapchips",
    "Linked Facilities",
  ];

  for (const heading of requiredHeadings) {
    if (!previewHtml.includes(`<h2>${heading} (`)) {
      fail(`Preview is missing section heading: ${heading}`);
    }
  }
}

function verifyNoWebsiteIconsProxy(viteConfig) {
  const proxyBlockMatch = viteConfig.match(/proxy\s*:\s*\{[\s\S]*?\}/m);
  const inProxyBlock = proxyBlockMatch ? proxyBlockMatch[0] : viteConfig;
  if (inProxyBlock.includes("/website_icons")) {
    fail("vite.config.ts still proxies /website_icons. Static icons must never be proxied.");
  }
}

function verifyPreviewSync(sourceHtml, publicHtml) {
  if (sourceHtml !== publicHtml) {
    fail(
      "website_icons/preview.html and public/website_icons/preview.html differ. Regenerate via tools/asset_extractor/list_all_icons.py"
    );
  }
}

function verifyLinkedFacilitiesManifest(sourceManifest, publicManifest) {
  const sourceIcons = Array.isArray(sourceManifest.icons) ? sourceManifest.icons.length : 0;
  const publicIcons = Array.isArray(publicManifest.icons) ? publicManifest.icons.length : 0;
  if (sourceIcons !== publicIcons) {
    fail(`facilities_confirmed manifest count mismatch: source=${sourceIcons}, public=${publicIcons}`);
  }
}

function main() {
  const sourceManifest = readJson(sourceManifestPath);
  const sourceFacilitiesManifest = readJson(sourceFacilitiesManifestPath);
  const publicFacilitiesManifest = readJson(publicFacilitiesManifestPath);
  const sourcePreviewHtml = readText(sourcePreview);
  const publicPreviewHtml = readText(publicPreview);
  const viteConfig = readText(viteConfigPath);

  verifyNoWebsiteIconsProxy(viteConfig);
  verifyManifestFiles(sourceManifest, sourceIconsDir);
  verifyManifestFiles(sourceManifest, publicIconsDir);
  verifyPreviewSections(sourcePreviewHtml);
  verifyPreviewSync(sourcePreviewHtml, publicPreviewHtml);
  verifyLinkedFacilitiesManifest(sourceFacilitiesManifest, publicFacilitiesManifest);

  const assembled = readJson(path.join(sourceIconsDir, "facilities_assembled/manifest.json"));
  const publicAssembled = readJson(path.join(publicIconsDir, "facilities_assembled/manifest.json"));
  if (JSON.stringify(assembled) !== JSON.stringify(publicAssembled)) {
    fail("Assembled facility manifests differ between source and public.");
  }
  const assembledIds = new Set();
  for (const icon of assembled.icons) {
    if (assembledIds.has(icon.id)) fail(`Duplicate assembled facility id: ${icon.id}`);
    assembledIds.add(icon.id);
    const relative = path.join("facilities_assembled", icon.filename);
    const sourcePath = path.join(sourceIconsDir, relative);
    const publicPath = path.join(publicIconsDir, relative);
    if (!fs.existsSync(sourcePath) || !fs.existsSync(publicPath)) fail(`Missing assembled icon: ${relative}`);
    if (!fs.readFileSync(sourcePath).equals(fs.readFileSync(publicPath))) fail(`Assembled icon copies differ: ${relative}`);
  }

  console.log("ICON FAIL-SAFE: PASS");
}

main();
