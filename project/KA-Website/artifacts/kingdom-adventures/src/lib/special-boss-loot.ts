type LootLine = {
  item: string;
  chance: string;
  quantity: string;
};

export type EncounterLoot = {
  difficulty: "Easy" | "Normal" | "Hard" | "Extreme";
  level: number;
  bossLevel: number;
  tables: LootLine[][];
};

export type SpecialBossLootGroup = {
  title: string;
  encounters: EncounterLoot[];
};

function parseLootSummary(summary: string): LootLine[] {
  return summary.split(", ").map((part) => {
    const match = part.match(/^(\d+)%\s+(.+)$/);
    if (!match) {
      return { item: part, chance: "-", quantity: "-" };
    }

    const [, percent, rest] = match;
    const rangeMatch = rest.match(/^(\d+\s*-\s*\d+)\s+(.+)$/);
    if (rangeMatch) {
      return {
        item: rangeMatch[2],
        chance: `1/${(100 / Number(percent)).toFixed(Number(percent) >= 10 ? 1 : 2).replace(/\.0$/, "")}`,
        quantity: rangeMatch[1].replace(/\s*/g, ""),
      };
    }

    const singleMatch = rest.match(/^(\d+)\s+(.+)$/);
    if (singleMatch) {
      return {
        item: singleMatch[2],
        chance: `1/${(100 / Number(percent)).toFixed(Number(percent) >= 10 ? 1 : 2).replace(/\.0$/, "")}`,
        quantity: singleMatch[1],
      };
    }

    return {
      item: rest,
      chance: `1/${(100 / Number(percent)).toFixed(Number(percent) >= 10 ? 1 : 2).replace(/\.0$/, "")}`,
      quantity: "1",
    };
  });
}

function encounter(
  difficulty: EncounterLoot["difficulty"],
  level: number,
  bossLevel: number,
  summaries: string[],
): EncounterLoot {
  return {
    difficulty,
    level,
    bossLevel,
    tables: summaries.map(parseLootSummary),
  };
}

export const KAIRO_ROOM_LOOT_GROUPS: SpecialBossLootGroup[] = [
  {
    title: "Kairobot Knight",
    encounters: [
      encounter("Easy", 35, 70, [
        "15% 1 A/ Kairo Sword, 100% 1 - 1 Kairo Flan, 20% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Strategic Retreat",
        "5% 1 A/ Kairo Sword, 100% 1 - 1 Kairo Flan, 15% 1 - 1 Bounty Bag, 5% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
      encounter("Normal", 65, 130, [
        "35% 1 A/ Kairo Sword, 100% 2 - 2 Kairo Flan, 40% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Battle Maniac",
        "10% 1 A/ Kairo Sword, 100% 1 - 2 Kairo Flan, 25% 1 - 1 Bounty Bag, 10% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
      encounter("Hard", 120, 240, [
        "65% 1 A/ Kairo Sword, 5% 1 S/ Kairo Shield, 100% 3 - 3 Kairo Flan, 60% 1 - 1 Kairo Creamy Cake, 5% 1 - 1 Skill Slots Up, 5% 1 Round Trip",
        "45% 1 A/ Kairo Sword, 3% 1 S/ Kairo Shield, 100% 2 - 3 Kairo Flan, 35% 1 - 1 Bounty Bag, 20% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
      encounter("Extreme", 250, 500, [
        "90% 1 A/ Kairo Sword, 10% 1 S/ Kairo Shield, 100% 5 - 5 Kairo Flan, 80% 1 - 1 Kairo Creamy Cake, 10% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Aid Specialist",
        "55% 1 A/ Kairo Sword, 1% 1 S/ Sally Prin Figure, 100% 3 - 5 Kairo Flan, 40% 1 - 1 Bounty Bag, 25% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Revive 100%",
      ]),
    ],
  },
  {
    title: "Kairobot Mage",
    encounters: [
      encounter("Easy", 25, 50, [
        "15% 1 A/ Kairo Hammer, 1% 1 S/ Kairo Shield, 100% 1 - 1 Kairo Flan, 20% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Instant Workshop",
        "5% 1 A/ Kairo Hammer, 1% 1 S/ Kairo Shield, 100% 1 - 1 Kairo Flan, 15% 1 - 1 Bounty Bag, 5% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal Maddy",
      ]),
      encounter("Normal", 55, 110, [
        "35% 1 A/ Kairo Hammer, 3% 1 S/ Kairo Shield, 100% 2 - 2 Kairo Flan, 40% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Instant Workshop",
        "10% 1 A/ Kairo Hammer, 2% 1 S/ Kairo Shield, 100% 1 - 2 Kairo Flan, 25% 1 - 1 Bounty Bag, 10% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal Maddy",
      ]),
      encounter("Hard", 100, 200, [
        "65% 1 A/ Kairo Hammer, 5% 1 S/ Kairo Shield, 100% 3 - 3 Kairo Flan, 60% 1 - 1 Kairo Creamy Cake, 5% 1 - 1 Skill Slots Up, 5% 1 Instant Construction",
        "45% 1 A/ Kairo Hammer, 3% 1 S/ Kairo Shield, 100% 2 - 3 Kairo Flan, 35% 1 - 1 Bounty Bag, 20% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Extreme", 200, 400, [
        "90% 1 A/ Kairo Lance, 5% 1 S/ Kairo Shield, 100% 5 - 5 Kairo Flan, 80% 1 - 1 Kairo Creamy Cake, 10% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 All-Out Sprint",
        "55% 1 A/ Kairo Lance, 1% 1 S/ Sally Prin Figure, 100% 3 - 5 Kairo Flan, 40% 1 - 1 Bounty Bag, 25% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Heal M, 5% 1 F Rank Scholar",
      ]),
    ],
  },
  {
    title: "Aloha Kairobot",
    encounters: [
      encounter("Easy", 30, 60, [
        "15% 1 A/ Kairo Bow, 100% 1 - 1 Kairo Flan, 20% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Instant Weeding",
        "5% 1 A/ Kairo Bow, 100% 1 - 1 Kairo Flan, 15% 1 - 1 Bounty Bag, 5% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Normal", 60, 120, [
        "35% 1 A/ Kairo Bow, 100% 2 - 2 Kairo Flan, 40% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Instant Weeding",
        "10% 1 A/ Kairo Bow, 100% 1 - 2 Kairo Flan, 25% 1 - 1 Bounty Bag, 10% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Hard", 110, 220, [
        "65% 1 A/ Kairo Bow, 3% 1 S/ Kairo Shield, 100% 3 - 3 Kairo Flan, 60% 1 - 1 Kairo Creamy Cake, 5% 1 - 1 Skill Slots Up, 5% 1 Instant Treasure Analysis",
        "45% 1 A/ Kairo Bow, 1% 1 S/ Kairo Shield, 100% 2 - 3 Kairo Flan, 35% 1 - 1 Bounty Bag, 20% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
      encounter("Extreme", 220, 440, [
        "90% 1 A/ Kairo Bow, 5% 1 S/ Kairo Shield, 100% 5 - 5 Kairo Flan, 80% 1 - 1 Kairo Creamy Cake, 10% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Instant Treasure Analysis",
        "55% 1 A/ Kairo Bow, 1% 1 S/ Kairo Shield, 100% 3 - 5 Kairo Flan, 40% 1 - 1 Bounty Bag, 25% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
    ],
  },
  {
    title: "Kairo Kommander",
    encounters: [
      encounter("Easy", 45, 90, [
        "15% 1 A/ Kairo Sword, 8% 1 A/ Kairo Hammer, 3% 1 A/ Kairo Lance, 100% 1 - 1 Kairo Flan, 20% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Direct Attack Ⅰ",
        "5% 1 A/ Kairo Sword, 8% 1 A/ Kairo Hammer, 3% 1 A/ Kairo Bow, 100% 1 - 1 Kairo Flan, 15% 1 - 1 Bounty Bag, 5% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Normal", 75, 150, [
        "35% 1 A/ Kairo Hammer, 8% 1 A/ Kairo Lance, 3% 1 A/ Kairo Bow, 100% 2 - 2 Kairo Flan, 40% 1 - 1 Kairo Creamy Cake, 3% 1 - 1 Skill Slots Up, 5% 1 Direct Attack Ⅰ",
        "10% 1 A/ Kairo Hammer, 8% 1 A/ Kairo Lance, 3% 1 A/ Kairo Sword, 100% 1 - 2 Kairo Flan, 25% 1 - 1 Bounty Bag, 10% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Hard", 160, 320, [
        "65% 1 A/ Kairo Lance, 8% 1 A/ Kairo Bow, 3% 1 A/ Kairo Hammer, 100% 3 - 3 Kairo Flan, 60% 1 - 1 Kairo Creamy Cake, 5% 1 - 1 Skill Slots Up, 5% 1 Area Attack Ⅰ",
        "45% 1 A/ Kairo Lance, 8% 1 A/ Kairo Sword, 3% 1 A/ Kairo Bow, 100% 2 - 3 Kairo Flan, 35% 1 - 1 Bounty Bag, 20% 1 - 1 Skill Slots Up, 1% 1 Kairo King Statue, 5% 1 Heal M",
      ]),
      encounter("Extreme", 300, 600, [
        "90% 1 A/ Kairo Bow, 8% 1 A/ Kairo Sword, 3% 1 A/ Kairo Hammer, 100% 5 - 5 Kairo Flan, 80% 1 - 1 Kairo Creamy Cake, 10% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Area Attack Ⅰ",
        "55% 1 A/ Kairo Bow, 8% 1 A/ Kairo Lance, 3% 1 A/ Kairo Sword, 100% 3 - 5 Kairo Flan, 40% 1 - 1 Bounty Bag, 25% 1 - 1 Skill Slots Up, 2% 1 Kairo King Statue, 5% 1 Revive 50%",
      ]),
    ],
  },
];

export const WAIRO_DUNGEON_LOOT_GROUP: SpecialBossLootGroup = {
  title: "Wairo Raid Dungeon",
  encounters: [
    encounter("Easy", 15, 30, [
      "15% 1 A/ Kairo Gun, 1% 1 S/ Wairo Shield, 100% 1 - 2 Wairo Flan, 40% 1 - 1 Wairo Creamy Cake, 10% 1 - 1 Bronze Trophy, 12% 1 Myriad Arrows, 1% 1 F Rank Scholar",
      "5% 1 A/ Kairo Gun, 1% 1 S/ Wairo Shield, 100% 1 - 1 Wairo Flan, 50% 1 - 1 Bounty Bag, 40% 1 - 1 Bronze Trophy, 12% 1 Parry",
    ]),
    encounter("Normal", 40, 80, [
      "35% 1 A/ Kairo Gun, 3% 1 S/ Wairo Shield, 100% 2 - 4 Wairo Flan, 60% 1 - 2 Wairo Creamy Cake, 20% 1 - 1 Silver Trophy, 12% 1 Half Reflect, 2% 1 F Rank Scholar",
      "10% 1 A/ Kairo Gun, 2% 1 S/ Wairo Shield, 100% 2 - 2 Wairo Flan, 60% 1 - 1 Bounty Bag, 50% 1 - 1 Silver Trophy, 12% 1 Critical UP",
    ]),
    encounter("Hard", 60, 120, [
      "65% 1 A/ Kairo Gun, 5% 1 S/ Wairo Shield, 100% 3 - 6 Wairo Flan, 70% 2 - 3 Wairo Creamy Cake, 30% 1 - 1 Gold Trophy, 12% 1 Counter, 3% 1 F Rank Scholar",
      "45% 1 A/ Kairo Gun, 3% 1 S/ Wairo Shield, 100% 3 - 3 Wairo Flan, 80% 1 - 1 Blessed Rain, 60% 1 - 1 Gold Trophy, 12% 1 Perfect Dodge",
    ]),
    encounter("Extreme", 80, 160, [
      "90% 1 A/ Kairo Gun, 10% 1 S/ Wairo Shield, 100% 5 - 10 Wairo Flan, 80% 3 - 5 Wairo Creamy Cake, 50% 1 - 1 Kairo Grail, 12% 1 Full Reflect, 5% 1 F Rank Scholar",
      "55% 1 A/ Kairo Gun, 5% 1 S/ Wairo Shield, 100% 5 - 5 Wairo Flan, 60% 1 - 1 Blessed Rain, 50% 1 - 1 Crown of Courage, 12% 1 Dodge UP",
    ]),
  ],
};
