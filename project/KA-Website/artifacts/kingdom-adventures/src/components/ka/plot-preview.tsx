import { useState } from "react";
import { PLOT_SIZES, type Building, type PlotSize } from "@/game-data/buildings";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** Static reference images generated from the recovered plot assembly. */
export function PlotPreview({ buildings }: { buildings: Building[] }) {
  const [selectedId, setSelectedId] = useState(buildings[0].id);
  const [size, setSize] = useState<PlotSize>("S");
  const building = buildings.find(b => b.id === selectedId) ?? buildings[0];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {buildings.length > 1 && (
          <Select value={String(building.id)} onValueChange={value => setSelectedId(Number(value))}>
            <SelectTrigger aria-label="Preview house" className="h-8 min-w-0 flex-1 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{buildings.map(b => <SelectItem key={b.id} value={String(b.id)}>{b.name}</SelectItem>)}</SelectContent>
          </Select>
        )}
        <Select value={size} onValueChange={value => setSize(value as PlotSize)}>
          <SelectTrigger aria-label={`Plot size for ${building.name}`} className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{PLOT_SIZES.map(s => <SelectItem key={s} value={s}>Plot {s}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <img src={`${import.meta.env.BASE_URL}world-assets/plots/house-${building.id}-${size}.png`}
        alt={`${building.name}, ${size} plot with initial furniture and one entrance`}
        className="h-56 w-full object-contain" style={{ imageRendering: "pixelated" }} loading="lazy" />
      <p className="text-[11px] text-muted-foreground">Initial layout · Example entrance position</p>
    </div>
  );
}
