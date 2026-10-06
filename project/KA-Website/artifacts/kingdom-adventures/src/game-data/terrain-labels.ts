// Native BaseMapGenerator terrain IDs. Soil states use separate IDs (8-13).
export const TERRAIN_NAMES: Record<number, string> = {
  0: "Water",
  1: "Ground / dirt",
  2: "Grass",
  3: "Sand",
  4: "Rock",
  5: "Volcano",
  6: "Snow",
  7: "Swamp",
  8: "Snow soil",
  9: "Desert soil",
  10: "Volcanic soil",
  11: "Rocky soil",
  12: "Swamp soil",
  13: "Grassland soil",
};

// Original map chip PNGs for the terrain types referenced by Survey.csv.
export const SURVEY_TERRAIN_PREVIEW_IMAGES: Record<number, string> = {
  0: "/world-assets/chip/mizu00.png",
  1: "/world-assets/chip/tuchi00.png",
  2: "/world-assets/chip/jimen00.png",
  3: "/world-assets/chip/suna00.png",
  4: "/world-assets/chip/iwa00.png",
  5: "/world-assets/chip/kazan00.png",
  6: "/world-assets/chip/snow00.png",
  7: "/world-assets/chip/swamp00.png",
  15: "/world-assets/chip/wasteland.png",
};
