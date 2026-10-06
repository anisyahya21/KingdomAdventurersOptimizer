import { FACILITY_TRAINING_DATA, TRAINING_STATS, XP_EMITTERS, type TrainingFacilityRecord, type TrainingStatName } from '@/game-data/training-facilities';
import { BUILDINGS, PLOT_SIZES, PLOT_TILES, type PlotSize } from '@/game-data/buildings';
import placementRules from '@/game-data/builder-placement-rules.json';
import { BUILDER_ASSETS, cells, dimensions, facilityById, initialFurniture, isManualFacility, validatePlacement, type BuilderItem, type BuilderState, type PlacementIndex } from '@/lib/world-builder';

type Footprint = { x: number; y: number; width: number; height: number };
type LayoutSource = Footprint & { id: string; facilityId: number; fixed?: boolean; provided?: boolean };
export type TrainingLayout = {
  revision: 2; targetId: number; width: number; height: number; target: Footprint;
  sources: LayoutSource[]; blocked: Array<{ x: number; y: number; reason: string; facilityId?: number }>;
  roomKey?: string; roomName?: string; room?: Footprint & { houseId: number; size: PlotSize };
  unsupportedReason?: string; warning?: string;
};
const records = new Map(FACILITY_TRAINING_DATA.map(f => [f.id, f]));
const origin = 70;
const indoorId = (id: number) => facilityById.get(id)?.tab === 'indoors';
const hostEntries = Object.entries(BUILDER_ASSETS.plots);
const hostSupplied = new Set(hostEntries.flatMap(([, p]) => p.fixed.map(f => f.facilityId))
  .filter(id => (records.get(id)?.stats.length ?? 0) > 0 || XP_EMITTERS.some(e => e.facilityId === id)));
const hasXp = (id: number) => XP_EMITTERS.some(e => e.facilityId === id);
export function trainingFootprint(id: number): { width: number; height: number } {
  const a = BUILDER_ASSETS.facilities[id];
  return { width: a?.width ?? 1, height: a?.height ?? 1 };
}
export function effectiveTrainingLevel(facility: TrainingFacilityRecord, level: number): number {
  return facility.canUpgrade ? Math.max(1, Math.min(100, Math.trunc(Number.isFinite(level) ? level : 1))) : 1;
}
// GetBonusParamExp 0x1622a8c; level is already capped to the effective in-game level.
export function getBaseXp(facility: TrainingFacilityRecord, stat: TrainingStatName, level: number): number {
  const pair = facility.baseXp[stat];
  return pair ? pair.initial + pair.increment * (effectiveTrainingLevel(facility, level) - 1) : 0;
}
// FacilityComponent.GetBonusParamExp 0x14c91ec: one integer operation after stacking.
export function getTrainingXp(facility: TrainingFacilityRecord, stat: TrainingStatName, level: number, bonuses: Partial<Record<TrainingStatName, number>> = {}): number {
  const base = getBaseXp(facility, stat, level);
  return Math.trunc(base * (100 + ((facility.flags & 2097152) ? 0 : bonuses[stat] ?? 0)) / 100);
}
export function xpEffectValue(minimum: number, maximum: number, level: number): number {
  const step = Math.max(1, Math.min(100, Math.trunc(level))) - 1;
  if (step >= 99) return maximum;
  return Math.trunc(Math.fround(Math.fround(Math.fround(step / 99) * (maximum - minimum)) + minimum));
}
function parseRoomKey(key: string): { houseId: number; size: PlotSize; fixtureIndex?: number } | undefined {
  const match = /^(\d+)-(S|M|L|XL)(?::(\d+))?$/.exec(key);
  if (!match) return undefined;
  return { houseId: Number(match[1]), size: match[2] as PlotSize, ...(match[3] !== undefined ? { fixtureIndex: Number(match[3]) } : {}) };
}
function roomLabel(key: string, index?: number): string {
  const parsed = parseRoomKey(key)!;
  const role = index === undefined ? '' : ` · ${BUILDER_ASSETS.plots[key].fixed[index].role}`;
  return `${BUILDINGS.find(b => b.id === parsed.houseId)?.name ?? 'Room'} ${parsed.size}${role}`;
}
export function trainingRoomOptions(target: TrainingFacilityRecord): Array<{ key: string; name: string }> {
  if (hostSupplied.has(target.id)) {
    return hostEntries.flatMap(([key, p]) => p.fixed.flatMap((f, index) => f.facilityId === target.id
      ? [{ key: `${key}:${index}`, name: roomLabel(key, index) }] : []))
      .sort((a, b) => PLOT_SIZES.indexOf(parseRoomKey(b.key)!.size) - PLOT_SIZES.indexOf(parseRoomKey(a.key)!.size) || a.key.localeCompare(b.key));
  }
  const params = new Set(TRAINING_STATS.filter(s => target.stats.includes(s.name)).map(s => s.paramId));
  const rooms = hostEntries.filter(([, p]) => indoorId(target.id) || p.fixed.some(f => XP_EMITTERS.some(e => e.facilityId === f.facilityId && params.has(e.type as typeof TRAINING_STATS[number]['paramId']))))
    .map(([key]) => ({ key, name: `${indoorId(target.id) ? '' : 'Beside '}${roomLabel(key)}` }))
    .sort((a, b) => Number(b.key === '15-XL') - Number(a.key === '15-XL') || PLOT_SIZES.indexOf(parseRoomKey(b.key)!.size) - PLOT_SIZES.indexOf(parseRoomKey(a.key)!.size) || a.name.localeCompare(b.name));
  return indoorId(target.id) ? rooms : [{ key: 'town-land', name: 'Open town land' }, ...rooms];
}
function plotItem(layout: TrainingLayout): BuilderItem | undefined {
  const r = layout.room;
  return r ? { id: 'training-room', kind: 'plot', size: r.size, houseId: r.houseId, x: origin + r.x, y: origin + r.y, direction: 0, level: 1, fullness: 0 } : undefined;
}
function fixtureItems(layout: TrainingLayout): BuilderItem[] {
  const plot = plotItem(layout);
  return plot ? initialFurniture(plot).map((f, index) => ({ ...f, id: `fixture-${layout.roomKey}-${index}` })) : [];
}
function isIndoorPlacement(layout: TrainingLayout, id: number): boolean {
  return indoorId(id) || (!!layout.room && hostSupplied.has(id));
}
function builderItem(layout: TrainingLayout, id: number, x: number, y: number, instanceId: string): BuilderItem {
  return { id: instanceId, kind: 'facility', facilityId: id, x: origin + x, y: origin + y, direction: 0, level: 1, fullness: 0,
    ...(isIndoorPlacement(layout, id) ? { parentId: 'training-room' } : {}) };
}
const covers = (rect: Footprint, x: number, y: number) => x >= rect.x && y >= rect.y && x < rect.x + rect.width && y < rect.y + rect.height;
export function createTrainingLayout(target: TrainingFacilityRecord, roomKey?: string): TrainingLayout {
  const options = trainingRoomOptions(target);
  const selected = options.find(o => o.key === roomKey) ?? options[0];
  const room = selected ? parseRoomKey(selected.key) : undefined;
  const size = trainingFootprint(target.id);
  const layout: TrainingLayout = { revision: 2, targetId: target.id, width: size.width + 6, height: size.height + 6,
    target: { x: 3, y: 3, ...size }, sources: [], blocked: [] };
  if (!BUILDER_ASSETS.facilities[target.id]) {
    layout.unsupportedReason = 'No placement geometry is available for this facility.';
    return layout;
  }
  if (target.flags & 2097152) {
    layout.unsupportedReason = 'This facility does not receive surround effects.';
    return layout;
  }
  if (room) {
    const [w, h] = PLOT_TILES[room.size].split('×').map(Number);
    layout.width = w + 6; layout.height = h + 6;
    layout.roomKey = selected!.key; layout.roomName = selected!.name;
    layout.room = { x: 3, y: 3, width: w, height: h, houseId: room.houseId, size: room.size };
    const fixtures = fixtureItems(layout);
    const chosen = room.fixtureIndex === undefined ? undefined : fixtures[room.fixtureIndex];
    if (chosen) {
      layout.target = { x: chosen.x - origin, y: chosen.y - origin, ...trainingFootprint(target.id) };
    } else if (indoorId(target.id)) {
      // Preserve the original Mansion XL training cell when expanding the grid.
      layout.target = { x: 6, y: 7, ...size };
      const occupied = new Set(fixtures.flatMap(f => cells(f).map(c => `${c.x-origin},${c.y-origin}`)));
      const fits = (x: number, y: number) => x > 3 && y > 3 && x + size.width < 3 + w && y + size.height < 3 + h
        && Array.from({ length: size.width * size.height }, (_, i) => `${x + i % size.width},${y + Math.floor(i / size.width)}`).every(c => !occupied.has(c));
      if (!fits(layout.target.x, layout.target.y)) {
        let location: { x: number; y: number } | undefined;
        for (let y = 4; y < h + 2 && !location; y++) for (let x = 4; x < w + 2 && !location; x++) if (fits(x, y)) location = { x, y };
        if (location) layout.target = { ...location, ...size };
        else layout.unsupportedReason = 'The room has no free space for this training facility.';
      }
    } else {
      layout.target = { x: 3 + w, y: 4, ...size };
    }
    for (let y = 3; y < 3 + h; y++) for (let x = 3; x < 3 + w; x++) {
      if (x === 3 || y === 3 || x === w + 2 || y === h + 2) layout.blocked.push({ x, y, reason: 'Room wall' });
    }
    fixtures.forEach((f, index) => {
      if (index === room.fixtureIndex) return;
      const [fw, fh] = dimensions(f);
      if (hasXp(f.facilityId!)) {
        layout.sources.push({ id: f.id, facilityId: f.facilityId!, x: f.x - origin, y: f.y - origin, width: fw, height: fh,
          fixed: f.facilityId !== 155, provided: true });
      } else {
        for (const c of cells(f)) layout.blocked.push({ x: c.x - origin, y: c.y - origin, reason: facilityById.get(f.facilityId!)?.name ?? 'Room fixture', facilityId: f.facilityId });
      }
    });
  } else {
    layout.roomKey = 'town-land'; layout.roomName = 'Open town land';
  }
  return layout;
}
function candidateEmitters(target: TrainingFacilityRecord, layout?: TrainingLayout): Array<{ id: number; name: string; canUpgrade: boolean }> {
  if (target.flags & 2097152) return [];
  const params = new Set(TRAINING_STATS.filter(s => target.stats.includes(s.name)).map(s => s.paramId));
  const ids = new Set(XP_EMITTERS.filter(e => (e.min > 0 || e.max > 0) && params.has(e.type as typeof TRAINING_STATS[number]['paramId'])).map(e => e.facilityId));
  return [...ids].flatMap(id => {
    const f = facilityById.get(id), r = records.get(id);
    if (!f || !r || !BUILDER_ASSETS.facilities[id]) return [];
    if (indoorId(id) && !layout?.room) return [];
    if (hostSupplied.has(id) && !(id === 155 && layout?.room?.houseId === 7)) return [];
    if (!isManualFacility(id)) return [];
    return [{ id, name: f.name, canUpgrade: r.canUpgrade }];
  }).sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
}
export function emitterXpEffects(layout: TrainingLayout, sourceId: number, levels: Record<number, number>): Partial<Record<TrainingStatName, number>> {
  const target = records.get(layout.targetId), source = records.get(sourceId);
  if (!target || !source || (target.flags & 2097152)) return {};
  const effects: Partial<Record<TrainingStatName, number>> = {};
  for (const effect of XP_EMITTERS.filter(e => e.facilityId === sourceId)) {
    const stat = TRAINING_STATS.find(s => s.paramId === effect.type)?.name;
    if (stat && target.stats.includes(stat)) effects[stat] = (effects[stat] ?? 0) + xpEffectValue(effect.min, effect.max, effectiveTrainingLevel(source, levels[sourceId] ?? 1));
  }
  return effects;
}
export function eligibleEmitters(target: TrainingFacilityRecord, layout?: TrainingLayout): Array<{ id: number; name: string; canUpgrade: boolean }> {
  const scene = layout ?? createTrainingLayout(target);
  if (scene.unsupportedReason) return [];
  const context = placementContext(scene);
  return candidateEmitters(target, scene).filter(source => {
    for (let y = 0; y < scene.height; y++) for (let x = 0; x < scene.width; x++) {
      if (!placementError(scene, source.id, x, y, context)) return true;
    }
    return false;
  });
}
function placementContext(layout: TrainingLayout): { state: BuilderState; index: PlacementIndex } {
  const plot = plotItem(layout);
  const parsed = layout.roomKey ? parseRoomKey(layout.roomKey) : undefined;
  // The target replaces exactly one template fixture, not every fixture of its type.
  const fixtures = fixtureItems(layout).filter((_, i) => i !== parsed?.fixtureIndex)
    .filter(f => !hasXp(f.facilityId!));
  const target = builderItem(layout, layout.targetId, layout.target.x, layout.target.y, 'training-target');
  const sources = layout.sources.map(s => ({ ...builderItem(layout, s.facilityId, s.x, s.y, s.id), fixed: s.fixed }));
  const items = [...(plot ? [plot] : []), ...fixtures, target, ...sources];
  const occupied = new Map<string, BuilderItem>(), roomCells = new Map<string, BuilderItem>();
  for (const f of items) for (const c of cells(f)) (f.parentId ? roomCells : occupied).set(`${c.x},${c.y}`, f);
  const land = new Set<string>();
  for (let y = 0; y < layout.height; y++) for (let x = 0; x < layout.width; x++) land.add(`${origin + x},${origin + y}`);
  // Reclaimed land inside town territory. Expansion exclusion and all occupancy
  // checks still use the shared game's placement adapter.
  return { state: { version: 1, items, reclaimed: [] }, index: { land, covered: land, occupied, indoors: roomCells, plots: plot ? [plot] : [] } };
}
function sourceRect(source: Footprint & { facilityId: number }): Footprint {
  const flags = (placementRules.facilities as Record<string, { flags: number }>)[source.facilityId]?.flags ?? 0;
  // FLAG_AREA_MERGE enumerates from the actual entity anchor, at bottom-right.
  return flags & 2048 ? { x: source.x + source.width - 1, y: source.y + source.height - 1, width: 1, height: 1 } : source;
}
function reachesTarget(source: Footprint & { facilityId: number }, target: Footprint): boolean {
  const r = sourceRect(source);
  for (let y = target.y; y < target.y + target.height; y++) for (let x = target.x; x < target.x + target.width; x++) {
    if (x >= r.x - 3 && x < r.x + r.width + 3 && y >= r.y - 3 && y < r.y + r.height + 3 && !covers(r, x, y)) return true;
  }
  return false;
}
function placementError(layout: TrainingLayout, sourceId: number, x: number, y: number, context = placementContext(layout)): string | undefined {
  const footprint = { x, y, ...trainingFootprint(sourceId) };
  if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x + footprint.width > layout.width || y + footprint.height > layout.height) return 'Keep the whole facility inside the layout.';
  if (sourceId === 155 && layout.sources.filter(s => s.facilityId === 155).length >= 2) return 'This Inn supplies two Guest Beds. Move or remove one before placing it again.';
  for (let cy = y; cy < y + footprint.height; cy++) for (let cx = x; cx < x + footprint.width; cx++) {
    const block = layout.blocked.find(c => c.x === cx && c.y === cy);
    if (block) return `${block.reason} occupies this cell.`;
    if (covers(layout.target, cx, cy)) return 'The training facility occupies this cell.';
    if (layout.sources.some(s => covers(s, cx, cy))) return 'Another facility occupies this space.';
  }
  if (!reachesTarget({ ...footprint, facilityId: sourceId }, layout.target)) return 'This facility is outside the three-cell XP surround range.';
  return validatePlacement(builderItem(layout, sourceId, x, y, 'training-placement-probe'), context.state, context.index).error;
}
export function placeXpEmitter(layout: TrainingLayout, sourceId: number, x: number, y: number): { layout: TrainingLayout; error?: string } {
  const target = records.get(layout.targetId);
  if (layout.unsupportedReason || !target) return { layout, error: layout.unsupportedReason ?? 'Unknown training facility.' };
  if (!candidateEmitters(target, layout).some(e => e.id === sourceId)) return { layout, error: 'This facility cannot be added in this layout. Room fixtures are supplied by their host room.' };
  const error = placementError(layout, sourceId, x, y);
  if (error) return { layout, error };
  const footprint = { x, y, ...trainingFootprint(sourceId) }, id = crypto.randomUUID();
  return { layout: { ...layout, sources: [...layout.sources, { id, facilityId: sourceId, ...footprint }] } };
}
export function removeXpEmitter(layout: TrainingLayout, id: string): TrainingLayout {
  return { ...layout, sources: layout.sources.filter(s => s.id !== id || s.fixed) };
}
export function normalizeTrainingLayout(layout: TrainingLayout | undefined, target: TrainingFacilityRecord): TrainingLayout {
  if (layout?.revision === 2) return layout;
  let next = createTrainingLayout(target);
  if (!layout) return next;
  // Preserve the user's existing valid surrounds during the editor upgrade.
  const shift = next.room ? 3 : 0;
  let dropped = 0;
  for (const source of layout.sources) {
    const result = placeXpEmitter(next, source.facilityId, source.x + shift, source.y + shift);
    if (result.error) { dropped++; continue; }
    next = result.layout;
    next.sources[next.sources.length - 1].id = source.id;
  }
  if (dropped) next.warning = `${dropped} previously placed facilities do not fit this room and were excluded from the XP calculation.`;
  return next;
}
export function trainingBonuses(layout: TrainingLayout, levels: Record<number, number>): Partial<Record<TrainingStatName, number>> {
  const target = records.get(layout.targetId), sums: Partial<Record<TrainingStatName, number>> = {};
  if (!target || layout.unsupportedReason || (target.flags & 2097152)) return sums;
  for (const source of layout.sources) {
    if (!reachesTarget(source, layout.target)) continue;
    const record = records.get(source.facilityId);
    if (!record) continue;
    const level = effectiveTrainingLevel(record, levels[source.facilityId] ?? 1);
    // Each logical MapChipRect entity contributes once: native Select/Where/
    // Distinct at 0x14e3e90 collapses references returned by several footprint cells.
    for (const effect of XP_EMITTERS.filter(e => e.facilityId === source.facilityId)) {
      const stat = TRAINING_STATS.find(s => s.paramId === effect.type)?.name;
      if (stat && getBaseXp(target, stat, levels[target.id] ?? 1) > 0) sums[stat] = (sums[stat] ?? 0) + xpEffectValue(effect.min, effect.max, level);
    }
  }
  return sums;
}
