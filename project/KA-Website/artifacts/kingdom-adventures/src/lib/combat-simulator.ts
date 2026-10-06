export type CombatActionType = "normal" | "attackSkill" | "magicSkill" | "armorBreaker";

export type CombatAction = {
  type: CombatActionType;
  value: number;
};

export type Combatant = {
  name: string;
  maxHp: number;
  atk: number;
  def: number;
  spd: number;
  dex: number;
  lck: number;
  int: number;
  weaponAdvantage: boolean;
  action: CombatAction;
};

export type AttackResult = {
  hit: boolean;
  crit: boolean;
  damage: number;
  damageBeforeDefense: number;
  rollCrit: number;
  rollHit: number;
  attackType: string;
  note: string;
};

export type BattleRound = {
  attacker: string;
  defender: string;
  defenderHpAfter: number;
  result: AttackResult;
};

export type BattleResult = {
  winner: string | null;
  rounds: BattleRound[];
  endedByRounds: boolean;
  leftHp: number;
  rightHp: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function randomInt(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export function rollPercent() {
  return randomInt(0, 100);
}

export function rollD100() {
  return randomInt(1, 100);
}

function critRateFromLuck(luck: number) {
  const clamped = Math.max(0, luck);
  if (clamped <= 99) {
    return 10 + (clamped * 10) / 99;
  }
  if (clamped <= 999) {
    return 21 + ((clamped - 100) * 14) / 899;
  }
  return 36 + ((clamped - 1000) * 39) / 98999;
}

export function calcCritRate(luck: number, skillValue: number) {
  const base = critRateFromLuck(luck);
  const modified = base * (100 + skillValue) / 100;
  return clamp(Math.round(modified), 0, 100);
}

export function calcHitRate(attackerDex: number, defenderSpd: number, defenderLck: number, skillValue: number) {
  const raw = 150 + attackerDex - defenderSpd / 5 - defenderLck / 5;
  const modified = raw * (100 - skillValue) / 100;
  return clamp(Math.round(modified), 5, 97);
}

export function attackFrameFromSpeed(speed: number) {
  return 30 * Math.pow(Math.max(1, speed) / 25, -0.362);
}

export function resolveAttack(attacker: Combatant, defender: Combatant): AttackResult {
  const skill = attacker.action;
  const skillValue = skill.type === "normal" ? 0 : skill.value;
  const critRate = calcCritRate(attacker.lck, skillValue);
  const rollCrit = rollPercent();
  const crit = rollCrit < critRate;

  const hitRate = crit
    ? 100
    : calcHitRate(attacker.dex, defender.spd, defender.lck, skillValue);
  const rollHit = crit ? -1 : rollD100();
  const hit = crit || rollHit < hitRate;
  const attackType =
    skill.type === "magicSkill"
      ? "Magic skill"
      : skill.type === "attackSkill"
      ? "Attack skill"
      : skill.type === "armorBreaker"
      ? "Armor breaker"
      : "Normal attack";

  if (!hit) {
    return {
      hit: false,
      crit: false,
      damage: 0,
      damageBeforeDefense: 0,
      rollCrit,
      rollHit,
      attackType,
      note: "Missed",
    };
  }

  const atkValue = skill.type === "magicSkill" ? attacker.int : attacker.atk;
  let defValue = defender.def;
  if (skill.type === "armorBreaker") {
    defValue = defValue * (100 - skillValue) / 100;
  }
  if (crit) {
    defValue = defValue / 8;
  }

  const damageBeforeDefense = Math.max(1, Math.floor((atkValue * randomInt(80, 120)) / 100));
  let damage = damageBeforeDefense - Math.floor((defValue * randomInt(70, 90)) / 100);
  if (damage <= 5) {
    damage = randomInt(1, 10);
  }
  if (attacker.weaponAdvantage) {
    damage = Math.floor(damage * 1.5);
  }

  return {
    hit: true,
    crit,
    damage,
    damageBeforeDefense,
    rollCrit,
    rollHit,
    attackType,
    note: attacker.weaponAdvantage ? "Weapon advantage applied" : "",
  };
}

export function simulateDuel(left: Combatant, right: Combatant, maxStrikes?: number): BattleResult {
  let leftHp = left.maxHp;
  let rightHp = right.maxHp;
  const rounds: BattleRound[] = [];

  const leftFrame = attackFrameFromSpeed(left.spd);
  const rightFrame = attackFrameFromSpeed(right.spd);
  const strikeCap = typeof maxStrikes === "number" && maxStrikes > 0 ? Math.floor(maxStrikes) : null;
  const hardSafetyCap = 50000;
  // Random initial gauge offsets reduce first-turn lock while preserving speed-based action frequency.
  let leftNext = Math.random() * leftFrame;
  let rightNext = Math.random() * rightFrame;

  while (leftHp > 0 && rightHp > 0 && rounds.length < hardSafetyCap) {
    if (strikeCap !== null && rounds.length >= strikeCap) break;
    if (leftHp <= 0 || rightHp <= 0) break;

    let attacker: Combatant;
    let defender: Combatant;
    let attackerIsLeft: boolean;

    if (leftNext === rightNext) {
      attackerIsLeft = Math.random() < 0.5;
    } else {
      attackerIsLeft = leftNext < rightNext;
    }

    if (attackerIsLeft) {
      attacker = left;
      defender = right;
      leftNext += leftFrame;
    } else {
      attacker = right;
      defender = left;
      rightNext += rightFrame;
    }

    const result = resolveAttack(attacker, defender);
    if (attackerIsLeft) {
      rightHp = Math.max(0, rightHp - result.damage);
      rounds.push({ attacker: left.name, defender: right.name, defenderHpAfter: rightHp, result });
    } else {
      leftHp = Math.max(0, leftHp - result.damage);
      rounds.push({ attacker: right.name, defender: left.name, defenderHpAfter: leftHp, result });
    }
  }

  const winner = leftHp > 0 && rightHp <= 0 ? left.name : rightHp > 0 && leftHp <= 0 ? right.name : null;
  const endedByUserCap = strikeCap !== null && leftHp > 0 && rightHp > 0 && rounds.length >= strikeCap;
  const endedBySafetyCap = leftHp > 0 && rightHp > 0 && rounds.length >= hardSafetyCap;
  return {
    winner,
    rounds,
    endedByRounds: endedByUserCap || endedBySafetyCap,
    leftHp,
    rightHp,
  };
}

export type BatchResult = {
  total: number;
  leftWins: number;
  rightWins: number;
  draws: number;
};

export function simulateBatch(left: Combatant, right: Combatant, runs: number, maxRounds?: number): BatchResult {
  const result: BatchResult = { total: runs, leftWins: 0, rightWins: 0, draws: 0 };
  const strikeCap = typeof maxRounds === "number" && maxRounds > 0 ? maxRounds * 2 : undefined;
  for (let i = 0; i < runs; i += 1) {
    const battle = simulateDuel(left, right, strikeCap);
    if (battle.winner === left.name) {
      result.leftWins += 1;
    } else if (battle.winner === right.name) {
      result.rightWins += 1;
    } else {
      result.draws += 1;
    }
  }
  return result;
}
