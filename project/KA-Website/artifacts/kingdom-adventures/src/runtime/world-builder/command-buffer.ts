import type { PlaceChipCommand, CommandBufferApi } from "./types";

export interface BufferedPlaceChipCommand {
  type: "placeChip";
  command: PlaceChipCommand;
}

export type BufferedRuntimeCommand = BufferedPlaceChipCommand;

export class CommandBuffer implements CommandBufferApi {
  private readonly commands: BufferedRuntimeCommand[] = [];

  placeChip(command: PlaceChipCommand): void {
    this.commands.push({ type: "placeChip", command });
  }

  drain(): BufferedRuntimeCommand[] {
    return this.commands.splice(0, this.commands.length);
  }
}
