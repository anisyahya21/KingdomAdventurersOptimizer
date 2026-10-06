import type React from "react";
import { Badge } from "@/components/ui/badge";
import { KA_CATEGORY_BADGE_CLASS, type KaCategory } from "@/design-system/category-styles";
import { cn } from "@/lib/utils";

type CategoryBadgeProps = {
  category: KaCategory;
  children: React.ReactNode;
  className?: string;
};

export function CategoryBadge({ category, children, className }: CategoryBadgeProps) {
  return (
    <Badge variant="outline" className={cn("text-[10px] shrink-0", KA_CATEGORY_BADGE_CLASS[category], className)}>
      {children}
    </Badge>
  );
}
