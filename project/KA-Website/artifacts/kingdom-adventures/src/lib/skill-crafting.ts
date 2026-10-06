import skillCsv from "../../../../data/sheet-research/raw-copies/KA GameData - Skill.csv?raw";
import { parseCsv } from "./monster-truth";

const rows = parseCsv(skillCsv);
const header = rows[0];
const field = (row:string[],name:string) => row[header.indexOf(name)];
const normalize = (name:string) => name.trim().toLowerCase();
const skills = new Map(rows.slice(1).map(row => {
  const name = field(row,"nameText").replace("<0>",field(row,"nameArg"));
  return [normalize(name), {name, flags:Number(field(row,"flag")), studioLevel:Number(field(row,"craftTermStudioLevel")), intelligence:Number(field(row,"craftTermIntelligence"))}];
}));

// SkillData.FLAG_CRAFTABLE = 2. Prices and level fields exist on noncraftable skills too.
export function isSkillCraftable(skill:{name:string;flags?:number}) {
  const flags = skills.get(normalize(skill.name))?.flags ?? skill.flags;
  return typeof flags === "number" && (flags & 2) !== 0;
}
export function getSkillCrafting(name:string) {
  const skill=skills.get(normalize(name));
  return skill && isSkillCraftable(skill) ? skill : undefined;
}
