import acquisitionLimits from '@/game-data/builder-acquisition-limits.json';
import placementRules from '@/game-data/builder-placement-rules.json';
import rawAssets from '@/game-data/builder-assets.json';
import mapFacilities from '@/game-data/native-map-facilities.json';
import mapGround from '@/game-data/native-map-ground.json';
import { BUILDINGS, PLOT_SIZES, PLOT_TILES, type PlotSize } from '@/game-data/buildings';
import { FACILITIES } from '@/game-data/facilities';
import monsterSprites from '@/game-data/monster-sprites.json';

export type Cell = { x: number; y: number };
export type BuilderItem = Cell & {
  id: string; kind: 'facility' | 'plot' | 'road' | 'dungeon'; facilityId?: number; size?: PlotSize; dungeonChipId?:number; petId?:number;
  houseId?: number; direction: number; level: number; fullness: number;
  parentId?: string; fixed?: boolean; role?: string; facing?:number;
};
export type BuilderState = { version: 1; geometryRevision?: 2; items: BuilderItem[]; reclaimed: Cell[] };
export type Sprite = { url: string; width: number; height: number; dx: number; dy: number };
export type FacilityAsset = { width: number; height: number; chipId: number; variants: string[]; states: string[]; anchorX: number; anchorY: number; expandsTown?:boolean; contentsAppearancePending?:boolean; menuIcon?:string; builtInRoomFixture?:boolean; barrierFrames?:string[]; rotationDraws?:PlotDraw[][]; surroundEffects?:number[][] };
export type PlotDraw = { sprite: string; x: number; y: number; elevation: number; depth: number };
type PlotAsset = { supportHeight:number; draws: PlotDraw[]; rotations:PlotDraw[][]; fixed: { facilityId: number; role: string; x: number; y: number; direction: number }[] };
export const BUILDER_ASSETS = rawAssets as unknown as { sprites: Record<string, Sprite>; facilities: Record<string, FacilityAsset>; dungeons:Record<string,FacilityAsset & {name:string}>; plots: Record<string, PlotAsset>; emptyPlots:Record<PlotSize,PlotDraw[][]> };
export const BUILDER_PETS=monsterSprites;
export const canHousePet=(item:BuilderItem)=>item.kind==='facility'&&[157,162].includes(item.facilityId!);
export function assignPet(state:BuilderState,id:string,petId?:number):BuilderState {
  if(!state.items.some(i=>i.id===id&&canHousePet(i)))throw Error('Select a Monster Room or Monster Stable.');
  if(petId!==undefined&&!BUILDER_PETS.some(p=>p.id===petId))throw Error('Unknown monster.');
  return {...state,items:state.items.map(i=>i.id===id?{...i,petId}:i)};
}
export const MAX_TOWN_LEVEL = 100; // User-specified builder limit.
export const SAVE_KEY = 'ka-world-builder-v1';
export const key = (c: Cell) => `${c.x},${c.y}`;
export const facilityById = new Map(FACILITIES.map(f => [f.id, f]));
export const MAP_SIZE = 160;
export const inBounds = (c: Cell) => Number.isInteger(c.x) && Number.isInteger(c.y) && c.x >= 0 && c.y >= 0 && c.x < MAP_SIZE && c.y < MAP_SIZE;
// Recovered native entity depth key. CreateFurniture 0x1476194..14761b0 stores
// Position=(cellX*stepX, height, cellY*stepZ) and the renderer orders by the
// 160-wide cell index (default depth step 0x64, depth-15d6480.asm) plus the
// world x+z term, the facing offset (5 for 0/3, 20 for 1/2) and the layer.
// The entity anchor for a multi-cell assembly is its south-east cell
// (placement/workshop-overlap-tests.json: "native entity anchor is +1,+1").
export function nativeDepth(cell: Cell, offset = 0) {
  return 100 * (cell.y * MAP_SIZE + cell.x) + 24 * (cell.x + cell.y) + offset;
}
// Ground pieces (plot floors, entrance ground, roads) paint before everything.
export function groundDepth(cell: Cell) {
  return -1e9 + cell.y * MAP_SIZE + cell.x;
}
// Exported plot/gate preview pieces carry the older 100*(x+y) depth; the part
// that still orders pieces inside one cell is that sub-cell offset.
export const pieceOffset = (depth: number, local: Cell) => depth - 100 * (local.x + local.y);
export function dimensions(item: BuilderItem): [number, number] {
  const a = item.kind==='dungeon'?BUILDER_ASSETS.dungeons[String(item.dungeonChipId)]:BUILDER_ASSETS.facilities[String(item.facilityId)];
  let [w,h] = item.kind === 'plot' ? PLOT_TILES[item.size ?? 'S'].split('×').map(Number) : item.kind === 'road' ? [1,1] : [a?.width ?? 1,a?.height ?? 1];
  if (item.direction % 2) [w,h] = [h,w];
  return [w,h];
}
export function cells(item: BuilderItem): Cell[] {
  const [w,h] = dimensions(item);
  return Array.from({length:w*h}, (_,i) => ({x:item.x+i%w,y:item.y+Math.floor(i/w)}));
}
export function contains(item: BuilderItem, c: Cell) {
  const [w,h] = dimensions(item);
  return c.x >= item.x && c.y >= item.y && c.x < item.x+w && c.y < item.y+h;
}
export const isHall = (item: BuilderItem) => item.kind === 'facility' && item.facilityId === 17;
// GetTownAreaExpansionRadius 0x15fe464; GetRangeRect 0x14603f0.
export const townRadius = (level: number) => 15 + 2*Math.floor(Math.min(MAX_TOWN_LEVEL,Math.max(1,level))/10);
export function territory(items: BuilderItem[]) {
  const result = new Set<string>();
  const extend = (item:BuilderItem,r:number) => {
    const [width,height] = dimensions(item);
    // The reserved hall ring is 4x4; native MapChip58's expansion rect is its
    // 2x2 core, inset one cell. GetRangeRect expands that entity rectangle.
    const inset=isHall(item)?1:0,x0=item.x+inset,y0=item.y+inset,w=width-2*inset,h=height-2*inset;
    for (let y=Math.max(0,y0-r);y<Math.min(160,y0+h+r);y++)
      for (let x=Math.max(0,x0-r);x<Math.min(160,x0+w+r);x++) result.add(`${x},${y}`);
  };
  for (const hall of items.filter(isHall)) extend(hall,townRadius(hall.level));
  // Expansion facilities use their shared Facility.validRange. Resolve outward
  // from halls so disconnected expansion cycles cannot provide their own town.
  const pending=items.filter(i=>!isHall(i)&&!i.parentId&&BUILDER_ASSETS.facilities[String(i.facilityId)]?.expandsTown&&(facilityById.get(i.facilityId!)?.validRange??0)>0);
  let changed=true;
  while(changed) {
    changed=false;
    for(let i=pending.length-1;i>=0;i--) {
      const item=pending[i];
      if(cells(item).every(c=>result.has(key(c)))) {
        extend(item,facilityById.get(item.facilityId!)!.validRange);
        pending.splice(i,1);changed=true;
      }
    }
  }
  return result;
}
// The drawn town boundary follows a town hall that is being moved: the draft
// replaces the saved hall so its territory travels with the preview.
export function previewTerritory(items: BuilderItem[], draft?: BuilderItem | null) {
  if (!draft || !items.some(i => i.id === draft.id)) return territory(items);
  return territory(items.map(i => i.id === draft.id ? {...i, x:draft.x, y:draft.y, direction:draft.direction} : i));
}
export function initialWorld(): BuilderState {
  return {version:1,geometryRevision:2,reclaimed:[],items:[
    ...mapFacilities.map(f => ({id:`native-${f.id}`,kind:'facility' as const,facilityId:f.id,x:f.originX,y:f.originY,direction:0,level:1,fullness:0,fixed:f.id===196})),
    ...mapGround.ports.map((p,i) => ({id:`port-${i}`,kind:'facility' as const,facilityId:i?10:7,x:p.x,y:p.y,direction:1,level:1,fullness:0,fixed:true})),
  ]};
}
export function isOriginalMapPlacement(item:BuilderItem) {
  const original=mapFacilities.find(f=>`native-${f.id}`===item.id);
  return !!original&&item.x===original.originX&&item.y===original.originY&&item.direction===0;
}
export function isMapStructure(item:BuilderItem) {
  return initialWorld().items.some(original=>original.id===item.id);
}
// Native MapChip FLAG_OUT_TERRITORY_OK (65536), with the hall/category-52
// outside-only branch. ChipReplaceSystem delegates to the same placement check.
export function townPlacementRule(item:BuilderItem): 'inside'|'outside'|'anywhere' {
  if(item.kind==='dungeon')return 'anywhere'; // Existing editor-only dungeon planning.
  if(item.kind==='plot')return 'inside'; // LandPlaceSystem.CheckPlace 0x15a4168.
  const fid=item.kind==='road'?(item.facilityId??4):item.facilityId;
  const rule=(placementRules.facilities as Record<string,{townCoverage:string}>)[String(fid)]?.townCoverage;
  return rule==='anywhere'||rule==='outside'?rule:'inside';
}
export function requiresTownCoverage(item:BuilderItem) {
  return !item.parentId&&townPlacementRule(item)==='inside';
}
export function makeItem(kind: BuilderItem['kind'], facilityId?: number, size?: PlotSize): BuilderItem {
  const item: BuilderItem={id:crypto.randomUUID(),kind,facilityId,size,x:0,y:0,direction:0,level:1,fullness:0};
  return {...item,direction:allowedDirections(item)[0]};
}
// The Gate is an entrance: the game only offers a 90-degree turn, i.e. the two
// orientations that place the door on its cells with the hinge on the same side
// (facing east = direction1 and facing south = direction2). The other two are a
// forbidden 180-degree flip whose artwork hangs half a cell off the footprint.
// User-confirmed gameplay observation 17 September; not an independently
// recovered native flag.
export const gateFacilityId = 28;
export const gateDirections = [1,2];
export function allowedDirections(item: BuilderItem): number[] {
  if(item.kind==='facility'&&item.facilityId===gateFacilityId)return gateDirections;
  return [0,1,2,3];
}
export const rotatedDirections = (item: BuilderItem) => allowedDirections(item).length > 1;
// A forbidden gate facing keeps its wall axis and takes the legal facing on that
// axis (0/2 and 1/3 share a footprint, so no cell changes).
export const legalGateDirection = (direction:number) => direction % 2 ? 1 : 2;
export function itemName(item: BuilderItem) {
  if(item.kind==='dungeon')return BUILDER_ASSETS.dungeons[String(item.dungeonChipId)]?.name??'Dungeon';
  if (item.kind === 'road') return item.facilityId===3?'Gravel Path':'Road';
  if (item.kind === 'plot') return `${item.size} · ${BUILDINGS.find(b=>b.id===item.houseId)?.name ?? 'Unassigned land'}`;
  return facilityById.get(item.facilityId!)?.name ?? 'Facility';
}
export function template(plot: BuilderItem) { return BUILDER_ASSETS.plots[`${plot.houseId}-${plot.size}`]; }
export function rotateLocal(c: Cell, width: number, height: number, direction: number): Cell {
  if (direction===1) return {x:height-1-c.y,y:c.x};
  if (direction===2) return {x:width-1-c.x,y:height-1-c.y};
  if (direction===3) return {x:c.y,y:width-1-c.x};
  return c;
}
export function initialFurniture(plot: BuilderItem): BuilderItem[] {
  const [w,h] = PLOT_TILES[plot.size!].split('×').map(Number);
  return (template(plot)?.fixed ?? []).map(p => {
    const a=BUILDER_ASSETS.facilities[p.facilityId];
    // Directions select visual facing; the native fixed-piece rectangle is unrotated.
    const corners=[{x:p.x,y:p.y},{x:p.x+a.width-1,y:p.y+a.height-1}].map(c=>rotateLocal(c,w,h,plot.direction));
    return {...makeItem('facility',p.facilityId),parentId:plot.id,role:p.role,fixed:p.role!=='shelves'&&!(plot.houseId===7&&p.facilityId===155),
      x:plot.x+Math.min(...corners.map(c=>c.x)),y:plot.y+Math.min(...corners.map(c=>c.y)),direction:plot.direction,
      // Rendering retains the native fixture facing independently of occupancy.
      fullness:0,facing:(p.direction+plot.direction)%4};
  });
}
// HouseData-created room/workshop/service fixtures are not build-menu options.
const builtInFacilityIds=new Set(Object.values(BUILDER_ASSETS.plots).flatMap(p=>p.fixed.filter(f=>['workbench','register','storage'].includes(f.role)&&f.facilityId!==155).map(f=>f.facilityId)));
export const isManualFacility=(id:number)=>!builtInFacilityIds.has(id)&&!BUILDER_ASSETS.facilities[id]?.builtInRoomFixture;
export function containingPlot(item:BuilderItem,state:BuilderState) {
  return state.items.find(p=>p.kind==='plot'&&p.houseId!==undefined&&cells(item).every(c=>{
    const [w,h]=dimensions(p);return c.x>p.x&&c.y>p.y&&c.x<p.x+w-1&&c.y<p.y+h-1;
  }));
}
export function supportHeight(plot?:BuilderItem) {return plot?template(plot)?.supportHeight??0:0;}
const bedIds = new Set(FACILITIES.filter(f=>/\bBed$/.test(f.name)).map(f=>f.id));
const shelfIds = new Set(Object.values(BUILDER_ASSETS.plots).flatMap(p=>p.fixed.filter(f=>f.role==='shelves'&&!bedIds.has(f.facilityId)).map(f=>f.facilityId)));
// Coverage only: a spatial range does not establish target eligibility or totals.
export function surroundCoverage(item:BuilderItem):Cell[] {
  if(item.kind!=='facility'||!BUILDER_ASSETS.facilities[String(item.facilityId)]?.surroundEffects?.length)return [];
  const [width,height]=dimensions(item),inset=isHall(item)?1:0;
  const x0=item.x+inset,y0=item.y+inset,w=width-2*inset,h=height-2*inset;
  const result:Cell[]=[];
  for(let y=y0-3;y<y0+h+3;y++)for(let x=x0-3;x<x0+w+3;x++)
    if(inBounds({x,y})&&!(x>=x0&&x<x0+w&&y>=y0&&y<y0+h))result.push({x,y});
  return result;
}
export function indoorLimit(item: BuilderItem, plot: BuilderItem, items: BuilderItem[]): string | null {
  const b=BUILDINGS.find(b=>b.id===plot.houseId); if (!b) return 'Assign a building type to this plot first.';
  const index=PLOT_SIZES.indexOf(plot.size!);
  const siblings=items.filter(i=>i.parentId===plot.id && i.id!==item.id);
  if (bedIds.has(item.facilityId!)) {
    if (siblings.filter(i=>bedIds.has(i.facilityId!) && i.role!=='bed').length >= b.beds[index]) return 'This plot has reached its extra-bed limit.';
  }
  if (shelfIds.has(item.facilityId!)) {
    // User policy 17 September: any store-shelf appearance fits any plot that
    // has a native shelf slot. Only the plot's own shelf count is enforced.
    if (!template(plot)?.fixed.some(f=>f.role==='shelves')) return 'Shelves only fit inside a shop or storage plot.';
    if (siblings.filter(i=>shelfIds.has(i.facilityId!)).length>=b.store[index]) return 'This plot has reached its shelf limit.';
  }
  if (item.facilityId===157 && siblings.filter(i=>i.facilityId===157).length>=b.monster[index]) return 'This plot has reached its monster-room limit.';
  return null;
}
export type PlacementIndex = { land:Set<string>; covered:Set<string>; occupied:Map<string,BuilderItem>; indoors:Map<string,BuilderItem>; plots:BuilderItem[] };
export function placementIndex(state: BuilderState, defaultLand: Set<string>, omitId?: string): PlacementIndex {
  const occupied=new Map<string,BuilderItem>(),indoors=new Map<string,BuilderItem>();
  for(const item of state.items) {
    if(omitId && (item.id===omitId || item.parentId===omitId)) continue;
    for(const c of cells(item)) (item.parentId?indoors:occupied).set(key(c),item);
    if(item.id.startsWith('port-')) for(let y=item.y+1;y<item.y+3;y++) for(let x=156;x<160;x++) occupied.set(`${x},${y}`,item);
  }
  return {land:new Set([...defaultLand,...state.reclaimed.map(key)]),covered:territory(state.items.filter(i=>i.id!==omitId)),occupied,indoors,plots:state.items.filter(i=>i.kind==='plot' && i.id!==omitId)};
}
// A finite normal-source acquisition budget, separate from native stock storage.
export function placementLimit(facilityId?:number):number|undefined {
  return (acquisitionLimits as Record<string,{maxPlaced:number}>)[String(facilityId)]?.maxPlaced;
}
export function placedCount(items:BuilderItem[],facilityId:number) {
  return items.filter(i=>i.kind==='facility'&&i.facilityId===facilityId).length;
}
export function validatePlacement(item: BuilderItem, state: BuilderState, index: PlacementIndex): {error?:string; parentId?:string} {
  const footprint=cells(item);
  const max=placementLimit(item.facilityId);
  const moving=state.items.some(i=>i.id===item.id);
  if(max!==undefined&&!moving&&placedCount(state.items,item.facilityId!)>=max)
    return {error:`${itemName(item)} limit reached (${max}/${max}). Move or remove an existing one first.`};
  if(item.facilityId!==undefined&&!isManualFacility(item.facilityId)&&!state.items.some(i=>i.id===item.id))return {error:'This fixture is supplied by its plot and cannot be placed manually.'};
  if(footprint.some(c=>!inBounds(c))) return {error:'Stay inside the world map.'};
  const indoor=facilityById.get(item.facilityId!)?.tab==='indoors' || !!item.parentId;
  if(indoor) {
    const plot=index.plots.find(p=>{const [w,h]=dimensions(p);return footprint.every(c=>c.x>p.x && c.y>p.y && c.x<p.x+w-1 && c.y<p.y+h-1);});
    if(!plot || plot.houseId===undefined) return {error:'Place this inside an assigned plot, clear of its walls.'};
    const limit=indoorLimit(item,plot,state.items);if(limit) return {error:limit};
    if(footprint.some(c=>index.indoors.has(key(c)))) return {error:'Another indoor facility occupies this space.'};
    return {parentId:plot.id};
  }
  if(footprint.some(c=>!index.land.has(key(c)))) return {error:'Reclaim the water before building here.'};
  if(footprint.some(c=>index.occupied.has(key(c)))) return {error:'This space is already occupied.'};
  // Dungeon placement is a layout-editor action, not native spawning gameplay.
  if(item.kind==='dungeon')return BUILDER_ASSETS.dungeons[String(item.dungeonChipId)]?{}:{error:'Unknown dungeon.'};
  if(isHall(item)) {
    if(state.items.filter(i=>isHall(i)&&i.id!==item.id).length>=5) return {error:'You can place up to five Town Halls.'};
  }
  const townRule=townPlacementRule(item);
  if(townRule==='inside'&&footprint.some(c=>!index.covered.has(key(c)))) return {error:'The whole footprint must be inside town coverage.'};
  if(townRule==='outside'&&footprint.some(c=>index.covered.has(key(c)))) return {error:'The whole footprint must be outside existing town coverage.'};
  // ChipPlaceSystem.CheckPlace 0x15087a4..0x1508814 calls
  // ContainsExpansionChip: another non-hall expansion's full range rectangle
  // excludes placement, across facility types (not just identical facilities).
  if(!isHall(item)&&BUILDER_ASSETS.facilities[String(item.facilityId)]?.expandsTown) {
    for(const other of state.items) {
      if(other.id===item.id||isHall(other)||other.parentId||!BUILDER_ASSETS.facilities[String(other.facilityId)]?.expandsTown)continue;
      const r=facilityById.get(other.facilityId!)?.validRange??0;
      const [w,h]=dimensions(other);
      if(footprint.some(c=>c.x>=other.x-r&&c.x<other.x+w+r&&c.y>=other.y-r&&c.y<other.y+h+r))
        return {error:`Too close to ${itemName(other)}. Place the whole facility outside its expansion area.`};
    }
  }
  return {};
}
export function removeItem(state:BuilderState,id:string): {state:BuilderState;error?:string} {
  const item=state.items.find(i=>i.id===id);
  if(!item || item.fixed) return {state,error:'This is a fixed structure.'};
  if(isMapStructure(item)) return {state,error:'Original map buildings can only be moved, not removed.'};
  return {state:{...state,items:state.items.filter(i=>i.id!==id&&i.parentId!==id)}};
}
export function rotateItem(item:BuilderItem):BuilderItem {
  if(item.fixed || isMapStructure(item)) return item;
  const allowed=allowedDirections(item);
  const current=allowed.indexOf(item.direction);
  const next=item.kind==='facility'&&item.facilityId===gateFacilityId&&current<0?legalGateDirection(item.direction):allowed[current<0?0:(current+1)%allowed.length];
  // Plot fixtures keep their own facing offset; the gate has no separate facing.
  const facing=item.kind==='facility'&&item.facilityId===gateFacilityId?next:((item.facing??item.direction)+1)%4;
  return {...item,direction:next,facing};
}
export function rotateContents(plot:BuilderItem,items:BuilderItem[]):BuilderItem[] {
  const [w,h]=dimensions(plot);
  return items.filter(i=>i.parentId===plot.id).map(i=>{
    const [,ih]=dimensions(i);const c=rotateLocal({x:i.x-plot.x,y:i.y-plot.y},w,h,1);
    return {...i,direction:(i.direction+1)%4,facing:((i.facing??i.direction)+1)%4,x:plot.x+c.x-ih+1,y:plot.y+c.y};
  });
}
export function reclaimError(cell:Cell,state:BuilderState,index:PlacementIndex,undo=false):string|null {
  if(!inBounds(cell)) return 'Stay inside the world map.';
  if(undo) {
    if(!state.reclaimed.some(c=>key(c)===key(cell))) return 'Default land cannot be turned into water.';
    if(index.occupied.has(key(cell))) return 'Remove the building or road first.';
    return null;
  }
  if(index.land.has(key(cell))) return 'This is already land.';
  if(index.occupied.has(key(cell))) return 'This is a fixed structure.';
  if(!index.covered.has(key(cell))) return 'Reclaim land within a town boundary.';
  return null;
}
export function decodeWorld(text:string):BuilderState {
  const s=JSON.parse(text) as BuilderState;
  if(s.version!==1||!Array.isArray(s.items)||s.items.length>20000||!Array.isArray(s.reclaimed)||s.reclaimed.length>25600) throw Error('Unsupported save');
  for(const item of s.items)if(isHall(item)&&Number.isSafeInteger(item.level))item.level=Math.min(MAX_TOWN_LEVEL,item.level);
  const ids=new Set<string>();
  for(const i of s.items) {
    if(typeof i.id!=='string'||ids.has(i.id)||!inBounds(i)||!['facility','plot','road','dungeon'].includes(i.kind)||![0,1,2,3].includes(i.direction)||!Number.isSafeInteger(i.level)||i.level<1||!Number.isInteger(i.fullness)||i.fullness<0||i.fullness>4) throw Error('Invalid saved item');
    if(i.kind==='dungeon'&&!BUILDER_ASSETS.dungeons[String(i.dungeonChipId)])throw Error('Unknown dungeon');
    if(i.petId!==undefined&&(!canHousePet(i)||!BUILDER_PETS.some(p=>p.id===i.petId)))throw Error('Invalid pet assignment');
    if(i.kind==='facility'&&!BUILDER_ASSETS.facilities[String(i.facilityId)]) throw Error('Unknown facility');
    if(i.kind==='plot'&&(!PLOT_SIZES.includes(i.size!)||(i.houseId!==undefined&&!BUILDINGS.some(b=>b.id===i.houseId)))) throw Error('Unknown plot');
    if(cells(i).some(c=>!inBounds(c))) throw Error('Out of bounds');
    ids.add(i.id);
  }
  if(s.reclaimed.some(c=>!inBounds(c))) throw Error('Invalid reclaimed land');
  if(s.items.some(i=>i.parentId&&!s.items.some(p=>p.id===i.parentId&&p.kind==='plot'))) throw Error('Missing parent plot');
  // User-confirmed Inn exception: both initial Guest Beds can be replaced.
  for(const item of s.items)if(item.facilityId===155&&s.items.some(p=>p.id===item.parentId&&p.kind==='plot'&&p.houseId===7))item.fixed=false;
  // User-confirmed gameplay rule: gates only exist in the two legal facings.
  // Old saves keep their cells and snap onto the legal facing of the same axis.
  for(const item of s.items)if(item.kind==='facility'&&item.facilityId===gateFacilityId&&!gateDirections.includes(item.direction))
    {item.direction=legalGateDirection(item.direction);item.facing=item.direction;}
  for(const fixed of initialWorld().items.filter(i=>i.fixed)) {
    const saved=s.items.find(i=>i.id===fixed.id);
    if(!saved||saved.x!==fixed.x||saved.y!==fixed.y||saved.facilityId!==fixed.facilityId||!saved.fixed) throw Error('Fixed structure changed');
  }
  // Recover landmarks deleted by the first builder. Keep user objects and moved
  // landmarks exactly where saved; a backup is made by the persistence owner.
  for(const original of initialWorld().items) {
    const saved=s.items.find(i=>i.id===original.id);
    if(saved&&saved.facilityId!==original.facilityId)throw Error('Map structure changed');
    if(!saved)s.items.push(original);
  }
  return {...s,geometryRevision:2};
}

export const supportsLinePlacement = (item:BuilderItem) => item.kind==='road'||(item.kind==='facility'&&[3,4,23,24,25,26].includes(item.facilityId!));
// Edge-connected raster line, including intermediate cells on fast pointer moves.
export function buildLine(start:Cell,end:Cell):Cell[] {
  const result=[{...start}];let {x,y}=start;
  const dx=Math.abs(end.x-x),dy=Math.abs(end.y-y),sx=Math.sign(end.x-x),sy=Math.sign(end.y-y);
  let ix=0,iy=0;
  while(ix<dx||iy<dy) {
    if(ix<dx&&(iy===dy||(1+2*ix)*dy<(1+2*iy)*dx)){x+=sx;ix++;}
    else{y+=sy;iy++;}
    result.push({x,y});
  }
  return result;
}
export function placeLine(state:BuilderState,land:Set<string>,draft:BuilderItem,start:Cell,end:Cell) {
  const index=placementIndex(state,land);const added:BuilderItem[]=[];let skipped=0;
  if(!supportsLinePlacement(draft))return {state,added:0,skipped:0};
  for(const c of buildLine(start,end)) {
    const item={...draft,...c,id:crypto.randomUUID()};
    if(validatePlacement(item,state,index).error){skipped++;continue;}
    added.push(item);for(const cell of cells(item))index.occupied.set(key(cell),item);
  }
  return {state:{...state,items:[...state.items,...added]},added:added.length,skipped};
}
// Removing works like building: the same dragged line clears the walls, fences
// and paths it crosses. Fixed structures and other facilities are left alone.
export function lineTargets(state:BuilderState,start:Cell,end:Cell) {
  const path=new Set(buildLine(start,end).map(key));
  return state.items.filter(i=>!i.fixed&&!isMapStructure(i)&&supportsLinePlacement(i)&&cells(i).some(c=>path.has(key(c))));
}
export function removeLine(state:BuilderState,start:Cell,end:Cell) {
  const targets=lineTargets(state,start,end);
  if(!targets.length)return {state,removed:0};
  const ids=new Set(targets.map(i=>i.id));
  return {state:{...state,items:state.items.filter(i=>!ids.has(i.id)&&!ids.has(i.parentId??''))},removed:targets.length};
}
