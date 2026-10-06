import {
  EQUIPMENT_BY_ID,
  createDefaultStartProfile,
  EQUIPMENT_SLOT_ORDER,
  SKILL_BY_ID,
  validateBattleSetup,
  type BattleItemRow,
  type BattleInput,
  type BattleSetup,
  type HumanUnit,
  type PetMonsterUnit,
  type FinishPolicy,
  type PlacementState,
  type SetupIssue,
  type StartProfile,
} from "@/lib/battle-setup";

/**
 * Adapter from the UI-independent BattleSetup model to the existing authoritative Python scenario
 * schema `ka-special-combat-research-1`.
 *
 * This is deliberately a conversion layer only. It does not calculate effective parameters, run
 * combat, reorder skills or invent legality rules.
 *
 * Contract notes recovered while wiring this layer:
 *  * `weaponId` is the raw equipment id. `0` is the recovered `Bare-Handed` record, and the
 *    authoritative validator exempts exactly `0` from the equipment-contribution list, so an
 *    unarmed human stays a valid scenario unit without a synthetic weapon row.
 *  * Owner-bound pets are not part of `ownUnits`: the loader appends `housePets[owner]` after the
 *    selected members, in selected-owner order, and then removes the `housePets` key. The pets come
 *    from the explicit `setup.households` declaration: its key set names the selected human house
 *    owners and each value is that owner's COMPLETE ordered household list (`[]` = "no pets").
 *    Nothing is inferred, capped, filtered or deduplicated, and an empty list still marks the owner.
 */

export type CombatScenarioParameter = {
  rawValue: number;
  rawMax: number;
  extraValue: number;
  extraMax: number;
  trainingLevel: number;
};

export type CombatScenarioUnit = {
  name: string;
  human: boolean;
  monsterId: number | null;
  weaponId: number;
  equipment: Array<{ id: number; level: number; affinity: number }>;
  visitor: boolean;
  leaderIdentity: boolean;
  skills: number[];
  invocationLevels: number[];
  parameters: Record<string, CombatScenarioParameter>;
  humanFlags?: number;
  isHouseOwner?: boolean;
  petOwnerName?: string;
  ownerPlayer?: boolean;
};

export type CombatScenario = {
  schema: "ka-special-combat-research-1";
  encounterId: number;
  defeatCount: number;
  mathSeed: number;
  libSeed: number;
  tickLimit: number;
  ownUnits: CombatScenarioUnit[];
  housePets?: Record<string, CombatScenarioUnit[]>;
  holyHerbStock: number;
  /** Explicit recovered recovery-item rows (bonusCategory 3 / bonusType 0..5) with finite stock. */
  items?: Record<string, BattleItemRow>;
  itemStock?: Record<string, number>;
  inputs: BattleInput[];
  prePlacement?: Record<string, PlacementState>;
  /**
   * Declared isolated-scene0 start profile (simulation condition, not captured save data). Emitted
   * for a normal setup so the runner's conditional simulation is supported; mutually exclusive with
   * captured `prePlacement`.
   */
  startProfile?: StartProfile;
  /** `at-horizon` (legacy default) or `on-verdict` (disclosed simulation confirmation policy). */
  finishPolicy?: FinishPolicy;
  note?: string;
};

export type BattleSetupVisual = {
  encounter: BattleSetup["encounter"];
  units: Array<{
    name: string;
    kind: "human" | "monster";
    jobId?: string;
    rank?: string;
    gender?: number;
    monsterId?: number;
    appearanceInputs?: HumanUnit["appearanceInputs"];
    weaponId: number;
    weaponMotion: number | null;
    /**
     * Shield-slot equipment id for the visual sprite (the loadout renderer's `shieldName` input).
     * Additive appearance contract field; the runner scenario is unchanged by it.
     */
    shieldId: number | null;
    skills: Array<{
      skillId: number;
      invocationLevel: 0 | 1 | 2;
      motion: number | null;
      requiredEquipType: number | null;
    }>;
  }>;
};

export class BattleSetupAdapterError extends Error {
  readonly issues: SetupIssue[];

  constructor(issues: SetupIssue[]) {
    super(issues.map((issue) => `${issue.code}: ${issue.message}`).join("; "));
    this.name = "BattleSetupAdapterError";
    this.issues = issues;
  }
}

function orderedEquipment(unit: HumanUnit) {
  return EQUIPMENT_SLOT_ORDER.flatMap((slot) => {
    const selection = unit.equipmentSlots[slot];
    return selection ? [{ id: selection.id, level: selection.level, affinity: selection.affinity }] : [];
  });
}

function humanToScenario(unit: HumanUnit, isHouseOwner: boolean): CombatScenarioUnit {
  return {
    name: unit.name,
    human: true,
    monsterId: null,
    weaponId: unit.weaponId,
    equipment: orderedEquipment(unit),
    visitor: unit.visitor ?? false,
    leaderIdentity: unit.leaderIdentity ?? false,
    skills: unit.skills.map((selection) => selection.skillId),
    invocationLevels: unit.skills.map((selection) => selection.invocationLevel),
    parameters: unit.parameters,
    ...(unit.humanFlags === undefined ? {} : { humanFlags: unit.humanFlags }),
    ...(isHouseOwner ? { isHouseOwner: true } : {}),
  };
}

function monsterToScenario(unit: PetMonsterUnit): CombatScenarioUnit {
  return {
    name: unit.name,
    human: false,
    monsterId: unit.monsterId,
    // The authoritative Python validator still expects the generic compatibility fields on every
    // unit. These are adapter-only compatibility values and never written back into BattleSetup.
    weaponId: 0,
    equipment: [],
    visitor: false,
    leaderIdentity: false,
    skills: unit.skills.map((selection) => selection.skillId),
    invocationLevels: unit.skills.map((selection) => selection.invocationLevel),
    parameters: unit.parameters,
    ...(unit.petOwnerName === undefined ? {} : { petOwnerName: unit.petOwnerName }),
    ...(unit.ownerPlayer === undefined ? {} : { ownerPlayer: unit.ownerPlayer }),
  };
}

export function battleSetupToCombatScenario(setup: BattleSetup): {
  scenario: CombatScenario;
  warnings: SetupIssue[];
  visualSetup: BattleSetupVisual;
} {
  const issues = validateBattleSetup(setup);
  const errors = issues.filter((issue) => issue.category === "ERROR");
  if (errors.length > 0) {
    throw new BattleSetupAdapterError(errors);
  }
  const warnings = issues.filter((issue) => issue.category !== "ERROR");

  // Captured prePlacement and a declared startProfile are mutually exclusive (the authoritative
  // validator rejects both); validateBattleSetup reports this as an ERROR above, so it is already
  // enforced here before the scenario is built.
  const humanNames = new Set(
    setup.playerTeam.filter((unit): unit is HumanUnit => unit.kind === "human").map((unit) => unit.name),
  );
  const housePets: Record<string, CombatScenarioUnit[]> = {};
  // The declaration key set is authoritative: each selected human house owner brings the COMPLETE
  // ordered household list (`[]` = declared "no pets"). Nothing is inferred, capped, filtered or
  // deduplicated, and the pet order is the declared list order. A pet may only be declared once.
  for (const [owner, pets] of Object.entries(setup.households ?? {})) {
    if (!humanNames.has(owner)) {
      throw new BattleSetupAdapterError([
        {
          category: "ERROR",
          code: "PET_OWNER_NOT_SELECTED_HUMAN",
          path: `households.${owner}`,
          message: `Pet owner '${owner}' is not a selected human unit.`,
        },
      ]);
    }
    housePets[owner] = pets.map((pet) => ({ ...monsterToScenario(pet), petOwnerName: owner }));
  }

  const ownUnits = setup.playerTeam
    .map((unit) =>
      unit.kind === "human"
        ? humanToScenario(unit, Object.prototype.hasOwnProperty.call(housePets, unit.name))
        : monsterToScenario(unit),
    );

  const scenario: CombatScenario = {
    schema: "ka-special-combat-research-1",
    encounterId: setup.encounter.encounterId,
    defeatCount: setup.defeatCount,
    mathSeed: setup.seeds.mathSeed,
    libSeed: setup.seeds.libSeed,
    tickLimit: setup.horizon.tickLimit,
    ownUnits,
    ...(Object.keys(housePets).length > 0 ? { housePets } : {}),
    holyHerbStock: setup.holyHerbStock,
    ...(setup.items ? { items: setup.items } : {}),
    ...(setup.itemStock ? { itemStock: setup.itemStock } : {}),
    inputs: setup.inputs,
    ...(setup.prePlacement
      ? { prePlacement: setup.prePlacement }
      : { startProfile: setup.startProfile ?? createDefaultStartProfile() }),
    finishPolicy: setup.finishPolicy ?? "at-horizon",
    note: "Generated by battleSetupToCombatScenario; raw/native-compatible inputs only.",
  };

  const visualSetup: BattleSetupVisual = {
    encounter: setup.encounter,
    units: setup.playerTeam.map((unit) => {
      const weapon = unit.kind === "human" ? EQUIPMENT_BY_ID.get(unit.weaponId) : undefined;
      const shieldId = unit.kind === "human" ? unit.equipmentSlots.shield?.id ?? null : null;
      return {
        name: unit.name,
        kind: unit.kind,
        ...(unit.kind === "human"
          ? {
              jobId: unit.jobId,
              rank: unit.rank,
              gender: unit.gender,
              appearanceInputs: unit.appearanceInputs,
            }
          : { monsterId: unit.monsterId }),
        weaponId: unit.kind === "human" ? unit.weaponId : 0,
        weaponMotion: weapon?.motion ?? null,
        shieldId,
        skills: unit.skills.map((selection) => {
          const skill = SKILL_BY_ID.get(selection.skillId);
          return {
            skillId: selection.skillId,
            invocationLevel: selection.invocationLevel,
            motion: skill?.motion ?? null,
            requiredEquipType: skill?.requiredEquipType ?? null,
          };
        }),
      };
    }),
  };

  return { scenario, warnings, visualSetup };
}
