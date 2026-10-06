import type React from "react";
import { Link } from "wouter";
import { getShopHref } from "@/lib/shop-utils";
import { cn } from "@/lib/utils";

export type KaEntityType =
  | "job"
  | "shop"
  | "building"
  | "facility"
  | "equipment"
  | "skill"
  | "monster"
  | "guide";

type EntityLinkProps = {
  type: KaEntityType;
  name: string;
  children?: React.ReactNode;
  className?: string;
  unlinkedClassName?: string;
  onClick?: React.MouseEventHandler<HTMLAnchorElement>;
};

export function getEntityHref(type: KaEntityType, name: string): string | null {
  const cleanName = name.trim();
  if (!cleanName) return null;

  switch (type) {
    case "job":
      return `/jobs/${encodeURIComponent(cleanName)}`;
    case "shop":
      return getShopHref(cleanName);
    case "building":
      return getShopHref(cleanName) ?? `/houses?tab=houses&search=${encodeURIComponent(cleanName)}`;
    case "facility":
      return `/houses?tab=facilities&search=${encodeURIComponent(cleanName)}`;
    case "equipment":
      return `/equipment-stats?search=${encodeURIComponent(cleanName)}`;
    case "skill":
      return `/skills?search=${encodeURIComponent(cleanName)}`;
    case "monster":
      return `/monsters?search=${encodeURIComponent(cleanName)}`;
    case "guide":
      return "/guides";
  }
}

export function EntityLink({ type, name, children, className, unlinkedClassName, onClick }: EntityLinkProps) {
  const href = getEntityHref(type, name);
  const content = children ?? name;

  if (!href) {
    return <span className={unlinkedClassName}>{content}</span>;
  }

  return (
    <Link href={href} onClick={onClick} className={cn("underline-offset-2 hover:underline", className)}>
      {content}
    </Link>
  );
}
