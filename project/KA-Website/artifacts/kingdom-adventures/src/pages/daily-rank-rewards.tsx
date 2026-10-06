import { useEffect, useState } from "react";
import { Award, CalendarDays } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DAILY_RANK_REWARDS, getDailyRankEquipmentMismatches } from "@/game-data/daily-rank-rewards";
import { eventStatusCardClass, eventStatusClass, eventStatusLabel, getJapanWeekday, getLocalWeekday } from "@/lib/event-status";
import { getEquipmentIcon, getItemIcon } from "@/lib/equipment-icons";
import { getSkillIcon } from "@/lib/skill-icons";
import { getRankIcon } from "@/lib/rank-icons";

const COLUMNS = [
  { key: "weapon", label: "Weapon" },
  { key: "armor", label: "Armor" },
  { key: "shield", label: "Shield" },
  { key: "overallItem1", label: "Overall Item 1" },
  { key: "overallItem2", label: "Overall Item 2" },
  { key: "ticket", label: "Ticket" },
  { key: "skill", label: "Skill" },
] as const;

const EQUIPMENT_COLUMNS = new Set(["weapon", "armor", "shield"]);
const ITEM_COLUMNS = new Set(["overallItem1", "overallItem2", "ticket"]);

function RewardCell({ value, columnKey }: { value: string; columnKey: typeof COLUMNS[number]["key"] }) {
  if (!value) return <>-</>;
  const skillIcon = columnKey === "skill" ? getSkillIcon(value) : undefined;
  const icon = EQUIPMENT_COLUMNS.has(columnKey)
    ? getEquipmentIcon(null, value)
    : ITEM_COLUMNS.has(columnKey)
      ? getItemIcon(value)
      : skillIcon;
  const iconSize = EQUIPMENT_COLUMNS.has(columnKey) ? "h-8 w-8" : "h-6 w-6";
  return (
    <span className="flex items-center gap-1">
      {icon && <img src={icon} alt="" className={`${iconSize} shrink-0 object-contain`} style={{ imageRendering: "pixelated" }} />}
      <span>{value}</span>
    </span>
  );
}

export default function DailyRankRewardsPage() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const currentLocalDay = getLocalWeekday(now);
  const currentJapanDay = getJapanWeekday(now);

  useEffect(() => {
    if (!import.meta.env.DEV) {
      return;
    }
    const mismatches = getDailyRankEquipmentMismatches();
    if (mismatches.length > 0) {
      console.warn("[daily-rank-rewards] Equipment mismatches found", mismatches);
    }
  }, []);

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Award className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-xl font-bold tracking-tight">Daily Rank Rewards</h1>
        </div>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Kingdom Adventurers daily ranking board rewards by weekday, showing full rank payouts from F through S for weapons,
          armor, shields, tickets, skills, and overall item rewards.
        </p>
        <p className="text-xs text-muted-foreground/90 max-w-3xl">
          Card highlight and Reward status follow your local time. Competition status follows Japan time (JST).
        </p>
      </div>

      <div className="grid gap-4">
        {DAILY_RANK_REWARDS.map((entry) => {
          const isCurrentRewardDay = entry.day === currentLocalDay;
          const isCurrentCompetitionDay = entry.day === currentJapanDay;
          return (
          <Card key={entry.day} className={`shadow-sm ${eventStatusCardClass(isCurrentRewardDay ? "live" : "inactive")}`}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base flex items-center gap-2">
                    <CalendarDays className="w-4 h-4 text-primary" />
                    {entry.day}
                  </CardTitle>
                  <CardDescription>S and A ranking board rewards for this weekday.</CardDescription>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge variant="outline" className={eventStatusClass(isCurrentRewardDay ? "live" : "inactive")}>
                    Reward (Local): {eventStatusLabel(isCurrentRewardDay ? "live" : "inactive")}
                  </Badge>
                  <Badge variant="outline" className={eventStatusClass(isCurrentCompetitionDay ? "live" : "inactive")}>
                    Competition (JST): {eventStatusLabel(isCurrentCompetitionDay ? "live" : "inactive")}
                  </Badge>
                  {entry.rewards.map((reward) => (
                    <Badge key={reward.rankValue} variant="outline">
                      {getRankIcon(reward.rankLabel) ? (
                        <img src={getRankIcon(reward.rankLabel)} alt="" className="h-4 w-auto object-contain" style={{ imageRendering: "pixelated" }} />
                      ) : reward.rankLabel}
                    </Badge>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="hidden lg:grid lg:grid-cols-[100px_repeat(7,minmax(0,1fr))] gap-3 rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs font-medium text-muted-foreground">
                <div>Rank</div>
                {COLUMNS.map((column) => (
                  <div key={column.key}>{column.label}</div>
                ))}
              </div>
              <div className="space-y-3">
                {entry.rewards.map((reward) => (
                  <div key={reward.rankValue} className="rounded-lg border border-border/60 bg-card/60 px-3 py-3">
                    <div className="mb-3 lg:hidden">
                      <Badge variant="secondary">
                        {getRankIcon(reward.rankLabel) ? (
                          <img src={getRankIcon(reward.rankLabel)} alt="" className="h-4 w-auto object-contain" style={{ imageRendering: "pixelated" }} />
                        ) : reward.rankLabel}
                      </Badge>
                    </div>
                    <div className="hidden lg:grid lg:grid-cols-[100px_repeat(7,minmax(0,1fr))] gap-3 text-sm">
                      <div className="font-semibold text-foreground">
                        {getRankIcon(reward.rankLabel) ? (
                          <img src={getRankIcon(reward.rankLabel)} alt="" className="h-5 w-auto object-contain" style={{ imageRendering: "pixelated" }} />
                        ) : reward.rankLabel}
                      </div>
                      {COLUMNS.map((column) => (
                        <div key={column.key} className="text-muted-foreground">
                          <RewardCell value={reward[column.key]} columnKey={column.key} />
                        </div>
                      ))}
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2 lg:hidden">
                      {COLUMNS.map((column) => (
                        <div key={column.key} className="rounded-md border border-border/50 bg-muted/20 px-2 py-2">
                          <div className="text-[11px] text-muted-foreground">{column.label}</div>
                          <div className="text-sm font-medium text-foreground">
                            <RewardCell value={reward[column.key]} columnKey={column.key} />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
          );
        })}
      </div>
    </div>
  );
}

