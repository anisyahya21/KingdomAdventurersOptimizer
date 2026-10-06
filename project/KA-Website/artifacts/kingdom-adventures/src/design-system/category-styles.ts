export type KaCategory =
  | "job"
  | "shop"
  | "house"
  | "service"
  | "special"
  | "facility"
  | "event"
  | "monster"
  | "equipment"
  | "survey"
  | "marriage"
  | "skill"
  | "builder"
  | "building"
  | "guide"
  | "warning"
  | "success"
  | "muted";

export type KaStatus = "Ready" | "In Progress" | "Research Needed";
export type KaFacilityTabStyle = "env" | "materials" | "amenity" | "indoors" | "map";
export type KaRank = "S" | "A" | "B" | "C" | "D";
export type KaAffinity = "A" | "B" | "C" | "D" | "E";
/** Presentation-only encounter difficulty palette; not game data. */
export type KaDifficulty = "Easy" | "Normal" | "Hard" | "Extreme";

export const KA_CATEGORY_BADGE_CLASS: Record<KaCategory, string> = {
  job: "bg-sky-500/10 text-sky-700 border-sky-500/30 dark:text-sky-300",
  shop: "bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-300",
  house: "bg-blue-500/10 text-blue-700 border-blue-500/30 dark:text-blue-300",
  service: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30 dark:text-emerald-300",
  special: "bg-purple-500/10 text-purple-700 border-purple-500/30 dark:text-purple-300",
  facility: "bg-teal-500/10 text-teal-700 border-teal-500/30 dark:text-teal-300",
  event: "bg-rose-500/10 text-rose-700 border-rose-500/30 dark:text-rose-300",
  monster: "bg-red-500/10 text-red-700 border-red-500/30 dark:text-red-300",
  equipment: "bg-indigo-500/10 text-indigo-700 border-indigo-500/30 dark:text-indigo-300",
  survey: "bg-cyan-500/10 text-cyan-700 border-cyan-500/30 dark:text-cyan-300",
  marriage: "bg-rose-500/10 text-rose-700 border-rose-500/30 dark:text-rose-300",
  skill: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30 dark:text-emerald-300",
  builder: "bg-orange-500/10 text-orange-700 border-orange-500/30 dark:text-orange-300",
  building: "bg-lime-500/10 text-lime-700 border-lime-500/30 dark:text-lime-300",
  guide: "bg-violet-500/10 text-violet-700 border-violet-500/30 dark:text-violet-300",
  warning: "bg-orange-500/10 text-orange-700 border-orange-500/30 dark:text-orange-300",
  success: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30 dark:text-emerald-300",
  muted: "bg-muted/40 text-muted-foreground border-border",
};

export const KA_DIFFICULTY_BADGE_CLASS: Record<KaDifficulty, string> = {
  Easy: "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-300",
  Normal: "bg-yellow-500/10 text-yellow-700 border-yellow-500/30 dark:text-yellow-300",
  Hard: "bg-orange-500/10 text-orange-700 border-orange-500/30 dark:text-orange-300",
  Extreme: "bg-red-500/10 text-red-700 border-red-500/30 dark:text-red-300",
};

export const KA_STATUS_BADGE_CLASS: Record<KaStatus, string> = {
  Ready: "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300",
  "In Progress": "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300",
  "Research Needed": "bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300",
};

export const KA_FACILITY_TAB_BADGE_CLASS: Record<KaFacilityTabStyle, string> = {
  env: "bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-300",
  materials: "bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-300",
  amenity: "bg-sky-500/10 text-sky-700 border-sky-500/30 dark:text-sky-300",
  indoors: "bg-violet-500/10 text-violet-700 border-violet-500/30 dark:text-violet-300",
  map: "bg-teal-500/10 text-teal-700 border-teal-500/30 dark:text-teal-300",
};

export const KA_RANK_BADGE_CLASS: Record<KaRank, string> = {
  S: "bg-violet-100 text-violet-800 border-violet-300 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-700",
  A: "bg-rose-100 text-rose-800 border-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-700",
  B: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-700",
  C: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-700",
  D: "bg-slate-100 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-600",
};

export const KA_RANK_HEADER_CLASS: Record<KaRank, string> = {
  S: "bg-violet-50 dark:bg-violet-950/30",
  A: "bg-rose-50 dark:bg-rose-950/30",
  B: "bg-amber-50 dark:bg-amber-950/30",
  C: "bg-emerald-50 dark:bg-emerald-950/30",
  D: "bg-slate-50 dark:bg-slate-800/30",
};

export const KA_RANK_BORDER_CLASS: Record<KaRank, string> = {
  S: "border-violet-200 dark:border-violet-800",
  A: "border-rose-200 dark:border-rose-800",
  B: "border-amber-200 dark:border-amber-800",
  C: "border-emerald-200 dark:border-emerald-800",
  D: "border-slate-200 dark:border-slate-700",
};

export const KA_AFFINITY_BADGE_CLASS: Record<KaAffinity, string> = {
  A: "bg-amber-100 text-amber-800 border-amber-400 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-600",
  B: "bg-violet-100 text-violet-800 border-violet-400 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-600",
  C: "bg-emerald-100 text-emerald-800 border-emerald-400 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-600",
  D: "bg-sky-100 text-sky-800 border-sky-400 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-600",
  E: "bg-slate-100 text-slate-600 border-slate-400 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-600",
};
