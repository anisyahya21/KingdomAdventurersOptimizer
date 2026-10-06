export { normJob, pairKey } from "./job-normalization";

export type MarriageRank = "S" | "A" | "B" | "C" | "D";

export const MARRIAGE_RANKS: MarriageRank[] = ["S", "A", "B", "C", "D"];

// Marriage compatibility is static reference data; server and browser copies are never authoritative.
export function completeMarriagePairs<T>(_candidate: T[] | undefined, bundled: T[]): T[] {
  return bundled;
}
