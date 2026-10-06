export type Weekday =
  | "Sunday"
  | "Monday"
  | "Tuesday"
  | "Wednesday"
  | "Thursday"
  | "Friday"
  | "Saturday";

export type JobCenterDraft = {
  day: Weekday;
  professions: string[];
};

export type DailyRankRewardTier = {
  rank: "S" | "A";
  weapon: string;
  armor: string;
  shield: string;
  overallItem1: string;
  overallItem2: string;
  ticket: string;
  skill: string;
};

export type DailyRankRewardDraft = {
  day: Weekday;
  rewards: DailyRankRewardTier[];
};

export type KairoRoomDraft = {
  day: Weekday;
  active: boolean;
  questName: string | null;
  equipmentFromBox: string[];
};

export const JOB_CENTER_DRAFTS: JobCenterDraft[] = [
  { day: "Monday", professions: ["Merchant", "Artisan", "Mage"] },
  { day: "Tuesday", professions: ["Farmer", "Blacksmith", "Paladin"] },
  { day: "Wednesday", professions: ["Carpenter", "Doctor", "Gunner"] },
  { day: "Thursday", professions: ["Mover", "Monk", "Archer"] },
  { day: "Friday", professions: ["Trader", "Rancher", "Ninja"] },
  { day: "Saturday", professions: ["Researcher", "Guard", "Samurai", "Pirate"] },
  { day: "Sunday", professions: ["Cook", "Knight", "Viking", "Champion", "Wizard"] },
];

export const DAILY_RANK_REWARD_DRAFTS: DailyRankRewardDraft[] = [
  {
    day: "Sunday",
    rewards: [
      { rank: "S", weapon: "A / Artisanal Sword", armor: "C / Skirt", shield: "C / Demonic Shield", overallItem1: "3x Skill Slot Up", overallItem2: "Eternal Candle", ticket: "Facility Trade Ticket", skill: "5-Hit Attack" },
      { rank: "A", weapon: "B / Commander's Sword", armor: "D / Thick Garmet", shield: "D / Iron Shield", overallItem1: "2x Skill Slots Up", overallItem2: "3x Bounty Bag", ticket: "Facility Trade Ticket", skill: "4-Hit Attack" },
    ],
  },
  {
    day: "Monday",
    rewards: [
      { rank: "S", weapon: "A / Red Club", armor: "B / Purple Cape", shield: "B / Green Shield", overallItem1: "3x Recovery Potion (L)", overallItem2: "Eternal Candle", ticket: "Job Trade Ticket", skill: "Lightning Magic V" },
      { rank: "A", weapon: "B / iron bar", armor: "C / Durable cloak", shield: "D / folk art shield", overallItem1: "2 diffusion recovery drugs", overallItem2: "3 speed shoes", ticket: "Occupation Trade Ticket", skill: "Lightning Magic IV" },
    ],
  },
  {
    day: "Tuesday",
    rewards: [
      { rank: "S", weapon: "A / Emerald Club", armor: "B / Bronze Armor", shield: "C / Nightmare Shield", overallItem1: "3x Skill Slot Up", overallItem2: "Eternal Candle", ticket: "Equipment Trade Ticket", skill: "Ice Magic V" },
      { rank: "A", weapon: "C / Art Bar", armor: "C / Light Mail", shield: "D / hard shield", overallItem1: "2 skill slots UP", overallItem2: "3 small amount of secret medicine", ticket: "Equipment Trade Ticket", skill: "Ice Magic IV" },
    ],
  },
  {
    day: "Wednesday",
    rewards: [
      { rank: "S", weapon: "A / Light Staff", armor: "C / Skirt", shield: "B / Shell Shield", overallItem1: "3x Recovery Potion (L)", overallItem2: "Eternal Candle", ticket: "Item Trade Ticket", skill: "Fire Magic V" },
      { rank: "A", weapon: "B / Shining Staff", armor: "D / Thick Garment", shield: "D / Folk Art Shield", overallItem1: "2x Recovery Potion (L)", overallItem2: "3x Recovery Energy", ticket: "Item Trade Ticket", skill: "Fire Magic IV" },
    ],
  },
  {
    day: "Thursday",
    rewards: [
      { rank: "S", weapon: "A / Giga Ninja Star", armor: "B / Purple Cape", shield: "B / Green Shield", overallItem1: "3x Skill Slot Up", overallItem2: "Eternal Candle", ticket: "Facility Trade Ticket", skill: "Area Attack III" },
      { rank: "A", weapon: "B / Flame Ninja Star", armor: "C / Durable Cape", shield: "C / Nightmare Shield", overallItem1: "2x Skill Slots Up", overallItem2: "3x Mystic Flute", ticket: "Facility Trade Ticket", skill: "Stealth" },
    ],
  },
  {
    day: "Friday",
    rewards: [
      { rank: "S", weapon: "A / Honor Sword", armor: "B / Bronze Armor", shield: "B / Scholar's Shield", overallItem1: "3x Recovery Potion (L)", overallItem2: "Eternal Candle", ticket: "Equipment Trade Ticket", skill: "Experience Up III" },
      { rank: "A", weapon: "B / Lightweight Sword", armor: "C / Light Mail", shield: "C / Noble Shield", overallItem1: "2x Recovery Potion (L)", overallItem2: "3x Holy Herb", ticket: "Equipment Trade Ticket", skill: "Experience Up II" },
    ],
  },
  {
    day: "Saturday",
    rewards: [
      { rank: "S", weapon: "A / High speed bow", armor: "B / Copper armor", shield: "B / Green Shield", overallItem1: "3 skill slots UP", overallItem2: "Candle of life", ticket: "Item Trade Ticket", skill: "Stubborn" },
      { rank: "A", weapon: "B / Hunting Bow", armor: "C / Light Mail", shield: "C / Nightmare Shield", overallItem1: "2x Skill Slot Up", overallItem2: "3x Energizing Stew", ticket: "Item Trade Ticket", skill: "Heal L" },
    ],
  },
];

export const KAIRO_ROOM_DRAFTS: KairoRoomDraft[] = [
  { day: "Monday", active: false, questName: null, equipmentFromBox: [] },
  { day: "Tuesday", active: true, questName: "Kairobot Mage's Challenge", equipmentFromBox: ["A / Kairo Hammer", "A / Kairo Lance"] },
  { day: "Wednesday", active: false, questName: null, equipmentFromBox: [] },
  { day: "Thursday", active: true, questName: "Aloha Kairobot's Challenge", equipmentFromBox: ["A / Kairo Bow"] },
  { day: "Friday", active: false, questName: null, equipmentFromBox: [] },
  { day: "Saturday", active: true, questName: "Kairobot Knight's Challenge", equipmentFromBox: ["A / Kairo Sword"] },
  { day: "Sunday", active: true, questName: "Kairo Kommander's Challenge", equipmentFromBox: ["A / Kairo Lance", "A / Kairo Hammer", "A / Kairo Sword", "A / Kairo Bow"] },
];
