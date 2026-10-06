import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  CombatActionType,
  Combatant,
  BattleResult,
  simulateBatch,
  simulateDuel,
} from "@/lib/combat-simulator";

const defaultCombatant = (name: string): Combatant => ({
  name,
  maxHp: 1200,
  atk: 220,
  def: 110,
  spd: 120,
  dex: 110,
  lck: 90,
  int: 80,
  weaponAdvantage: false,
  action: { type: "normal", value: 0 },
});

const actionLabels: Record<CombatActionType, string> = {
  normal: "Normal attack",
  attackSkill: "Attack skill",
  magicSkill: "Magic skill",
  armorBreaker: "Armor breaker",
};

function numericInput(value: number, onChange: (next: number) => void) {
  return (
    <Input
      type="number"
      inputMode="numeric"
      value={String(value)}
      onChange={(event) => {
        const next = parseInt(event.currentTarget.value, 10);
        if (!Number.isNaN(next)) onChange(next);
      }}
      className="h-8 text-sm w-full"
    />
  );
}

function CombatantPanel({
  combatant,
  onChange,
}: {
  combatant: Combatant;
  onChange: (next: Combatant) => void;
}) {
  return (
    <Card className="space-y-4">
      <CardHeader>
        <CardTitle className="text-sm">{combatant.name}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            Name
            <Input
              value={combatant.name}
              onChange={(event) => onChange({ ...combatant, name: event.currentTarget.value })}
              className="h-8 text-sm"
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Speed
            {numericInput(combatant.spd, (next) => onChange({ ...combatant, spd: next }))}
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            HP
            {numericInput(combatant.maxHp, (next) => onChange({ ...combatant, maxHp: Math.max(1, next) }))}
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            ATK
            {numericInput(combatant.atk, (next) => onChange({ ...combatant, atk: next }))}
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            DEF
            {numericInput(combatant.def, (next) => onChange({ ...combatant, def: next }))}
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            INT
            {numericInput(combatant.int, (next) => onChange({ ...combatant, int: next }))}
          </label>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            DEX
            {numericInput(combatant.dex, (next) => onChange({ ...combatant, dex: next }))}
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            LCK
            {numericInput(combatant.lck, (next) => onChange({ ...combatant, lck: next }))}
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Skill value
            {numericInput(combatant.action.value, (next) => onChange({ ...combatant, action: { ...combatant.action, value: next } }))}
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            Action type
            <select
              value={combatant.action.type}
              onChange={(event) => onChange({ ...combatant, action: { ...combatant.action, type: event.currentTarget.value as CombatActionType } })}
              className="h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground w-full"
            >
              {Object.entries(actionLabels).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={combatant.weaponAdvantage}
              onChange={(event) => onChange({ ...combatant, weaponAdvantage: event.currentTarget.checked })}
              className="h-4 w-4 rounded border border-border bg-background"
            />
            Weapon advantage
          </label>
        </div>

        <div className="text-xs text-muted-foreground">
          Attack skills and magic skills use the skill value to modify crit and hit chances. Armor breaker lowers the defender's DEF by the given value.
        </div>
      </CardContent>
    </Card>
  );
}

function formatNumber(value: number | null | undefined) {
  return value == null ? "-" : value.toLocaleString();
}

function formatPercent(part: number, total: number) {
  if (total <= 0) return "0.0%";
  return `${((part * 100) / total).toFixed(1)}%`;
}

const resultChipClass = "rounded-md border border-border bg-transparent px-3 py-2";

export default function CombatSimulatorPage() {
  const [left, setLeft] = useState<Combatant>(() => defaultCombatant("Attacker A"));
  const [right, setRight] = useState<Combatant>(() => defaultCombatant("Attacker B"));
  const [battle, setBattle] = useState<BattleResult | null>(null);
  const [batchCount, setBatchCount] = useState(100);
  const [batchResult, setBatchResult] = useState<{ leftWins: number; rightWins: number; draws: number; total: number } | null>(null);

  const summary = useMemo(() => {
    if (!battle) return null;
    const outcome = battle.winner ? `${battle.winner} standing` : "Draw";
    return `${outcome} after ${battle.rounds.length} strikes`;
  }, [battle]);

  const attackCounts = useMemo(() => {
    if (!battle) return null;
    let leftAttacks = 0;
    let rightAttacks = 0;
    for (const round of battle.rounds) {
      if (round.attacker === left.name) leftAttacks += 1;
      if (round.attacker === right.name) rightAttacks += 1;
    }
    return { leftAttacks, rightAttacks };
  }, [battle, left.name, right.name]);

  const runSimulation = () => {
    setBattle(simulateDuel(left, right));
    setBatchResult(simulateBatch(left, right, batchCount));
  };

  return (
    <div className="space-y-6 pb-10">
      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-semibold">Combat Simulator</p>
            <p className="text-sm text-muted-foreground">
              Simulate a simplified 1v1 duel where both sides attack and defend using the documented hit, crit, and damage formulas.
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <CombatantPanel combatant={left} onChange={setLeft} />
            <CombatantPanel combatant={right} onChange={setRight} />
          </div>

          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <div className="flex items-center gap-2">
              <Input
                type="number"
                inputMode="numeric"
                value={String(batchCount)}
                onChange={(event) => {
                  const next = parseInt(event.currentTarget.value, 10);
                  if (!Number.isNaN(next) && next > 0) setBatchCount(next);
                }}
                className="h-8 text-sm w-full max-w-[120px]"
              />
              <span className="text-xs text-muted-foreground">batch size</span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={runSimulation} className="w-full md:w-auto">
                Run simulation
              </Button>
            </div>
          </div>

          {(battle || batchResult) && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Result</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="space-y-3">
                  {batchResult && (
                    <div className={`${resultChipClass} text-sm text-foreground`}>
                      Batch outcomes: <strong>{left.name}</strong> ahead in <strong>{formatNumber(batchResult.leftWins)}</strong> fights (<strong>{formatPercent(batchResult.leftWins, batchResult.total)}</strong>), <strong>{right.name}</strong> ahead in <strong>{formatNumber(batchResult.rightWins)}</strong> fights (<strong>{formatPercent(batchResult.rightWins, batchResult.total)}</strong>), and <strong>{formatNumber(batchResult.draws)}</strong> draws (<strong>{formatPercent(batchResult.draws, batchResult.total)}</strong>) out of <strong>{formatNumber(batchResult.total)}</strong>.
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3">
                    {battle && (
                      <>
                        <div className={resultChipClass}>{summary}</div>
                        <div className={resultChipClass}>{left.name}: {formatNumber(battle.leftHp)} HP left</div>
                        <div className={resultChipClass}>{right.name}: {formatNumber(battle.rightHp)} HP left</div>
                        {attackCounts && (
                          <>
                            <div className={resultChipClass}>{left.name}: {formatNumber(attackCounts.leftAttacks)} attacks</div>
                            <div className={resultChipClass}>{right.name}: {formatNumber(attackCounts.rightAttacks)} attacks</div>
                          </>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {battle && (
                  <>
                    <Separator />

                    <div className="space-y-3">
                      {battle.rounds.map((round, index) => {
                        const note = round.result.note || `Crit roll ${round.result.rollCrit}${round.result.rollHit >= 0 ? `, hit roll ${round.result.rollHit}` : ``}`;
                        return (
                          <div key={`${round.attacker}-${index}`} className="rounded-lg border border-border bg-transparent p-3">
                            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                              <div>Strike {index + 1}</div>
                              <div>{round.attacker} â†’ {round.defender}</div>
                            </div>
                            <div className="mt-2 text-sm">
                              <div className="flex flex-wrap items-center gap-2">
                                <strong>{round.result.attackType}</strong>
                                <span>â€¢</span>
                                <span>{round.result.hit ? "Hit" : "Miss"}</span>
                                {round.result.crit ? <span className="font-semibold text-orange-500">ðŸ’¥ Crit</span> : null}
                              </div>
                              <div>Damage: {formatNumber(round.result.damage)}</div>
                              <div>Defender HP after: {formatNumber(round.defenderHpAfter)}</div>
                              <div className="text-xs text-muted-foreground">{note}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">How this works</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-muted-foreground">
              <p>Each strike uses the documented Kingdom Adventurers formulas:</p>
              <ul className="list-disc space-y-2 pl-5">
                <li>Crit chance from Luck and skill value.</li>
                <li>Hit chance from Dexterity, Speed, and Luck.</li>
                <li>Damage = ATK or INT Ã— random(80-120) / 100.</li>
                <li>Defense subtracts â‰ˆ DEF Ã— random(70-90) / 100.</li>
                <li>Critical reduces DEF further by 8Ã—.</li>
                <li>Weapon advantage multiplies damage by 1.5.</li>
              </ul>
              <p>This simulator runs both sides as attacker and defender in a simple duel loop.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

