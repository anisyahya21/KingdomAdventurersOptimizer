import { useCallback, useEffect, useState } from "react";
import type { EncounterLoot } from "@/lib/special-boss-loot";
import { LOOT_DIFFICULTIES } from "@/components/ka/special-boss-loot-ui";

type Difficulty = EncounterLoot["difficulty"];
type DifficultySelections = Record<string, Set<Difficulty>>;

const ALL_DIFFICULTIES = new Set<Difficulty>(LOOT_DIFFICULTIES);

function readSelections(storageKey: string): DifficultySelections {
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (!stored) return {};

    const parsed: unknown = JSON.parse(stored);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};

    const selections: DifficultySelections = {};
    for (const [group, values] of Object.entries(parsed)) {
      if (Array.isArray(values)) {
        selections[group] = new Set(values.filter(
          (value): value is Difficulty => LOOT_DIFFICULTIES.includes(value as Difficulty),
        ));
      }
    }
    return selections;
  } catch {
    return {};
  }
}

/** Persist independently keyed loot difficulty selections in this browser. */
export function useLootDifficulties(storageKey: string) {
  const [selections, setSelections] = useState<DifficultySelections>(() => {
    if (typeof window === "undefined") return {};
    return readSelections(storageKey);
  });

  useEffect(() => {
    try {
      const serializable = Object.fromEntries(
        Object.entries(selections).map(([group, values]) => [group, Array.from(values)]),
      );
      window.localStorage.setItem(storageKey, JSON.stringify(serializable));
    } catch {
      // Storage can be disabled or full; the in-memory controls remain usable.
    }
  }, [selections, storageKey]);

  const selectedDifficulties = useCallback(
    (group: string) => selections[group] ?? new Set(ALL_DIFFICULTIES),
    [selections],
  );

  const setDifficulty = useCallback(
    (group: string, difficulty: Difficulty, checked: boolean) => {
      setSelections((current) => {
        const next = new Set(current[group] ?? ALL_DIFFICULTIES);
        if (checked) next.add(difficulty);
        else next.delete(difficulty);
        return { ...current, [group]: next };
      });
    },
    [],
  );

  return { selections, selectedDifficulties, setDifficulty };
}
