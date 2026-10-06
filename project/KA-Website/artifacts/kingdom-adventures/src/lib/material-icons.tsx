// Material icon component — style variants for preview/selection
// Materials: 0=Grass, 1=Wood, 2=Food, 3=Ore, 4=Mystic Ore
import type { ReactElement } from "react";
import { getItemIcon } from "@/lib/equipment-icons";

export const MATERIAL_NAMES: Record<number, string> = {
  0: "Grass",
  1: "Wood",
  2: "Food",
  3: "Ore",
  4: "Mystic Ore",
};

// ── Style A: Pixel Art ────────────────────────────────────────────────────────

function PixelGrass({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} style={{ imageRendering: "pixelated" }}>
      <rect x="7" y="1" width="2" height="2" fill="#aed581" />
      <rect x="5" y="2" width="6" height="2" fill="#8bc34a" />
      <rect x="4" y="3" width="2" height="3" fill="#4caf50" />
      <rect x="6" y="2" width="4" height="5" fill="#4caf50" />
      <rect x="10" y="3" width="2" height="3" fill="#4caf50" />
      <rect x="5" y="6" width="6" height="3" fill="#388e3c" />
      <rect x="6" y="4" width="2" height="2" fill="#aed581" />
      <rect x="9" y="3" width="2" height="2" fill="#c5e1a5" />
      <rect x="7" y="9" width="2" height="6" fill="#5d4037" />
      <rect x="6" y="11" width="4" height="2" fill="#5d4037" />
    </svg>
  );
}
function PixelWood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} style={{ imageRendering: "pixelated" }}>
      <rect x="1" y="5" width="14" height="6" fill="#8b5e2e" />
      <rect x="2" y="5" width="12" height="2" fill="#c4884a" />
      <rect x="1" y="10" width="14" height="1" fill="#4a2c0a" />
      <rect x="1" y="5" width="2" height="6" fill="#6b3e1e" />
      <rect x="13" y="5" width="2" height="6" fill="#6b3e1e" />
      <rect x="2" y="6" width="1" height="2" fill="#e0a060" />
      <rect x="4" y="8" width="8" height="1" fill="#7a4a1e" />
    </svg>
  );
}
function PixelFood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} style={{ imageRendering: "pixelated" }}>
      <rect x="3" y="5" width="3" height="6" fill="#e03020" />
      <rect x="10" y="6" width="3" height="5" fill="#38a030" />
      <rect x="5" y="4" width="6" height="7" fill="#f0b020" />
      <rect x="5" y="4" width="2" height="2" fill="#ffe060" />
      <rect x="3" y="5" width="2" height="2" fill="#ff5040" />
      <rect x="2" y="10" width="12" height="3" fill="#c47820" />
      <rect x="1" y="11" width="14" height="2" fill="#a05610" />
    </svg>
  );
}
function PixelOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} style={{ imageRendering: "pixelated" }}>
      <rect x="4" y="2" width="8" height="12" fill="#808080" />
      <rect x="2" y="4" width="12" height="8" fill="#808080" />
      <rect x="5" y="3" width="3" height="3" fill="#c8c8c8" />
      <rect x="4" y="4" width="2" height="2" fill="#e8e8e8" />
      <rect x="10" y="10" width="3" height="3" fill="#484848" />
      <rect x="9" y="11" width="4" height="2" fill="#484848" />
    </svg>
  );
}
function PixelMysticOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} style={{ imageRendering: "pixelated" }}>
      <rect x="5" y="1" width="6" height="2" fill="#d44444" />
      <rect x="3" y="3" width="10" height="2" fill="#ee3333" />
      <rect x="2" y="5" width="12" height="4" fill="#cc1111" />
      <rect x="3" y="9" width="10" height="2" fill="#aa0000" />
      <rect x="5" y="11" width="6" height="2" fill="#880000" />
      <rect x="7" y="13" width="2" height="2" fill="#660000" />
      <rect x="6" y="3" width="4" height="2" fill="#ff7777" />
      <rect x="5" y="5" width="3" height="2" fill="#ff5555" />
    </svg>
  );
}

// ── Style B1: Flat (smooth layered shapes) ────────────────────────────────────

function FlatGrass({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <ellipse cx="12" cy="9" rx="6" ry="8" fill="#66bb6a" />
      <ellipse cx="11" cy="8" rx="3" ry="4" fill="#a5d6a7" opacity="0.7" />
      <line x1="12" y1="10" x2="12" y2="5" stroke="#2e7d32" strokeWidth="1.5" />
      <rect x="11" y="16" width="2" height="6" rx="1" fill="#5d4037" />
    </svg>
  );
}
function FlatWood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <rect x="2" y="7" width="20" height="10" rx="3" fill="#a0632e" />
      <rect x="3" y="8" width="18" height="3" rx="1" fill="#d4944a" opacity="0.65" />
      <rect x="3" y="14" width="18" height="1" rx="0.5" fill="#5a2e10" opacity="0.5" />
      <rect x="2" y="7" width="5" height="10" rx="2" fill="#7a4820" />
      <ellipse cx="4.5" cy="12" rx="1.5" ry="2" fill="#d4944a" opacity="0.5" />
    </svg>
  );
}
function FlatFood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <path d="M4 15 Q4 20 12 20 Q20 20 20 15 L18 12 Q16 10 12 10 Q8 10 6 12 Z" fill="#c07818" />
      <ellipse cx="12" cy="12" rx="7" ry="3" fill="#e89030" />
      <circle cx="9" cy="10" r="3" fill="#e03030" />
      <circle cx="15" cy="10" r="3" fill="#3aa838" />
      <circle cx="12" cy="9" r="3" fill="#f0b820" />
      <ellipse cx="10.5" cy="8.5" rx="1" ry="0.7" fill="#ffe050" opacity="0.8" />
    </svg>
  );
}
function FlatOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <circle cx="12" cy="12" r="9" fill="#9a9a9a" />
      <ellipse cx="9" cy="9" rx="3" ry="2" fill="#d8d8d8" transform="rotate(-15 9 9)" />
      <ellipse cx="8.5" cy="8.5" rx="1.5" ry="1" fill="#f0f0f0" transform="rotate(-15 8.5 8.5)" />
      <path d="M14 15 Q17 13.5 16 17" stroke="#555" strokeWidth="1.5" fill="none" strokeLinecap="round" />
    </svg>
  );
}
function FlatMysticOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="12,2 20,9 12,22 4,9" fill="#c41818" />
      <polygon points="12,2 20,9 12,14 4,9" fill="#e03030" />
      <polygon points="12,2 15.5,9 12,14 8.5,9" fill="#ff5555" />
      <polygon points="8.5,9 4,9 12,2" fill="#a00a0a" />
      <polygon points="15.5,9 20,9 12,2" fill="#cc2020" />
    </svg>
  );
}

// ── Style B2: Outlined (stroke line art, minimal fill) ────────────────────────

function OutlinedGrass({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="#2e7d32" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round">
      <path d="M12 20 L12 10" />
      <path d="M12 10 C12 10 8 8 7 4 C10 5 12 8 12 10" fill="#c8e6c9" stroke="#2e7d32" />
      <path d="M12 12 C12 12 16 9 17 5 C14 6.5 12 10 12 12" fill="#a5d6a7" stroke="#388e3c" />
      <path d="M12 14 C12 14 9 11 6 12 C8 14 12 14 12 14" fill="#dcedc8" stroke="#33691e" />
    </svg>
  );
}
function OutlinedWood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinejoin="round" strokeLinecap="round">
      <rect x="3" y="8" width="18" height="8" rx="2" fill="#ffe0b2" stroke="#bf360c" strokeWidth="1.5" />
      <ellipse cx="7" cy="12" rx="2.5" ry="4" fill="none" stroke="#e65100" strokeWidth="1.2" />
      <line x1="10" y1="9" x2="10" y2="15" stroke="#bf360c" strokeWidth="0.8" opacity="0.5" />
      <line x1="13" y1="9" x2="13" y2="15" stroke="#bf360c" strokeWidth="0.8" opacity="0.5" />
      <ellipse cx="6" cy="11.5" rx="1" ry="1.5" fill="#ffccbc" stroke="none" />
    </svg>
  );
}
function OutlinedFood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 17 Q4 21 12 21 Q20 21 20 17 L20 16 Q17 14 12 14 Q7 14 4 16 Z" fill="#fff9c4" stroke="#f57f17" strokeWidth="1.4" />
      <circle cx="9" cy="11" r="3.5" fill="#ffebee" stroke="#c62828" strokeWidth="1.4" />
      <circle cx="15" cy="11" r="3.5" fill="#e8f5e9" stroke="#2e7d32" strokeWidth="1.4" />
      <circle cx="12" cy="9.5" r="3.5" fill="#fff8e1" stroke="#f9a825" strokeWidth="1.4" />
    </svg>
  );
}
function OutlinedOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2 L18 7 L20 14 L15 20 L9 20 L4 14 L6 7 Z" fill="#eceff1" stroke="#546e7a" strokeWidth="1.5" />
      <path d="M12 2 L18 7 L12 10 L6 7 Z" fill="#cfd8dc" stroke="#546e7a" strokeWidth="1" />
      <path d="M8 7 L9 13" stroke="#90a4ae" strokeWidth="1" />
      <path d="M14 5 L16 10" stroke="#90a4ae" strokeWidth="1" />
    </svg>
  );
}
function OutlinedMysticOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 1 L16 8 L22 9 L17 14 L18 21 L12 18 L6 21 L7 14 L2 9 L8 8 Z" fill="#fce4ec" stroke="#ad1457" strokeWidth="1.5" />
      <path d="M12 1 L16 8 L12 10 L8 8 Z" fill="#f8bbd0" stroke="#c2185b" strokeWidth="1" />
      <circle cx="12" cy="12" r="2" fill="#f48fb1" stroke="none" />
    </svg>
  );
}

// ── Style B3: Crystal (faceted gem polygons) ──────────────────────────────────

function CrystalGrass({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="12,2 16,8 14,15 10,15 8,8" fill="#81c784" />
      <polygon points="12,2 16,8 12,10" fill="#c8e6c9" />
      <polygon points="12,2 8,8 12,10" fill="#a5d6a7" />
      <polygon points="8,8 10,15 12,10" fill="#66bb6a" />
      <polygon points="16,8 14,15 12,10" fill="#43a047" />
      <rect x="11" y="15" width="2" height="7" rx="1" fill="#5d4037" />
    </svg>
  );
}
function CrystalWood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="3,10 7,6 17,6 21,10 21,14 17,18 7,18 3,14" fill="#a0522d" />
      <polygon points="3,10 7,6 7,18 3,14" fill="#8b4513" />
      <polygon points="7,6 17,6 17,10 7,10" fill="#deb887" />
      <polygon points="3,10 17,10 21,10 12,8" fill="#f5deb3" opacity="0.6" />
    </svg>
  );
}
function CrystalFood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="12,2 18,8 20,16 12,22 4,16 6,8" fill="#ffa726" />
      <polygon points="12,2 18,8 12,12" fill="#ffe082" />
      <polygon points="12,2 6,8 12,12" fill="#ffcc02" />
      <polygon points="6,8 4,16 12,12" fill="#fb8c00" />
      <polygon points="18,8 20,16 12,12" fill="#ef6c00" />
      <polygon points="4,16 12,22 12,16" fill="#e65100" />
      <polygon points="20,16 12,22 12,16" fill="#bf360c" />
    </svg>
  );
}
function CrystalOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="12,3 19,8 19,16 12,21 5,16 5,8" fill="#90a4ae" />
      <polygon points="12,3 19,8 12,12" fill="#eceff1" />
      <polygon points="12,3 5,8 12,12" fill="#cfd8dc" />
      <polygon points="5,8 5,16 12,12" fill="#78909c" />
      <polygon points="19,8 19,16 12,12" fill="#607d8b" />
      <polygon points="5,16 12,21 12,16" fill="#546e7a" />
      <polygon points="19,16 12,21 12,16" fill="#455a64" />
    </svg>
  );
}
function CrystalMysticOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <polygon points="12,1 17,7 22,12 17,17 12,23 7,17 2,12 7,7" fill="#e53935" />
      <polygon points="12,1 17,7 12,10" fill="#ff8a80" />
      <polygon points="12,1 7,7 12,10" fill="#ff5252" />
      <polygon points="7,7 2,12 12,10" fill="#d32f2f" />
      <polygon points="17,7 22,12 12,10" fill="#c62828" />
      <polygon points="2,12 7,17 12,14 12,10" fill="#b71c1c" />
      <polygon points="22,12 17,17 12,14 12,10" fill="#e53935" />
      <polygon points="7,17 12,23 12,18" fill="#7f0000" />
      <polygon points="17,17 12,23 12,18" fill="#b71c1c" />
    </svg>
  );
}

// ── Style B4: App Icon (shape on rounded square background) ──────────────────

function AppIconGrass({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#e8f5e9" />
      <path d="M12 18 L12 11" stroke="#2e7d32" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M12 13 C12 13 9 10 7 7 C10.5 8 12 12 12 13" fill="#66bb6a" />
      <path d="M12 15 C12 15 15 11 17 8 C13.5 9.5 12 14 12 15" fill="#43a047" />
      <path d="M12 17 C12 17 9.5 14 7 15 C9 17 12 17 12 17" fill="#81c784" />
    </svg>
  );
}
function AppIconWood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#fff3e0" />
      <rect x="4" y="9" width="16" height="7" rx="2" fill="#a0522d" />
      <rect x="4" y="9" width="16" height="2.5" rx="1" fill="#deb887" />
      <ellipse cx="7.5" cy="12.5" rx="2" ry="3.5" fill="none" stroke="#bf8040" strokeWidth="1.2" />
    </svg>
  );
}
function AppIconFood({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#fffde7" />
      <circle cx="9" cy="10.5" r="3" fill="#ef5350" />
      <circle cx="15" cy="10.5" r="3" fill="#66bb6a" />
      <circle cx="12" cy="9" r="3" fill="#ffca28" />
      <rect x="5" y="14" width="14" height="3" rx="1.5" fill="#ff8f00" />
    </svg>
  );
}
function AppIconOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#eceff1" />
      <polygon points="12,4 18,9 18,17 12,20 6,17 6,9" fill="#90a4ae" />
      <polygon points="12,4 18,9 12,12" fill="#eceff1" />
      <polygon points="12,4 6,9 12,12" fill="#cfd8dc" />
    </svg>
  );
}
function AppIconMysticOre({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}>
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#fce4ec" />
      <polygon points="12,3 15,9 20,10 16,15 17,21 12,18 7,21 8,15 4,10 9,9" fill="#e53935" />
      <polygon points="12,3 15,9 12,11 9,9" fill="#ff8a80" />
      <circle cx="12" cy="13" r="2" fill="#ff5252" />
    </svg>
  );
}

// ── Style C: Badge (CSS pill) ─────────────────────────────────────────────────

const BADGE_COLORS: Record<number, { bg: string; text: string; border: string }> = {
  0: { bg: "#d9f0d9", text: "#1e5c1e", border: "#4caf50" },
  1: { bg: "#fce8ca", text: "#6b3208", border: "#c46820" },
  2: { bg: "#ffecd0", text: "#7a3800", border: "#e07020" },
  3: { bg: "#e2e2e2", text: "#303030", border: "#888888" },
  4: { bg: "#ffd2d2", text: "#880000", border: "#cc2020" },
};

function BadgeMaterial({ id }: { id: number }) {
  const c = BADGE_COLORS[id];
  const name = MATERIAL_NAMES[id] ?? "?";
  return (
    <span style={{ backgroundColor: c.bg, color: c.text, border: `1.5px solid ${c.border}`, borderRadius: "4px", padding: "2px 7px", fontSize: "11px", fontWeight: 700, letterSpacing: "0.03em", display: "inline-block", lineHeight: "1.6", fontFamily: "ui-monospace, monospace" }}>
      {name}
    </span>
  );
}

// ── Extra Wood variants ───────────────────────────────────────────────────────

/** Diagonal log showing cut end — outlined, similar feel to OutlinedOre */
export function WoodLog({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* log body */}
      <path d="M3 16 Q3 20 7 20 L17 20 Q21 20 21 16 L21 10 Q21 6 17 6 L7 6 Q3 6 3 10 Z" fill="#ffe0b2" stroke="#bf360c" strokeWidth="1.4" />
      {/* cut end face (left) */}
      <ellipse cx="7" cy="13" rx="3" ry="4.5" fill="#ffccbc" stroke="#e64a19" strokeWidth="1.2" />
      <ellipse cx="7" cy="13" rx="1.5" ry="2.5" fill="none" stroke="#bf360c" strokeWidth="0.8" />
      <ellipse cx="7" cy="13" rx="0.5" ry="1" fill="#a0522d" stroke="none" />
      {/* grain lines on log top */}
      <line x1="10" y1="7" x2="10" y2="19" stroke="#e64a19" strokeWidth="0.6" opacity="0.4" />
      <line x1="14" y1="7" x2="14" y2="19" stroke="#e64a19" strokeWidth="0.6" opacity="0.4" />
    </svg>
  );
}

/** Plank bundle tied — flat stylized */
export function WoodPlanks({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round">
      <rect x="2" y="6" width="20" height="4" rx="1.5" fill="#d4944a" stroke="#7a3810" strokeWidth="1.2" />
      <rect x="2" y="11" width="20" height="4" rx="1.5" fill="#c07830" stroke="#7a3810" strokeWidth="1.2" />
      <rect x="2" y="16" width="20" height="4" rx="1.5" fill="#a86020" stroke="#7a3810" strokeWidth="1.2" />
      {/* grain highlights */}
      <line x1="4" y1="7" x2="7" y2="9" stroke="#f0b868" strokeWidth="0.8" opacity="0.7" />
      <line x1="4" y1="12" x2="8" y2="14" stroke="#e0a050" strokeWidth="0.8" opacity="0.7" />
      {/* rope tie */}
      <path d="M11 5 L11 21" stroke="#8b6914" strokeWidth="1.8" />
    </svg>
  );
}

/** Branch/twig shape — thin organic lines */
export function WoodBranch({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 20 Q8 14 12 10 Q16 6 19 4" stroke="#6d3b0a" strokeWidth="2.5" />
      <path d="M12 10 Q10 7 8 5" stroke="#8b4e10" strokeWidth="1.8" />
      <path d="M14 12 Q17 11 19 9" stroke="#8b4e10" strokeWidth="1.8" />
      <path d="M10 14 Q8 15 6 14" stroke="#a0631a" strokeWidth="1.5" />
      <ellipse cx="19" cy="4" rx="1.5" ry="1" fill="#a5d6a7" transform="rotate(-30 19 4)" />
      <ellipse cx="8" cy="5" rx="1.5" ry="1" fill="#81c784" transform="rotate(20 8 5)" />
      <ellipse cx="19" cy="9" rx="1.5" ry="1" fill="#c5e1a5" transform="rotate(-15 19 9)" />
    </svg>
  );
}

// ── Extra Food variants ───────────────────────────────────────────────────────

/** Outlined apple — simple iconic shape */
export function FoodApple({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* leaf */}
      <path d="M12 5 Q15 2 17 4 Q15 6 12 5 Z" fill="#a5d6a7" stroke="#388e3c" strokeWidth="1" />
      {/* stem */}
      <line x1="12" y1="5" x2="12" y2="7" stroke="#5d4037" strokeWidth="1.5" />
      {/* apple body */}
      <path d="M8 8 Q4 8 4 13 Q4 20 8 21 Q10 22 12 21 Q14 22 16 21 Q20 20 20 13 Q20 8 16 8 Q14 7 12 8 Q10 7 8 8 Z" fill="#ffcdd2" stroke="#c62828" strokeWidth="1.5" />
      {/* shine */}
      <path d="M8 10 Q7 13 8 15" stroke="#ef9a9a" strokeWidth="1" opacity="0.7" />
    </svg>
  );
}

/** Wheat/grain stalk — like the game's grass material but more grain-like */
export function FoodGrain({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="22" x2="12" y2="4" stroke="#8b6914" strokeWidth="1.6" />
      {/* grain heads on stalk */}
      <ellipse cx="12" cy="5" rx="2" ry="3" fill="#f9a825" stroke="#f57f17" strokeWidth="1.2" />
      <ellipse cx="9" cy="9" rx="2" ry="2.5" fill="#fbc02d" stroke="#f57f17" strokeWidth="1.2" transform="rotate(-20 9 9)" />
      <ellipse cx="15" cy="9" rx="2" ry="2.5" fill="#fbc02d" stroke="#f57f17" strokeWidth="1.2" transform="rotate(20 15 9)" />
      <ellipse cx="8" cy="13" rx="1.8" ry="2.2" fill="#f9a825" stroke="#e65100" strokeWidth="1" transform="rotate(-25 8 13)" />
      <ellipse cx="16" cy="13" rx="1.8" ry="2.2" fill="#f9a825" stroke="#e65100" strokeWidth="1" transform="rotate(25 16 13)" />
    </svg>
  );
}

/** Basket of produce — outlined, warm colours */
export function FoodBasket({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* basket */}
      <path d="M4 13 Q4 20 12 20 Q20 20 20 13 L19 10 Q16 8 12 8 Q8 8 5 10 Z" fill="#fff9c4" stroke="#f57f17" strokeWidth="1.4" />
      {/* weave lines */}
      <line x1="8" y1="9" x2="6" y2="19" stroke="#ef6c00" strokeWidth="0.7" opacity="0.5" />
      <line x1="12" y1="8" x2="12" y2="20" stroke="#ef6c00" strokeWidth="0.7" opacity="0.5" />
      <line x1="16" y1="9" x2="18" y2="19" stroke="#ef6c00" strokeWidth="0.7" opacity="0.5" />
      {/* items sticking out */}
      <circle cx="9" cy="9" r="2.5" fill="#ffebee" stroke="#c62828" strokeWidth="1.2" />
      <circle cx="15" cy="9" r="2.5" fill="#e8f5e9" stroke="#2e7d32" strokeWidth="1.2" />
      <circle cx="12" cy="7" r="2.5" fill="#fff8e1" stroke="#f9a825" strokeWidth="1.2" />
    </svg>
  );
}

// ── Extra Mystic Ore variants ─────────────────────────────────────────────────

/** Faceted ruby gem — like OutlinedOre but red diamond cut */
export function MysticGem({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* top crown */}
      <path d="M8 9 L6 5 L12 3 L18 5 L16 9 Z" fill="#ffcdd2" stroke="#c62828" strokeWidth="1.3" />
      {/* pavilion */}
      <path d="M6 9 L8 9 L16 9 L18 9 L12 22 Z" fill="#ef9a9a" stroke="#b71c1c" strokeWidth="1.3" />
      {/* girdle line */}
      <line x1="6" y1="9" x2="18" y2="9" stroke="#c62828" strokeWidth="1" />
      {/* crown facets */}
      <line x1="12" y1="3" x2="12" y2="9" stroke="#e57373" strokeWidth="0.8" />
      <line x1="6" y1="5" x2="12" y2="9" stroke="#e57373" strokeWidth="0.8" />
      <line x1="18" y1="5" x2="12" y2="9" stroke="#e57373" strokeWidth="0.8" />
      {/* shine */}
      <path d="M9 5 L10 8" stroke="#ffebee" strokeWidth="1.2" />
    </svg>
  );
}

/** Crystal shard cluster — angular spiky crystals */
export function MysticShard({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* main shard */}
      <polygon points="12,2 16,10 12,22 8,10" fill="#ffcdd2" stroke="#c62828" strokeWidth="1.3" />
      <line x1="12" y1="2" x2="12" y2="22" stroke="#e57373" strokeWidth="0.7" />
      {/* left shard */}
      <polygon points="7,5 10,11 7,20 4,11" fill="#ef9a9a" stroke="#d32f2f" strokeWidth="1" />
      {/* right shard */}
      <polygon points="17,5 20,11 17,20 14,11" fill="#ef9a9a" stroke="#d32f2f" strokeWidth="1" />
      {/* shine on main */}
      <line x1="10" y1="4" x2="11" y2="8" stroke="#fff9c4" strokeWidth="1.2" opacity="0.8" />
    </svg>
  );
}

/** Glowing rune stone — flat with glow effect */
export function MysticRune({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" strokeLinecap="round" strokeLinejoin="round">
      {/* stone body */}
      <path d="M7 3 L17 3 L21 7 L21 17 L17 21 L7 21 L3 17 L3 7 Z" fill="#fce4ec" stroke="#ad1457" strokeWidth="1.4" />
      {/* rune lines */}
      <path d="M12 7 L12 11 L9 15 M12 11 L15 15" stroke="#e91e63" strokeWidth="1.8" />
      <line x1="9" y1="7" x2="15" y2="7" stroke="#f48fb1" strokeWidth="1" />
      <line x1="9" y1="17" x2="15" y2="17" stroke="#f48fb1" strokeWidth="1" />
    </svg>
  );
}

// ── Public API ────────────────────────────────────────────────────────────────

export type MaterialIconStyle = "pixel" | "flat" | "outlined" | "crystal" | "appicon" | "badge";

const ICONS: Record<MaterialIconStyle, ((props: { size: number }) => ReactElement | null)[]> = {
  pixel:    [PixelGrass,     PixelWood,      PixelFood,     PixelOre,     PixelMysticOre],
  flat:     [FlatGrass,      FlatWood,       FlatFood,      FlatOre,      FlatMysticOre],
  outlined: [OutlinedGrass,  OutlinedWood,   OutlinedFood,  OutlinedOre,  OutlinedMysticOre],
  crystal:  [CrystalGrass,   CrystalWood,    CrystalFood,   CrystalOre,   CrystalMysticOre],
  appicon:  [AppIconGrass,   AppIconWood,    AppIconFood,   AppIconOre,   AppIconMysticOre],
  badge:    [],
};

export function MaterialIcon({ id, style = "flat", size = 20 }: { id: number; style?: MaterialIconStyle; size?: number; }) {
  if (style === "badge") return <BadgeMaterial id={id} />;
  // Use real game icon from manifest when available
  const resourceName = MATERIAL_NAMES[id];
  if (resourceName) {
    const iconUrl = getItemIcon(resourceName);
    if (iconUrl) {
      return <img src={iconUrl} alt={resourceName} width={size} height={size} style={{ imageRendering: "pixelated", objectFit: "contain" }} />;
    }
  }
  // Fallback to SVG
  const Icon = ICONS[style]?.[id];
  if (!Icon) return null;
  return <Icon size={size} />;
}
