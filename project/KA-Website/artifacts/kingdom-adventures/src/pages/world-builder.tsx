import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RotateCw, Move, Layers, X, Undo2, Redo2, ZoomIn, ZoomOut, LocateFixed, Package, Waves, Search, Plus, Trash2, Maximize2, Minimize2, Eye, EyeOff } from 'lucide-react';
import RuntimeWorldRenderTestPage from './runtime-world-render-test';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { BUILDINGS, PLOT_SIZES, PLOT_TILES } from '@/game-data/buildings';
import { FACILITIES, FACILITY_TABS } from '@/game-data/facilities';
import nativeGround from '@/game-data/native-map-ground.json';
import { BUILDER_ASSETS, BUILDER_PETS, placementLimit, placedCount, requiresTownCoverage, canHousePet, assignPet, SAVE_KEY, surroundCoverage, isManualFacility, containingPlot, supportHeight, MAX_TOWN_LEVEL, allowedDirections, buildLine, placeLine, supportsLinePlacement, cells, contains, decodeWorld, dimensions, initialFurniture, initialWorld, isHall, isOriginalMapPlacement, isMapStructure, itemName, key, lineTargets, makeItem, placementIndex, previewTerritory, reclaimError, removeItem, removeLine, rotateItem, rotateContents, rotatedDirections, territory, townRadius, validatePlacement, type BuilderItem, type BuilderState, type Cell } from '@/lib/world-builder';
import { builderDraws, diamond, drawBuilder, hitBuilder } from '@/lib/world-builder-render';

import { BuilderSpriteCache } from '@/lib/builder-sprite-cache';

function BuildIcon({remove=false,large=false}:{remove?:boolean;large?:boolean}) {return <img alt="" className={`${large?'h-12 w-12':'mr-2 h-8 w-8'} object-contain [image-rendering:pixelated]`} src={`${import.meta.env.BASE_URL}world-assets/builder/${remove?'remove':'build'}-icon.png`}/>;}

function TownLevelInput({level,onCommit}:{level:number;onCommit:(level:number)=>boolean}) {
  const [text,setText]=useState(String(level));
  useEffect(()=>setText(String(level)),[level]);
  const apply=()=>{
    const parsed=Number(text);
    if(text.trim()===''||!Number.isInteger(parsed)){setText(String(level));return;}
    const next=Math.min(MAX_TOWN_LEVEL,Math.max(1,parsed));
    if(next===level||onCommit(next))setText(String(next));else setText(String(level));
  };
  return <input className="ml-2 w-24 rounded border bg-background p-1" aria-label="Town Hall level" type="number" min="1" max={MAX_TOWN_LEVEL} value={text} onChange={e=>setText(e.target.value)} onBlur={apply} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();if(e.key==='Escape'){setText(String(level));e.stopPropagation();}}}/>;
}

type Tool = 'select'|'remove'|'reclaim'|'unclaim'|'place';
const fixedIds=[196];
const natureVisibility={terrain:false,resources:false,humans:false,special:false};
const catalog=FACILITIES.filter(f=>isManualFacility(f.id)&&f.tab!=='map'&&![7,10,180,196].includes(f.id)&&!!BUILDER_ASSETS.facilities[String(f.id)]);
const iconClass='h-20 w-full object-contain [image-rendering:pixelated]';
// Menu footprint uses the orientation a fresh draft starts in, so a Gate reads
// as its real 1x2 wall slot instead of the raw asset 2x1.
function defaultFootprint(facilityId:number) {
  const item:BuilderItem={id:'menu',kind:'facility',facilityId,x:0,y:0,direction:0,level:1,fullness:0};
  return dimensions({...item,direction:allowedDirections(item)[0]});
}
type BuildSlot = { id: string; name: string; state: BuilderState };
const BUILDS_KEY = 'ka-world-builder-builds-v1';
const MAX_BUILDS = 3; // User-requested second and third build tabs.
function readBuilds(): {builds:BuildSlot[];activeId:string;error:string;migrated:boolean} {
  const fresh=()=>[{id:'build-1',name:'Build 1',state:initialWorld()}];
  try {
    const list=localStorage.getItem(BUILDS_KEY);
    if(list) {
      const doc=JSON.parse(list) as {version?:number;activeId?:string;builds?:{id?:unknown;name?:unknown;state?:unknown}[]};
      if(doc.version!==2||!Array.isArray(doc.builds)||!doc.builds.length)throw Error('Unsupported build list');
      const builds=doc.builds.slice(0,MAX_BUILDS).map((entry,index)=>{
        if(typeof entry?.id!=='string'||!entry.id)throw Error('Invalid build id');
        return {id:entry.id,name:typeof entry.name==='string'&&entry.name.trim()?entry.name:`Build ${index+1}`,state:decodeWorld(JSON.stringify(entry.state))};
      });
      const activeId=builds.some(b=>b.id===doc.activeId)?doc.activeId!:builds[0].id;
      return {builds,activeId,error:'',migrated:false};
    }
    const raw=localStorage.getItem(SAVE_KEY);
    const state=raw?decodeWorld(raw):initialWorld();
    const migrated=!!raw&&JSON.parse(raw).geometryRevision!==2;
    if(raw&&!localStorage.getItem(`${SAVE_KEY}-before-build-tabs`))localStorage.setItem(`${SAVE_KEY}-before-build-tabs`,raw);
    if(migrated&&!localStorage.getItem(`${SAVE_KEY}-before-assembly-fix`))localStorage.setItem(`${SAVE_KEY}-before-assembly-fix`,raw);
    return {builds:fresh().map(b=>({...b,state})),activeId:'build-1',error:'',migrated};
  }
  catch {return {builds:fresh(),activeId:'build-1',error:'Your existing save could not be loaded. It has been preserved; automatic saving is paused.',migrated:false};}
}

export default function WorldBuilderPage() {
  const [loaded]=useState(readBuilds);
  const [slots,setSlots]=useState<BuildSlot[]>(loaded.builds);
  const [activeId,setActiveId]=useState(loaded.activeId);
  const active=slots.find(slot=>slot.id===activeId)??slots[0];
  const world=active.state;
  const setWorld=useCallback((next:BuilderState)=>setSlots(list=>list.map(slot=>slot.id===activeId?{...slot,state:next}:slot)),[activeId]);
  const [saveError,setSaveError]=useState(loaded.error);
  const [saved,setSaved]=useState(false);
  const [land,setLand]=useState<Set<string>>(new Set());
  const [tool,setTool]=useState<Tool>('select');
  const [draft,setDraft]=useState<BuilderItem|null>(null);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [linePreview,setLinePreview]=useState<Cell[]>([]);
  const [hover,setHover]=useState<Cell|null>(null);
  const [buildOpen,setBuildOpen]=useState(false);
  const [assignOpen,setAssignOpen]=useState(false);
  const [petOpen,setPetOpen]=useState(false);
  const [petQuery,setPetQuery]=useState('');
  const [tab,setTab]=useState('env');
  const [query,setQuery]=useState('');
  const [message,setMessage]=useState(loaded.migrated?'Save updated: original map buildings restored if missing; storage now occupies 2×2. Your old save is backed up. Move any objects that now overlap.':'Start with a Town Hall. Drag anywhere to pan; pinch or use + / − to zoom.');
  const [messageTone,setMessageTone]=useState<'info'|'error'>('info');
  const [flash,setFlash]=useState<{text:string;token:number}|null>(null);
  const flashToken=useRef(0);
  const [boundary,setBoundary]=useState(true);
  const [levels,setLevels]=useState(false);
  const [surround,setSurround]=useState(false);
  const [grid,setGrid]=useState(false);
  const [resetOpen,setResetOpen]=useState(false);
  const [deleteOpen,setDeleteOpen]=useState(false);
  const [fullscreen,setFullscreen]=useState(false);
  const [fullscreenFallback,setFullscreenFallback]=useState(false);
  const [fullscreenRoot,setFullscreenRoot]=useState<HTMLDivElement|null>(null);
  const fullscreenRef=useRef<HTMLDivElement|null>(null);
  const attachFullscreenRoot=useCallback((node:HTMLDivElement|null)=>{fullscreenRef.current=node;setFullscreenRoot(node);},[]);
  const isFullscreen=fullscreen||fullscreenFallback;
  const [zoomRequest,setZoomRequest]=useState<{factor:number;token:number}|null>(null);
  const [focus,setFocus]=useState<{x:number;y:number;token:number}|null>({x:112,y:136,token:0});
  const [history,setHistory]=useState<Record<string,{undo:BuilderState[];redo:BuilderState[]}>>({});
  const hist=history[activeId]??{undo:[] as BuilderState[],redo:[] as BuilderState[]};
  const [spriteCache]=useState(()=>new BuilderSpriteCache());
  const [imageVersion,setImageVersion]=useState(0);
  const selected=world.items.find(i=>i.id===selectedId);
  const moving=!!draft&&world.items.some(i=>i.id===draft.id);
  const draftAsset=BUILDER_ASSETS.facilities[String(draft?.facilityId)];
  const canRotateDraft=!!draft&&rotatedDirections(draft)&&!moving&&!draft.fixed&&!isMapStructure(draft)&&(draft.kind==='plot'||!!draftAsset&&!draftAsset.barrierFrames&&(draftAsset.width!==draftAsset.height||new Set(draftAsset.variants).size>1));
  const index=useMemo(()=>placementIndex(world,land,moving?draft?.id:undefined),[world,land,moving,draft?.id]);
  const ready=land.size>0;
  // A hall being moved carries its town boundary with it.
  const covered=useMemo(()=>previewTerritory(world.items,moving&&draft&&isHall(draft)?draft:null),[world.items,moving,draft]);
  const showBoundary=boundary||tool==='place';
  const ground=useMemo(()=>[...nativeGround.facilityGround.map(([x,y])=>({x,y})),...world.reclaimed],[world.reclaimed]);
  const onTerrainReady=useCallback((value:Set<string>)=>setLand(previous=>previous.size===value.size&&[...value].every(k=>previous.has(k))?previous:value),[]);
  useEffect(()=>{
    if(loaded.error) return;
    setSaved(false);
    try{
      // The tab list is the owner of every build; SAVE_KEY keeps the active one
      // readable for older code and for the pre-tabs backup file.
      localStorage.setItem(BUILDS_KEY,JSON.stringify({version:2,activeId,builds:slots}));
      localStorage.setItem(SAVE_KEY,JSON.stringify(world));
      setSaved(true);setSaveError('');
    }catch{setSaveError('Browser storage is unavailable or full. Keep this page open to preserve your changes.');}
  },[world,slots,activeId,loaded.error]);
  const announce=(text:string)=>{setMessageTone('info');setMessage(text);};
  // Reasons appear in red and, while full screen, as a short-lived popup.
  const complain=(text:string)=>{setMessageTone('error');setMessage(text);setFlash({text,token:++flashToken.current});};
  useEffect(()=>{
    if(!flash)return;
    const token=flash.token;
    const timer=setTimeout(()=>setFlash(current=>current&&current.token===token?null:current),3000);
    return ()=>clearTimeout(timer);
  },[flash]);
  useEffect(()=>{
    const sync=()=>setFullscreen(!!document.fullscreenElement||fullscreenFallback);
    document.addEventListener('fullscreenchange',sync);
    return ()=>document.removeEventListener('fullscreenchange',sync);
  },[fullscreenFallback]);
  const toggleFullscreen=()=>{
    const element=fullscreenRef.current;
    if(!isFullscreen){
      const request=element?.requestFullscreen?.bind(element);
      if(request)request().catch(()=>setFullscreenFallback(true));
      else setFullscreenFallback(true);
    }
    else if(document.fullscreenElement)void document.exitFullscreen().catch(()=>setFullscreenFallback(false));
    else setFullscreenFallback(false);
  };

  const commit=(next:BuilderState,notice:string)=>{
    // A hall move/rank reduction or removed expansion must not strand buildings.
    const area=territory(next.items);
    const unsupported=next.items.find(i=>requiresTownCoverage(i)&&!i.fixed&&!isOriginalMapPlacement(i)&&cells(i).some(c=>!area.has(key(c))));
    if(unsupported){complain(`That would leave ${itemName(unsupported)} outside town coverage. Move or remove it first.`);return false;}
    setHistory(h=>({...h,[activeId]:{undo:[...(h[activeId]?.undo??[]).slice(-39),world],redo:[]}}));
    setWorld(next);announce(notice);return true;
  };
  const cancel=useCallback(()=>{setTool('select');setDraft(null);setSelectedId(null);setLinePreview([]);},[]);
  useEffect(()=>{const handle=(e:KeyboardEvent)=>{if(e.key==='Escape')cancel();};window.addEventListener('keydown',handle);return()=>window.removeEventListener('keydown',handle);},[cancel]);
  const choose=(item:BuilderItem)=>{setDraft(item);setTool('place');setSelectedId(null);setBuildOpen(false);announce(`Place ${itemName(item)}. Every square must fit in a highlighted area.${supportsLinePlacement(item)?' Hold and drag to build a line; Shift-drag to pan.':''}`);};
  const chooseTerrain=(value:'reclaim'|'unclaim')=>{setDraft(null);setTool(value);setSelectedId(null);setBuildOpen(false);announce(value==='reclaim'?'Tap water within town coverage to reclaim it.':'Tap empty reclaimed land to return it to water. Default land is protected.');};
  const remove=(item:BuilderItem)=>{
    const result=removeItem(world,item.id);
    if(result.error){complain(result.error);return;}
    if(commit(result.state,`${itemName(item)} removed.`))setSelectedId(null);
  };
  // Removing mirrors building: drag across walls, fences or paths to clear them.
  const stripLine=(start:Cell,end:Cell,phase:'preview'|'commit'|'cancel')=>{
    if(phase==='preview'){setLinePreview(buildLine(start,end));return;}
    setLinePreview([]);
    if(phase==='cancel')return;
    const result=removeLine(world,start,end);
    if(!result.removed)return;
    commit(result.state,`${result.removed} wall, fence or path tile${result.removed===1?'':'s'} removed.`);
  };
  const tap=(c:Cell)=>{
    if(!ready)return;
    setHover(c);
    if(tool==='reclaim'||tool==='unclaim') {
      const error=reclaimError(c,world,index,tool==='unclaim');if(error){complain(error);return;}
      commit({...world,reclaimed:tool==='reclaim'?[...world.reclaimed,c]:world.reclaimed.filter(p=>key(p)!==key(c))},tool==='reclaim'?'Land reclaimed.':'Reclaimed land returned to water.');return;
    }
    if(tool==='place'&&draft) {
      const item={...draft,...c};const result=validatePlacement(item,world,index);
      if(result.error){complain(result.error);return;}
      item.parentId=result.parentId;
      const children=moving?world.items.filter(i=>i.parentId===item.id).map(i=>({...i,x:i.x+item.x-draft.x,y:i.y+item.y-draft.y})):[];
      const next={...world,items:[...world.items.filter(i=>i.id!==item.id&&i.parentId!==item.id),item,...children]};
      if(!commit(next,`${itemName(item)} placed.`))return;
      if(moving){setTool('select');setDraft(null);setSelectedId(item.id);}
      else if(item.kind==='plot'){setTool('select');setDraft(null);setSelectedId(item.id);setAssignOpen(true);}
      else if(placementLimit(item.facilityId)!==undefined&&placedCount(next.items,item.facilityId!)>=placementLimit(item.facilityId)!) {setTool('select');setDraft(null);setSelectedId(item.id);announce(`${itemName(item)} placed. All ${placementLimit(item.facilityId)} available copies are placed.`);}
      else {setDraft({...draft,id:crypto.randomUUID()});setHover(null);}
      return;
    }
    const hit=[...world.items].reverse().find(i=>i.parentId&&contains(i,c))??[...world.items].reverse().find(i=>contains(i,c));
    if(tool==='remove') {
      if(hit)remove(hit);
      else if(world.reclaimed.some(p=>key(p)===key(c))) {const error=reclaimError(c,world,index,true);if(error)complain(error);else commit({...world,reclaimed:world.reclaimed.filter(p=>key(p)!==key(c))},'Reclaimed land returned to water.');}
      return;
    }
    setSelectedId(hit?.id??null);
  };
  const rotateSelected=()=>{
    if(!selected||selected.fixed||isMapStructure(selected))return;
    const rotated=rotateItem(selected);
    const result=validatePlacement(rotated,world,placementIndex(world,land,selected.id));
    if(result.error){complain(result.error);return;}
    const children=rotateContents(selected,world.items);
    commit({...world,items:world.items.map(i=>i.id===selected.id?rotated:children.find(c=>c.id===i.id)??i)},'Rotated.');
  };
  const assign=(houseId:number)=>{
    if(!selected||selected.kind!=='plot')return;
    const plot={...selected,houseId};
    if(commit({...world,items:[...world.items.filter(i=>i.id!==plot.id&&i.parentId!==plot.id),plot,...initialFurniture(plot)]},`${itemName(plot)} assigned.`))setAssignOpen(false);
  };
  const ghost=tool==='place'&&draft&&hover?{...draft,...hover}:null;
  const ghostError=ghost?validatePlacement(ghost,world,index).error:undefined;
  const effectSource=ghost??selected;
  const effectCells=useMemo(()=>new Set(surround&&effectSource?surroundCoverage(effectSource).map(key):[]),[surround,effectSource]);
  const draws=useMemo(()=>builderDraws(world,ghost,!ghostError),[world,ghost?.x,ghost?.y,draft,ghostError]);
  useEffect(()=>spriteCache.subscribe(()=>setImageVersion(v=>v+1)),[spriteCache]);
  useEffect(()=>{
    for(const id of new Set(draws.map(d=>d.sprite))) {
      const sprite=BUILDER_ASSETS.sprites[id];
      if(sprite)spriteCache.load(id,`${import.meta.env.BASE_URL}${sprite.url.replace(/^\//,'')}`);
    }
  },[draws,spriteCache,imageVersion]);
  const validAnchors=useMemo(()=>{
    if(!ready||tool==='select'||tool==='remove')return null;
    const valid=new Set<string>();
    for(let y=0;y<160;y++)for(let x=0;x<160;x++) {
      const c={x,y};
      const error=tool==='place'&&draft?validatePlacement({...draft,...c},world,index).error:reclaimError(c,world,index,tool==='unclaim');
      if(!error)valid.add(key(c));
    }
    return valid;
  },[tool,draft,world,index,ready]);
  const lineRemoveCells=useMemo(()=>{
    if(tool!=='remove'||!linePreview.length)return null;
    const path=new Set(linePreview.map(key));
    const marked=new Set<string>();
    for(const item of lineTargets(world,linePreview[0],linePreview[linePreview.length-1]))
      for(const c of cells(item))if(path.has(key(c)))marked.add(key(c));
    return marked;
  },[tool,linePreview,world]);
  const drawLayer=useCallback((ctx:CanvasRenderingContext2D,camera:{offsetX:number;offsetY:number;zoom:number},width:number,height:number)=>{
    drawBuilder(ctx,camera,width,height,draws,spriteCache.images);
    // Screen culling avoids painting 25,600 diamonds when zoomed in.
    for(let y=0;y<160;y++)for(let x=0;x<160;x++) {
      const px=camera.offsetX+24*(x-y)*camera.zoom,py=camera.offsetY+12*(x+y)*camera.zoom;
      if(px < -30||px>width+30||py < -20||py>height+20)continue;
      const c={x,y},k=key(c);
      if(effectCells.has(k))diamond(ctx,c,camera,'rgba(168,85,247,0.24)','rgba(192,132,252,0.65)');
      if(validAnchors&&!validAnchors.has(k))diamond(ctx,c,camera,'rgba(10,18,26,0.57)');
      else if(showBoundary&&covered.has(k)) {
        const edge=[[x-1,y],[x+1,y],[x,y-1],[x,y+1]].some(([xx,yy])=>!covered.has(`${xx},${yy}`));
        if(edge)diamond(ctx,c,camera,'rgba(250,204,21,0.18)','rgba(250,204,21,0.7)');
      }
    }
    for(const c of linePreview){
      const k=key(c);
      if(tool==='remove'){const hit=!!lineRemoveCells?.has(k);diamond(ctx,c,camera,hit?'rgba(239,68,68,0.5)':'rgba(15,23,42,0.35)',hit?'#f87171':undefined);continue;}
      const valid=validAnchors?.has(k);diamond(ctx,c,camera,valid?'rgba(34,197,94,0.4)':'rgba(239,68,68,0.4)',valid?'#4ade80':'#f87171');
    }
    if(selected)for(const c of cells(selected))diamond(ctx,c,camera,'rgba(56,189,248,0.12)','#38bdf8',supportHeight(world.items.find(p=>p.id===selected.parentId)));
    if(ghost){const elevation=supportHeight(containingPlot(ghost,world));const valid=validAnchors?.has(key(ghost));for(const c of cells(ghost))diamond(ctx,c,camera,valid?'rgba(34,197,94,0.25)':'rgba(239,68,68,0.35)',valid?'#4ade80':'#f87171',elevation);}
  },[draws,imageVersion,covered,showBoundary,validAnchors,selected,ghost?.x,ghost?.y,linePreview,lineRemoveCells,tool,effectCells]);
  const changeHistory=(back:boolean)=>{
    const list=back?hist.undo:hist.redo;const next=list.at(-1);if(!next)return;
    setHistory(h=>({...h,[activeId]:back?{undo:list.slice(0,-1),redo:[...hist.redo,world]}:{undo:[...hist.undo,world],redo:list.slice(0,-1)}}));
    setWorld(next);cancel();announce(back?'Change undone.':'Change restored.');
  };
  const focusTown=(state:BuilderState)=>{const hall=state.items.find(isHall);setFocus({x:hall?.x??112,y:hall?.y??136,token:Date.now()});};
  const switchBuild=(id:string)=>{
    if(id===activeId)return;
    const target=slots.find(slot=>slot.id===id);if(!target)return;
    cancel();setActiveId(id);focusTown(target.state);announce(`${target.name} opened.`);
  };
  const addBuild=()=>{
    if(slots.length>=MAX_BUILDS)return;
    const used=new Set(slots.map(slot=>slot.name));
    const index=Array.from({length:MAX_BUILDS},(_,i)=>i+1).find(n=>!used.has(`Build ${n}`))??slots.length+1;
    const slot:BuildSlot={id:crypto.randomUUID(),name:`Build ${index}`,state:initialWorld()};
    cancel();setSlots(list=>[...list,slot]);setActiveId(slot.id);focusTown(slot.state);
    announce(`${slot.name} added: an empty world with the original map structures.`);
  };
  const resetBuild=()=>{
    const fresh=initialWorld();
    if(commit(fresh,`${active.name} reset to the original map.`)){setResetOpen(false);cancel();focusTown(fresh);}
  };
  const deleteBuild=()=>{
    if(slots.length<2)return;
    const remaining=slots.filter(slot=>slot.id!==activeId);
    setHistory(previous=>{const next={...previous};delete next[activeId];return next;});
    cancel();setSlots(remaining);setActiveId(remaining[0].id);focusTown(remaining[0].state);
    setDeleteOpen(false);announce(`${active.name} deleted. ${remaining[0].name} opened.`);
  };
  const visible=catalog.filter(f=>(f.tab===tab||(tab==='amenity'&&f.tab==='map'))&&f.name.toLowerCase().includes(query.toLowerCase()));

  return <div ref={attachFullscreenRoot} className={isFullscreen?'fixed inset-0 z-40 flex h-screen w-screen max-w-none flex-col gap-2 overflow-hidden bg-background p-2':'mx-auto max-w-[2400px] px-2 py-4 sm:px-4'}>
    {!isFullscreen&&<div role="tablist" aria-label="Builds" className="flex flex-wrap items-center gap-2">
      {slots.map(slot=><button key={slot.id} role="tab" aria-selected={slot.id===activeId} className={`rounded-lg border px-3 py-2 text-sm font-medium ${slot.id===activeId?'border-primary bg-primary text-primary-foreground':'bg-card hover:bg-muted'}`} onClick={()=>switchBuild(slot.id)}>{slot.name}<span className="ml-2 text-xs font-normal opacity-80">{slot.state.items.filter(isHall).length}/5 towns</span></button>)}
      <button role="tab" aria-selected={false} disabled={slots.length>=MAX_BUILDS} className="rounded-lg border border-dashed px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50" onClick={addBuild} title={slots.length>=MAX_BUILDS?`Up to ${MAX_BUILDS} builds`:undefined}><Plus className="mr-1 inline h-4 w-4"/>Add build</button>
      <span className="text-xs text-muted-foreground">Each build keeps its own town, undo history and save.</span>
    </div>}
    {!isFullscreen&&<div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div><h1 className="text-2xl font-bold">World Builder</h1><p className="text-sm text-muted-foreground">Build your kingdom · {world.items.filter(isHall).length}/5 towns</p></div>
      <span className={`text-xs ${saveError?'text-destructive':'text-muted-foreground'}`} role="status">{saveError|| (saved?'Saved in this browser':'Saving…')}</span>
    </div>}
    <div className="mb-2 flex flex-wrap gap-2" aria-label="World builder tools">
      <Button variant={levels?'secondary':'outline'} aria-pressed={levels} onClick={()=>setLevels(!levels)}><Layers className="mr-2 h-4 w-4"/>Levels</Button>
      <Button variant={surround?'secondary':'outline'} aria-pressed={surround} onClick={()=>setSurround(!surround)}><Layers className="mr-2 h-4 w-4"/>Surround</Button>
      <Button variant="outline" aria-pressed={grid} onClick={()=>setGrid(!grid)}>Grid</Button>
      <Button variant={boundary?'outline':'secondary'} aria-pressed={!boundary} title="Show or hide the yellow town build boundary while not placing a building" onClick={()=>setBoundary(!boundary)}>{boundary?<EyeOff className="mr-2 h-4 w-4"/>:<Eye className="mr-2 h-4 w-4"/>}Boundary</Button>
      <Button variant="outline" size="icon" aria-label="Undo" disabled={!hist.undo.length} onClick={()=>changeHistory(true)}><Undo2 className="h-4 w-4"/></Button>
      <Button variant="outline" size="icon" aria-label="Redo" disabled={!hist.redo.length} onClick={()=>changeHistory(false)}><Redo2 className="h-4 w-4"/></Button>
      <Button variant="outline" size="icon" aria-label="Zoom in" onClick={()=>setZoomRequest(v=>({factor:1.3,token:(v?.token??0)+1}))}><ZoomIn className="h-4 w-4"/></Button>
      <Button variant="outline" size="icon" aria-label="Zoom out" onClick={()=>setZoomRequest(v=>({factor:1/1.3,token:(v?.token??0)+1}))}><ZoomOut className="h-4 w-4"/></Button>
      <Button variant="outline" size="icon" aria-label="Center on town" onClick={()=>{const hall=world.items.find(isHall);setFocus({x:hall?.x??112,y:hall?.y??136,token:Date.now()});}}><LocateFixed className="h-4 w-4"/></Button>
      <Button variant="outline" aria-label={isFullscreen?'Exit full screen':'Full screen'} aria-pressed={isFullscreen} onClick={toggleFullscreen}>{isFullscreen?<Minimize2 className="mr-2 h-4 w-4"/>:<Maximize2 className="mr-2 h-4 w-4"/>}{isFullscreen?'Exit':'Full screen'}</Button>
      <Button variant="outline" disabled={slots.length<2} title={slots.length<2?'Keep at least one build':`Delete ${active.name}`} onClick={()=>setDeleteOpen(true)}><Trash2 className="mr-2 h-4 w-4"/>Delete build</Button>
      <Button variant="destructive" onClick={()=>setResetOpen(true)}><Trash2 className="mr-2 h-4 w-4"/>Reset builder</Button>
    </div>
    {!isFullscreen&&<div className="flex min-h-10 items-center gap-2 rounded border bg-card px-3 py-2 text-sm" role="status">
      <span className={`flex-1 ${messageTone==='error'?'font-medium text-red-500':''}`}>{!ready?'Loading the world map…':message}</span>
    </div>}
    {surround&&<p className="my-2 text-xs text-muted-foreground" role="status">{!effectSource?'Select a facility or choose one to place to see its surround coverage.':effectCells.size?`${itemName(effectSource)}: purple marks its 3-cell surround range. Targets must qualify for its effects.`:'This selection has no surround effect.'}</p>}
    {!!spriteCache.errors.size&&<p role="alert">Some building sprites failed to load. <button className="underline" onClick={()=>spriteCache.retry()}>Retry sprites</button></p>}
    <div className={isFullscreen?'relative min-h-0 flex-1':'relative'}>
      {flash&&<div role="alert" className="pointer-events-none absolute left-1/2 top-2 z-30 max-w-[calc(100%-1rem)] -translate-x-1/2 rounded-lg border border-red-400 bg-red-950/95 px-3 py-2 text-center text-sm font-medium text-red-100 shadow-lg">{flash.text}</div>}
      <RuntimeWorldRenderTestPage publicMode initialZoom={.65} zoomRequest={zoomRequest} focusCell={focus} showLevelOverlay={levels} showCellGrid={grid} editorCellPicking interactiveInPublicMode hideNatureToggleButtons initialNatureVisibility={natureVisibility} visibleOnePieceFacilityIds={fixedIds} landOverrideCells={ground} reclaimedTintCells={world.reclaimed} onTerrainReady={onTerrainReady} onEditorLine={tool==='place'&&draft&&!moving&&supportsLinePlacement(draft)?(start,end,phase)=>{
        if(phase==='preview'){setLinePreview(buildLine(start,end));return;}
        setLinePreview([]);
        if(phase==='cancel')return;
        const result=placeLine(world,land,draft,start,end);
        if(result.added)commit(result.state,`${result.added} ${itemName(draft)} tiles placed.${result.skipped?` ${result.skipped} blocked tiles skipped.`:''}`);
        else complain('No tiles placed: every tile is blocked or fails this facility placement rules.');
      }:tool==='remove'&&ready?stripLine:undefined} editorLineAllowed={tool==='remove'?cell=>world.items.some(i=>!i.fixed&&supportsLinePlacement(i)&&contains(i,cell)):undefined} fillHeight={isFullscreen} editorCellElevation={cell=>{const plot=containingPlot({id:'surface-probe',kind:'road',direction:0,level:1,fullness:0,...cell},world);return supportHeight(plot);}} onCellClick={tap} onCellHover={setHover} drawEditorLayer={drawLayer} onEditorObjectClick={(point,camera)=>{
        if(tool!=='select'&&tool!=='remove')return false;
        const id=hitBuilder(point,camera,draws,spriteCache.alpha);const item=world.items.find(i=>i.id===id);
        if(!item)return false;
        if(tool==='remove')remove(item);else setSelectedId(item.id);
        return true;
      }}/>
      {ghost&&<div role="status" aria-label="Placement preview" className={`pointer-events-none absolute left-2 top-5 z-20 max-w-[calc(100%-110px)] rounded-lg border px-3 py-2 text-sm shadow-lg ${ghostError?'border-red-400 bg-red-950 text-red-100':'border-emerald-400 bg-emerald-950 text-emerald-100'}`}>{ghostError?`Cannot place: ${ghostError}`:`Preview: ${itemName(ghost)} · tap to place`}</div>}
      <div role="toolbar" aria-label="Map building tools" className="absolute right-2 top-3 z-20 flex flex-col gap-2 rounded-xl border bg-card/95 p-1.5 shadow-lg">
      <Button aria-label="Build" title="Build" className="h-14 w-14 p-1 sm:h-16 sm:w-16 sm:p-2" disabled={!ready} onClick={()=>{setBuildOpen(true);setSelectedId(null);}}><BuildIcon large/></Button>
      <Button aria-label="Remove" title="Remove" className="h-14 w-14 p-1 sm:h-16 sm:w-16 sm:p-2" variant={tool==='remove'?'destructive':'outline'} aria-pressed={tool==='remove'} onClick={()=>{setTool(tool==='remove'?'select':'remove');setDraft(null);setSelectedId(null);announce('Remove mode: tap a removable object, or drag along walls, fences and paths to clear a line. Shift-drag or drag empty ground to pan.');}}><BuildIcon remove large/></Button>
      {tool==='place'&&draft&&canRotateDraft&&<Button className="h-14 w-14 flex-col gap-1 p-1 sm:h-16 sm:w-16" variant="outline" aria-label="Rotate before placing" onClick={()=>{setDraft(rotateItem(draft));announce(`${itemName(draft)} rotated. Choose where to place it.`);}}><RotateCw className="h-6 w-6"/>Rotate</Button>}
      {tool!=='select'&&<Button className="h-14 w-14 flex-col gap-1 p-1 sm:h-16 sm:w-16" variant="outline" onClick={cancel}><X className="h-5 w-5"/>Cancel</Button>}
      </div>
      {selected&&tool==='select'&&<div className="absolute bottom-4 left-2 z-10 max-w-[min(360px,calc(100%-16px))] rounded-xl border bg-card p-3 shadow-xl" aria-label="Selected object menu">
        <div className="mb-2 flex items-center gap-3"><strong className="flex-1 text-sm">{itemName(selected)}</strong><Button variant="ghost" size="icon" aria-label="Close object menu" onClick={()=>setSelectedId(null)}><X className="h-4 w-4"/></Button></div>
        {selected.fixed?<p className="text-xs text-muted-foreground">Fixed {selected.parentId?'plot fixture':'map structure'}</p>:<div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={()=>{choose({...selected});}}><Move className="mr-1 h-4 w-4"/>Move</Button>
          {!isMapStructure(selected)&&<><Button size="sm" variant="outline" onClick={rotateSelected}><RotateCw className="mr-1 h-4 w-4"/>Rotate</Button>
          <Button size="sm" variant="destructive" onClick={()=>remove(selected)}><BuildIcon remove/>Remove</Button></>}
        </div>}
        {selected.kind==='plot'&&selected.houseId===undefined&&<Button className="mt-2 w-full" size="sm" onClick={()=>setAssignOpen(true)}>Assign building type</Button>}
        {selected.kind==='plot'&&selected.houseId!==undefined&&<p className="mt-2 text-xs text-muted-foreground">Tap furniture to edit it. Remove this plot to choose a different type.</p>}
        {canHousePet(selected)&&<div className="mt-3 border-t pt-3"><p className="text-sm font-medium">Pet · {selected.petId===undefined?'0':'1'}/1</p>{selected.petId!==undefined&&<div className="my-2 flex items-center gap-2"><img className="h-14 w-14 object-contain [image-rendering:pixelated]" src={BUILDER_PETS.find(p=>p.id===selected.petId)?.src} alt=""/><span>{BUILDER_PETS.find(p=>p.id===selected.petId)?.name}</span></div>}<div className="flex gap-2"><Button size="sm" onClick={()=>{setPetQuery('');setPetOpen(true);}}>{selected.petId===undefined?'Assign pet':'Replace pet'}</Button>{selected.petId!==undefined&&<Button size="sm" variant="outline" onClick={()=>commit(assignPet(world,selected.id),'Pet assignment removed.')}>Remove pet</Button>}</div></div>}
        {selected.parentId&&<Button className="mt-2" size="sm" variant="outline" onClick={()=>setSelectedId(selected.parentId!)}>Select plot</Button>}
        {isHall(selected)&&<label className="mt-3 block text-sm">Town Hall level / rank<TownLevelInput key={selected.id} level={selected.level} onCommit={level=>commit({...world,items:world.items.map(i=>i.id===selected.id?{...i,level}:i)},`Town range: ${townRadius(level)} tiles.`)}/><span className="mt-1 block text-xs text-muted-foreground">Range: {townRadius(selected.level)} tiles</span></label>}
        {BUILDER_ASSETS.facilities[String(selected.facilityId)]?.contentsAppearancePending&&<p className="mt-2 text-xs text-muted-foreground">Contents appearance is not yet reconstructed for this storage type.</p>}
        {!!BUILDER_ASSETS.facilities[String(selected.facilityId)]?.states.length&&<label className="mt-3 block text-sm">Storage fullness<select aria-label="Storage fullness" className="ml-2 rounded border bg-background p-1" value={selected.fullness} onChange={e=>commit({...world,items:world.items.map(i=>i.id===selected.id?{...i,fullness:Number(e.target.value)}:i)},'Storage appearance updated.')}>
          {['Empty','¼ full','½ full','¾ full','Full'].map((name,i)=><option key={name} value={i}>{name}</option>)}
        </select></label>}
      </div>}
    </div>
    {!isFullscreen&&<p className="mt-2 text-xs text-muted-foreground">Tap to select or place. Drag to pan; when building or removing walls, fences and paths, drag along the map to fill or clear a line and Shift-drag to pan. Touch dragging always pans. Yellow edges show the town build boundary (Boundary hides it while you are not placing, and it follows a Town Hall you are moving). Reclaimed land has a green tint.</p>}

    <Dialog open={buildOpen} onOpenChange={setBuildOpen}><DialogContent container={isFullscreen?fullscreenRoot:null} className="flex max-h-[85dvh] max-w-3xl flex-col overflow-hidden">
      <DialogHeader><DialogTitle>Build your kingdom</DialogTitle><DialogDescription>Choose a sprite, then tap a valid space on the map.</DialogDescription></DialogHeader>
      <div className="flex gap-1 overflow-x-auto" role="tablist" aria-label="Build categories">{[...FACILITY_TABS.filter(t=>t.key!=='map'),{key:'dungeons',label:'Dungeons'}].map(t=><button key={t.key} role="tab" aria-selected={tab===t.key} className={`min-w-fit flex-1 rounded px-2 py-3 text-sm font-medium ${tab===t.key?'bg-primary text-primary-foreground':'bg-muted'}`} onClick={()=>{setTab(t.key);setQuery('');}}>{t.label}</button>)}</div>
      {tab==='dungeons'&&<p className="text-xs text-muted-foreground">Plan dungeons on clear land, including outside town coverage. Cave numbers distinguish appearance variants.</p>}
      <label className="flex items-center gap-2 rounded border px-3"><Search className="h-4 w-4"/><input className="w-full bg-transparent py-2 text-sm outline-none" placeholder="Find a building or facility…" aria-label="Search build menu" value={query} onChange={e=>setQuery(e.target.value)}/></label>
      <div key={tab} role="tabpanel" aria-label={`${tab} build options`} className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1" style={{maxHeight:'55dvh'}}>
        {tab==='env'&&!query&&<><h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Package className="h-4 w-4"/>Inv. · Land plots</h3><div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">{PLOT_SIZES.map(size=><button key={size} className="rounded-lg border bg-card p-2 hover:bg-muted" onClick={()=>choose(makeItem('plot',undefined,size))}><img className={iconClass} src={`${import.meta.env.BASE_URL}world-assets/builder/land-${size}.png`} alt=""/><span className="block text-sm font-semibold">Land {size}</span><span className="text-xs text-muted-foreground">{PLOT_TILES[size]} · assign after placing</span></button>)}</div></>}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tab==='env'&&['road','gravel','reclaim','unclaim'].filter(v=>!query||v.includes(query.toLowerCase())).map(v=><button className="rounded-lg border bg-card p-3 hover:bg-muted" key={v} onClick={()=>v==='road'||v==='gravel'?choose(makeItem('road',v==='gravel'?3:undefined)):chooseTerrain(v as 'reclaim'|'unclaim')}>
            {v==='unclaim'?<Waves className="mx-auto h-20 w-16 text-sky-500"/>:<img className={iconClass} src={BUILDER_ASSETS.sprites[BUILDER_ASSETS.facilities[v==='gravel'?'3':v].variants[0]].url} alt=""/>}<span className="mt-1 block text-sm font-medium">{v==='road'?'Road':v==='gravel'?'Gravel Path':v==='reclaim'?'Reclaim land':'Unclaim land'}</span>
          </button>)}
          {tab==='dungeons'&&Object.values(BUILDER_ASSETS.dungeons).filter(d=>d.name.toLowerCase().includes(query.toLowerCase())).map(d=><button className="rounded-lg border bg-card p-2 hover:border-primary hover:bg-muted" key={d.chipId} onClick={()=>choose({...makeItem('dungeon'),dungeonChipId:d.chipId})}><img src={d.menuIcon} alt="" className={iconClass}/><span className="block text-sm font-medium">{d.name}</span><span className="text-xs text-muted-foreground">{d.width}×{d.height}</span></button>)}
          {visible.map(f=><button className="rounded-lg border bg-card p-2 hover:border-primary hover:bg-muted disabled:opacity-50" disabled={placementLimit(f.id)!==undefined&&placedCount(world.items,f.id)>=placementLimit(f.id)!} key={f.id} onClick={()=>choose(makeItem('facility',f.id))}>
            <img src={BUILDER_ASSETS.facilities[String(f.id)].menuIcon} alt="" className={iconClass} loading="lazy"/><span className="mt-1 block text-sm font-medium">{f.name}</span>{placementLimit(f.id)!==undefined&&<span className="block text-xs font-medium">{placedCount(world.items,f.id)}/{placementLimit(f.id)} placed</span>}<span className="text-xs text-muted-foreground">{defaultFootprint(f.id).join('×')}{BUILDER_ASSETS.facilities[f.id].expandsTown&&f.id!==17?` · expands ${f.validRange}`:''}</span>
          </button>)}
        </div>
        {!visible.length&&tab!=='env'&&tab!=='dungeons'&&<p className="py-10 text-center text-muted-foreground">No facilities match your search.</p>}
      </div>
    </DialogContent></Dialog>
    <Dialog open={petOpen} onOpenChange={setPetOpen}><DialogContent container={isFullscreen?fullscreenRoot:null} className="flex max-h-[85dvh] max-w-3xl flex-col overflow-hidden"><DialogHeader><DialogTitle>Assign a monster / pet</DialogTitle><DialogDescription>One occupant per Monster Room or Monster Stable. This saves a planned assignment and shows its sprite.</DialogDescription></DialogHeader><input aria-label="Search pets" placeholder="Find a monster…" className="rounded border bg-background p-2" value={petQuery} onChange={e=>setPetQuery(e.target.value)}/><div className="grid min-h-0 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-4">{BUILDER_PETS.filter(p=>p.name.toLowerCase().includes(petQuery.toLowerCase())).map(p=><button key={p.id} className="rounded-lg border p-2 hover:bg-muted" onClick={()=>{if(selected&&commit(assignPet(world,selected.id,p.id),`${p.name} assigned.`))setPetOpen(false);}}><img src={p.src} alt="" className={iconClass} loading="lazy"/><span className="text-sm">{p.name}</span></button>)}</div></DialogContent></Dialog>
    <Dialog open={assignOpen} onOpenChange={setAssignOpen}><DialogContent container={isFullscreen?fullscreenRoot:null} className="flex max-h-[85dvh] max-w-3xl flex-col overflow-hidden"><DialogHeader><DialogTitle>Assign Land {selected?.size}</DialogTitle><DialogDescription>Each building starts with its required fixtures. Indoor limits follow the selected plot size.</DialogDescription></DialogHeader>
      <div className="grid min-h-0 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-4">{BUILDINGS.map(b=><button key={b.id} className="rounded border p-2 hover:bg-muted" onClick={()=>assign(b.id)}><img className={iconClass} loading="lazy" src={`${import.meta.env.BASE_URL}world-assets/plots/house-${b.id}-${selected?.size??'S'}.png`} alt=""/><span className="text-sm">{b.name}</span></button>)}</div>
    </DialogContent></Dialog>
    <Dialog open={resetOpen} onOpenChange={setResetOpen}><DialogContent container={isFullscreen?fullscreenRoot:null} className="max-w-md">
      <DialogHeader><DialogTitle>Reset {active.name}?</DialogTitle><DialogDescription>Every building, road, plot and reclaimed tile in this build is removed and the original map structures come back. The other builds are untouched, and Undo restores this one until you place something new.</DialogDescription></DialogHeader>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={()=>setResetOpen(false)}>Cancel</Button><Button variant="destructive" onClick={resetBuild}><Trash2 className="mr-2 h-4 w-4"/>Reset builder</Button></div>
    </DialogContent></Dialog>
    <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}><DialogContent container={isFullscreen?fullscreenRoot:null} className="max-w-md">
      <DialogHeader><DialogTitle>Delete {active.name}?</DialogTitle><DialogDescription>This build and everything placed in it is deleted from this browser. This cannot be undone, and the other builds are untouched.</DialogDescription></DialogHeader>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={()=>setDeleteOpen(false)}>Cancel</Button><Button variant="destructive" onClick={deleteBuild}><Trash2 className="mr-2 h-4 w-4"/>Delete {active.name}</Button></div>
    </DialogContent></Dialog>
  </div>;
}
