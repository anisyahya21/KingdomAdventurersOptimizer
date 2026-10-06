import { BUILDER_ASSETS, cells, dimensions, containingPlot, groundDepth, nativeDepth, pieceOffset, supportHeight, template, type BuilderItem, type BuilderState, type Cell } from './world-builder';
import { PLOT_TILES } from '@/game-data/buildings';

export type BuilderDraw = {sprite:string;x:number;y:number;elevation:number;depth:number;opacity:number;itemId:string};
// Gate (facility 28) is the only free-standing entrance assembly: it reserves two
// cells and draws one door piece per cell. World.CreateEntranceDoor 0x14766bc
// gives every entrance two frames, and the native frame choice samples the cell
// one step along the wall axis: another entrance -> the continuation frame 0,
// anything else -> the end frame 1 (door-layer-mirror-rules.json entranceTexture).
const entranceFacilityIds = [28];
const entranceStep = (direction:number) => direction % 2 ? {x:0,y:-1} : {x:1,y:0};
const entrancePieceFrame = (direction:number, piece:Cell) => direction % 2 ? (piece.y === 0 ? 1 : 0) : (piece.x === 0 ? 0 : 1);
// Shell and gate pieces are exported in preview order (100*(x+y)); re-key them
// onto the recovered native cell-major depth so they mix correctly with
// furniture. Negative depths stay in the ground band but keep cell order.
function pieceDepth(depth:number,cell:Cell,local:Cell) {
  return depth < 0 ? groundDepth(cell) : nativeDepth(cell,pieceOffset(depth,local));
}
export function builderDraws(state:BuilderState, ghost?:BuilderItem|null, previewValid=true):BuilderDraw[] {
  // Invalid previews must not appear as another placed building. Keep the saved
  // original visible when an attempted move is invalid.
  if(!previewValid)ghost=null;
  const draws:BuilderDraw[]=[];
  const items=[...state.items.filter(item=>item.id!==ghost?.id),...(ghost?[ghost]:[])];
  const barriers=new Set(items.filter(item=>item.kind==='facility'&&BUILDER_ASSETS.facilities[String(item.facilityId)]?.barrierFrames).map(item=>`${item.x},${item.y}`));
  const entrances=new Set<string>();
  for(const item of items) if(item.kind==='facility'&&entranceFacilityIds.includes(item.facilityId!)) for(const c of cells(item)) entrances.add(`${c.x},${c.y}`);
  // Direction.OFFSETS 0x145f4c4; FencePlaceSystem passes 1<<direction directly
  // to barrier_00.seb. Gate/plot fences are different native types and do not join.
  const offsets=[[0,-1],[1,0],[0,1],[-1,0]];
  for(const item of items) {
    // Original map renderer owns the fixed port and cave assemblies.
    if(item.fixed&&!item.parentId) continue;
    const opacity=item===ghost?0.6:1;
    if(item.kind==='plot') {
      const [w,h]=PLOT_TILES[item.size!].split('×').map(Number);
      const shell=item.houseId===undefined?BUILDER_ASSETS.emptyPlots[item.size!][item.direction]:template(item).rotations[item.direction];
      for(const d of shell) {
        const c=d;
        const cell={x:item.x+c.x,y:item.y+c.y};
        draws.push({...d,x:cell.x,y:cell.y,depth:pieceDepth(d.depth,cell,c),opacity,itemId:item.id});
      }
    } else {
      const a=item.kind==='dungeon'?BUILDER_ASSETS.dungeons[String(item.dungeonChipId)]:BUILDER_ASSETS.facilities[item.kind==='road'?(item.facilityId===3?'3':'road'):String(item.facilityId)];
      if(!a) continue;
      const parent=item===ghost?containingPlot(item,state):state.items.find(p=>p.id===item.parentId);
      const direction=item.facing??item.direction;
      if(a.rotationDraws) {
        const parts=a.rotationDraws[direction];
        const doors=entranceFacilityIds.includes(item.facilityId!)?parts.filter(p=>p.depth>=0):[];
        for(const d of parts) {
          const cell={x:item.x+d.x,y:item.y+d.y};
          const step=entranceStep(direction);
          const frame=doors.length&&d.depth>=0?(entrances.has(`${cell.x+step.x},${cell.y+step.y}`)?0:1):undefined;
          const chosen=frame===undefined?d:doors.find(p=>entrancePieceFrame(direction,p)===frame)??d;
          draws.push({...chosen,x:cell.x,y:cell.y,depth:pieceDepth(d.depth,cell,d),opacity,itemId:item.id});
        }
        continue;
      }
      const mask=a.barrierFrames?offsets.reduce((mask,[dx,dy],dir)=>mask|(barriers.has(`${item.x+dx},${item.y+dy}`)?1<<dir:0),0):0;
      const sprite=a.barrierFrames?a.barrierFrames[mask]:a.states.length?a.states[item.fullness]:a.variants[direction];
      const [w,h]=dimensions(item);
      // The sprite anchor is already the native south-east cell; depth uses the
      // same cell, so a 2x2 assembly no longer double-counts its offset.
      const x=item.x+(item.direction%2?a.anchorY:a.anchorX),y=item.y+(item.direction%2?a.anchorX:a.anchorY);
      const elevation=supportHeight(parent);
      const facing=a.barrierFrames?5:([0,3].includes(direction)?5:20);
      draws.push({sprite,x,y,elevation,depth:item.kind==='road'?groundDepth({x,y}):nativeDepth({x,y},elevation+facing+(parent?direction:0)),opacity,itemId:item.id});
      if(item.petId!==undefined)draws.push({sprite:`pet-${item.petId}`,x:item.x+(w-1)/2,y:item.y+(h-1)/2,elevation,depth:nativeDepth({x,y},elevation+30),opacity,itemId:item.id});
    }
  }
  return draws.sort((a,b)=>a.depth-b.depth);
}
export function hitBuilder(point:Cell,camera:{offsetX:number;offsetY:number;zoom:number},draws:BuilderDraw[],alpha:Map<string,Uint8ClampedArray>) {
  for(let i=draws.length-1;i>=0;i--) {
    const d=draws[i],s=BUILDER_ASSETS.sprites[d.sprite],pixels=alpha.get(d.sprite);if(!s||!pixels)continue;
    const x=Math.floor((point.x-camera.offsetX)/camera.zoom-24*(d.x-d.y)-s.dx);
    const y=Math.floor((point.y-camera.offsetY)/camera.zoom-12*(d.x+d.y)-s.dy+d.elevation);
    if(x>=0&&y>=0&&x<s.width&&y<s.height&&pixels[(y*s.width+x)*4+3]>30)return d.itemId;
  }
  return undefined;
}
export function drawBuilder(context:CanvasRenderingContext2D,camera:{offsetX:number;offsetY:number;zoom:number},width:number,height:number,draws:BuilderDraw[],images:Map<string,HTMLImageElement>) {
  const z=camera.zoom;
  context.save();context.imageSmoothingEnabled=false;
  for(const d of draws) {
    const s=BUILDER_ASSETS.sprites[d.sprite],image=images.get(d.sprite);if(!s||!image) continue;
    const x=camera.offsetX+(24*(d.x-d.y)+s.dx)*z,y=camera.offsetY+(12*(d.x+d.y)+s.dy-d.elevation)*z;
    if(x+s.width*z<0||y+s.height*z<0||x>width||y>height)continue;
    context.globalAlpha=d.opacity;
    context.drawImage(image,x,y,s.width*z,s.height*z);
  }
  context.restore();
}
export function diamond(context:CanvasRenderingContext2D,c:Cell,camera:{offsetX:number;offsetY:number;zoom:number},fill:string,stroke?:string,elevation=0) {
  const z=camera.zoom,x=camera.offsetX+24*(c.x-c.y)*z,y=camera.offsetY+(12*(c.x+c.y)-elevation)*z;
  context.beginPath();context.moveTo(x,y-12*z);context.lineTo(x+24*z,y);context.lineTo(x,y+12*z);context.lineTo(x-24*z,y);context.closePath();
  context.fillStyle=fill;context.fill();if(stroke){context.strokeStyle=stroke;context.lineWidth=1;context.stroke();}
}
