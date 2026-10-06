export function zoomAt<T extends {offsetX:number;offsetY:number;zoom:number}>(camera:T,zoom:number,x:number,y:number):T {
  const next=Math.max(0.08,Math.min(3.5,zoom));
  const ratio=next/camera.zoom;
  return {...camera,zoom:next,offsetX:x-(x-camera.offsetX)*ratio,offsetY:y-(y-camera.offsetY)*ratio};
}
export function wheelZoomFactor(delta:number,mode:number) {
  const pixels=delta*(mode===1?16:mode===2?800:1);
  return Math.exp(-Math.max(-160,Math.min(160,pixels))*0.0015);
}
