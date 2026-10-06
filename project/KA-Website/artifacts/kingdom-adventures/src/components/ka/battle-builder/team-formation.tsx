import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CharacterPreviewCanvas } from "@/components/character-preview-canvas";
import { MONSTER_BY_ID, type BattleSetup } from "@/lib/battle-setup";
import { MONSTER_ICON_MAP } from "@/lib/monster-icons";
import { gearInSlot, type BuilderSharedData, type DraftCharacter } from "@/lib/battle-team-draft";
import type { BattlePreview } from "@/lib/battle-preview";

const COLUMNS = 5;

type FormationEntry =
  | { kind: "human"; name: string; character: DraftCharacter }
  | { kind: "pet"; name: string; monsterId: number; owner: string };

function formationEntries(characters: DraftCharacter[], setup: BattleSetup | null): FormationEntry[] {
  const humans: FormationEntry[] = characters.map((character, index) => ({
    kind: "human",
    name: setup?.playerTeam[index]?.name ?? character.name?.trim() ?? `Character ${index + 1}`,
    character,
  }));
  const pets: FormationEntry[] = [];
  for (const [ownerIndex, character] of characters.entries()) {
    const owner = humans[ownerIndex].name;
    const convertedPets = setup?.households?.[owner] ?? [];
    for (const [petIndex, pet] of (character.householdPets ?? []).entries()) {
      const species = MONSTER_BY_ID.get(pet.monsterId);
      pets.push({
        kind: "pet",
        name: convertedPets[petIndex]?.name ?? pet.name?.trim() ?? species?.name ?? `Pet ${petIndex + 1}`,
        monsterId: pet.monsterId,
        owner,
      });
    }
  }
  return [...humans, ...pets];
}

function FormationSprite({ entry, data }: { entry: FormationEntry; data: BuilderSharedData | null }) {
  if (entry.kind === "pet") {
    const species = MONSTER_BY_ID.get(entry.monsterId);
    const image = species?.src || (species?.name ? MONSTER_ICON_MAP[species.name] : undefined);
    return image ? (
      <img src={image} alt="" className="h-14 w-14 object-contain [image-rendering:pixelated] sm:h-16 sm:w-16" />
    ) : <span className="text-sm text-muted-foreground">?</span>;
  }
  const character = entry.character;
  const weapon = gearInSlot(character, "weapon", data?.slotAssignments);
  const shield = gearInSlot(character, "shield", data?.slotAssignments);
  return (
    <CharacterPreviewCanvas
      jobName={character.jobName ?? "Guard"}
      rank={character.rank}
      variant={character.gender === 1 ? 2 : 1}
      equipState="right"
      weaponName={weapon?.name ?? null}
      shieldName={shield?.name ?? null}
      scale={2}
      poseFrame={0}
      label={`${entry.name} sprite`}
      className="h-14 w-auto max-w-full object-contain sm:h-16"
    />
  );
}

export function TeamFormation({ characters, setup, data, preview, previewStatus }: {
  characters: DraftCharacter[];
  setup: BattleSetup | null;
  data: BuilderSharedData | null;
  preview: BattlePreview | null;
  previewStatus: "idle" | "loading" | "ok" | "error";
}) {
  const entries = formationEntries(characters, setup);
  const allies = preview?.units.filter((unit) => unit.side === "ally" && unit.cell && unit.rosterIndex !== null) ?? [];
  const rows = [...new Set(allies.map((unit) => unit.cell![1]))].sort((a, b) => a - b);
  const cells = new Map(allies.map((unit) => [`${unit.cell![0]},${unit.cell![1]}`, unit]));
  return (
    <Card data-team-formation data-formation-units={entries.length} data-formation-rows={rows.length}>
      <CardHeader>
        <CardTitle>2 - Team formation</CardTitle>
        <CardDescription>Battle starting cells · front row first. Positions follow formation priority and defense.</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <div className="space-y-2" aria-label="Team formation">
            {rows.map((row, rowIndex) => (
              <div key={row}>
                <div className="mb-1 text-xs text-muted-foreground">{rowIndex === 0 ? "Front row" : `Back row ${rowIndex}`} · cell row {row}</div>
                <div className="grid grid-cols-5 gap-1.5 sm:gap-2">
                  {Array.from({ length: COLUMNS }, (_, column) => {
                    const unit = cells.get(`${column},${row}`);
                    const entry = unit?.rosterIndex === null || unit?.rosterIndex === undefined ? undefined : entries[unit.rosterIndex];
                    return (
                      <div
                        key={column}
                        className="flex min-w-0 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border/70 bg-muted/10 px-0.5 py-2 text-center sm:px-2"
                        data-formation-cell={`${column},${row}`}
                        data-formation-kind={entry?.kind ?? "empty"}
                        title={entry?.kind === "pet" ? `${entry.name} · ${entry.owner}'s pet` : entry?.name}
                      >
                        <span className="text-[10px] tabular-nums text-muted-foreground">{column},{row}</span>
                        <span className="flex h-14 w-full items-center justify-center sm:h-16">
                          {entry ? <FormationSprite entry={entry} data={data} /> : null}
                        </span>
                        <span className="w-full truncate text-[10px] font-medium sm:text-xs">{entry?.name ?? unit?.name ?? ""}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground" data-formation-status={previewStatus}>
            {previewStatus === "loading" ? "Calculating starting cells…" : previewStatus === "error" ? "Starting cells are unavailable. See the preview error below." : previewStatus === "ok" ? "No starting cells were returned." : setup ? "Calculating starting cells…" : "Add a valid team to see its starting cells."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
