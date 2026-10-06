// A load belongs to the asset, not to a particular hover/placement render.
export class BuilderSpriteCache {
  readonly images = new Map<string, HTMLImageElement>();
  readonly alpha = new Map<string, Uint8ClampedArray>();
  readonly errors = new Set<string>();
  private pending = new Set<string>();
  private listeners = new Set<()=>void>();
  constructor(private createImage = ()=>new Image(), private readAlpha = (img:HTMLImageElement)=>{
    const canvas=document.createElement('canvas');
    canvas.width=img.width;canvas.height=img.height;
    const context=canvas.getContext('2d')!;
    context.drawImage(img,0,0);
    return context.getImageData(0,0,img.width,img.height).data;
  }) {}
  subscribe(listener:()=>void) {this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};}
  load(id:string,url:string) {
    if(this.images.has(id)||this.pending.has(id)||this.errors.has(id))return;
    this.pending.add(id);
    const image=this.createImage();
    const finish=()=>{this.pending.delete(id);for(const listener of this.listeners)listener();};
    image.onload=()=>{
      this.images.set(id,image);
      try {this.alpha.set(id,this.readAlpha(image));} catch { /* Cell picking still works. */ }
      finish();
    };
    image.onerror=()=>{this.errors.add(id);finish();};
    image.src=url;
  }
  retry(){this.errors.clear();for(const listener of this.listeners)listener();}
}
