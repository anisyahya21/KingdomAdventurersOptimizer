import rules from "@/game-data/native-water.json";

export { rules as nativeWaterRules };

/** MapSystem.GetWaterEdgeIndex 0x15b25ac; out-of-map neighbors are skipped.
 * Side/corner tables recovered from MapSystem.cctor, hashed metadata arrays.
 * UpdateAroundWaterDarkness 0x15b4394 uses Manhattan distance minus one,
 * selecting Define.WATER_MAPCHIP_DATA_IDS = [1,2,3,4].
 */
export function recoverWaterTiles(cells: readonly { x: number; y: number; water: boolean }[]) {
  const terrain = new Map(cells.map(c => [`${c.x},${c.y}`, c.water]));
  return cells.filter(c => c.water).map(c => {
    let mask = 0;
    rules.directions.forEach(([dx, dy], direction) => {
      if (terrain.get(`${c.x + dx},${c.y + dy}`) !== false) return;
      if (direction >= 4 && (mask & ((1 << (direction - 4)) | (1 << ((direction - 3) % 4))))) return;
      mask |= 1 << direction;
    });
    let distance = 4;
    for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
      const d = Math.abs(dx) + Math.abs(dy);
      if (d > 0 && d < distance && terrain.get(`${c.x + dx},${c.y + dy}`) === false) distance = d;
    }
    const layers: number[] = [];
    if (mask & 15) layers.push(rules.sideLayers[mask & 15]);
    for (let corner = 0; corner < 4; corner++) if (mask & (1 << (corner + 4))) layers.push(rules.cornerLayers[corner]);
    return { x: c.x, y: c.y, image: `mizu0${distance - 1}`, layers };
  });
}
