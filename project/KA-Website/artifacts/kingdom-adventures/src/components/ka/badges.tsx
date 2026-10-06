import type React from "react";
import { Badge } from "@/components/ui/badge";
import {
  KA_AFFINITY_BADGE_CLASS,
  KA_CATEGORY_BADGE_CLASS,
  KA_DIFFICULTY_BADGE_CLASS,
  KA_RANK_BADGE_CLASS,
  type KaAffinity,
  type KaCategory,
  type KaDifficulty,
  type KaRank,
} from "@/design-system/category-styles";
import { cn } from "@/lib/utils";

type BadgeProps = {
  children: React.ReactNode;
  className?: string;
};

export function RankBadge({ rank, className }: { rank: KaRank | string; className?: string }) {
  const style = KA_RANK_BADGE_CLASS[rank as KaRank] ?? KA_CATEGORY_BADGE_CLASS.muted;
  return (
    <Badge variant="outline" className={cn("text-[10px] shrink-0", style, className)}>
      {rank}
    </Badge>
  );
}

export function AffinityBadge({ affinity, className }: { affinity: KaAffinity | string; className?: string }) {
  const style = KA_AFFINITY_BADGE_CLASS[affinity as KaAffinity] ?? KA_CATEGORY_BADGE_CLASS.muted;
  return (
    <Badge variant="outline" className={cn("text-[10px] shrink-0", style, className)}>
      {affinity}
    </Badge>
  );
}

export function DifficultyBadge({ difficulty, className }: { difficulty: KaDifficulty | string; className?: string }) {
  const style = KA_DIFFICULTY_BADGE_CLASS[difficulty as KaDifficulty] ?? KA_CATEGORY_BADGE_CLASS.muted;
  return (
    <Badge variant="outline" className={cn("text-[10px] shrink-0", style, className)}>
      {difficulty}
    </Badge>
  );
}

export function ToneBadge({ category, children, className }: BadgeProps & { category: KaCategory }) {
  return (
    <Badge variant="outline" className={cn("text-[10px] shrink-0", KA_CATEGORY_BADGE_CLASS[category], className)}>
      {children}
    </Badge>
  );
}
