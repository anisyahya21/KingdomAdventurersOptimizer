/**
 * Native cross-job skill admission (`ecs.SkillComponent.CanSetSkill 0x14cfe40`).
 *
 * This is a MASTER-DATA fact, not saved-loadout ownership. `SkillData.flag_` (`BaseData +0x1c`)
 * carries the skill's equip-class bits 0x200 Attack / 0x400 Attack Magic / 0x800 Recovery magic
 * (0x1000 = "Type Duplicate OK"). `JobGroupData.flag_` (`BaseData +0x1c`) carries the job
 * group's 4 Attack / 1 Attack Magic / 2 Recovery magic bits. `CanSetSkill` pairs them
 * (0x200<->4, 0x400<->1, 0x800<->2, `BaseData.Check 0x161c200` = `(flag_ & bit) != 0`) and
 * returns failure `0x400000000` when a set skill bit has no matching job-group bit
 * (disassembly 0x14cfe40, gate at 0x14d00c0..0x14d0134). There is NO rank condition in the gate.
 *
 * Skill flags come from the existing catalog owner (`battle-skill-catalog.json` -> `flags`).
 * Job-group masks come from `skill-job-admission.json` (Job.csv `group` -> JobGroup.csv flag).
 * Per-player ownership / learned-skill rank stays a separately declared input.
 */
import skillJobAdmission from "@/game-data/skill-job-admission.json";
import { SKILL_BY_ID, type SkillCatalogEntry } from "@/lib/battle-setup";

export const SKILL_FLAG_ATTACK = 0x200;
export const SKILL_FLAG_ATTACK_MAGIC = 0x400;
export const SKILL_FLAG_RECOVERY_MAGIC = 0x800;
export const SKILL_FLAG_TYPE_DUPLICATE_OK = 0x1000;

/**
 * Native `SkillData.flags` bit that marks a battle skill (`dump.cs`
 * `SkillData.FLAG_FOR_BATTLE = 8`). A row without it is never reached by battle invocation:
 * `CanUseSkill 0x15df0e0` rejects the category-2 activity rows (`special-combat.md:447`), and the
 * always-on rows (type 48 EQUIP_MASTER, the auto-recovery rows, the town/priority abilities) have
 * no trigger level to choose. `Entity.AddSkill 0x1472564` still stores such a row and initialises
 * its invocation level to 1, so the saved invocation array stays aligned with the skill array.
 */
export const SKILL_FLAG_FOR_BATTLE = 0x8;

/** `Entity.AddSkill 0x1472564` and `SetSkillAt 0x14d0214` initialise invocation levels to 1. */
export const NATIVE_DEFAULT_INVOCATION_LEVEL = 1 as const;

export const JOB_GROUP_FLAG_ATTACK_MAGIC = 0x1;
export const JOB_GROUP_FLAG_RECOVERY_MAGIC = 0x2;
export const JOB_GROUP_FLAG_ATTACK = 0x4;

/** `CanSetSkill 0x14cfe40` return bitfield; success is `+1`. */
export const CAN_SET_SKILL_SUCCESS = 0x1;
export const CAN_SET_SKILL_DUPLICATE = 0x200000000;
export const CAN_SET_SKILL_SAME_TYPE = 0x300000000;
export const CAN_SET_SKILL_JOB_GROUP_MISMATCH = 0x400000000;

const admissionFile = skillJobAdmission as unknown as {
  $comment: string;
  source: unknown;
  jobGroupIndexByJob: Record<string, number>;
  jobGroupMaskByJob: Record<string, number>;
};

/** Canonical job name -> native job-group admission mask (`JobGroupData.flag_ +0x1c`). */
export const JOB_GROUP_MASK_BY_JOB: Readonly<Record<string, number>> = admissionFile.jobGroupMaskByJob;
export const JOB_GROUP_INDEX_BY_JOB: Readonly<Record<string, number>> = admissionFile.jobGroupIndexByJob;

export function jobGroupMaskForJob(jobName: string): number | null {
  return JOB_GROUP_MASK_BY_JOB[jobName] ?? null;
}

export function skillAdmissionFlags(skillId: number): number | null {
  return SKILL_BY_ID.get(skillId)?.flags ?? null;
}

export type SkillActivationStatus = "active" | "permanently_active" | "unknown";

/**
 * Whether the battle invocation level applies to a skill row. Only one input is used, recovered
 * master data: the `SkillData.flags` bit `FLAG_FOR_BATTLE` (dump.cs). A row without the bit is a
 * permanently-active/always-on row (the category-2 activity and always-on rows `CanUseSkill
 * 0x15df0e0` refuses or never invokes); its trigger level is not a player choice.
 *
 * `unknown` is returned when the id has no recovered `SkillData` row; it is never silently treated
 * as either state.
 */
export function skillActivationStatusFromFlags(flags: number | null): SkillActivationStatus {
  if (flags === null) return "unknown";
  return (flags & SKILL_FLAG_FOR_BATTLE) !== 0 ? "active" : "permanently_active";
}

export function skillActivationStatus(skillId: number): SkillActivationStatus {
  return skillActivationStatusFromFlags(skillAdmissionFlags(skillId));
}

/** The three native (skill bit -> job-group bit) admission pairs. */
const ADMISSION_PAIRS: ReadonlyArray<{ skill: number; jobGroup: number; label: string }> = [
  { skill: SKILL_FLAG_ATTACK, jobGroup: JOB_GROUP_FLAG_ATTACK, label: "attack" },
  { skill: SKILL_FLAG_ATTACK_MAGIC, jobGroup: JOB_GROUP_FLAG_ATTACK_MAGIC, label: "attack magic" },
  { skill: SKILL_FLAG_RECOVERY_MAGIC, jobGroup: JOB_GROUP_FLAG_RECOVERY_MAGIC, label: "recovery magic" },
];

/** Native job-group gate only: `0x400000000` on mismatch, `0` when admitted. */
export function jobGroupGateFailure(skillFlags: number, jobGroupMask: number): number {
  for (const { skill, jobGroup } of ADMISSION_PAIRS) {
    if ((skillFlags & skill) !== 0 && (jobGroupMask & jobGroup) === 0) return CAN_SET_SKILL_JOB_GROUP_MISMATCH;
  }
  return 0;
}

export type SkillAdmissionStatus = "admitted" | "rejected" | "unknown";

export type SkillAdmissionVerdict = {
  status: SkillAdmissionStatus;
  /** Native failure bit when rejected; `0` when admitted or unresolved. */
  failure: number;
  reason: string;
  jobGroupMask: number | null;
  skillFlags: number | null;
};

export type SkillAdmissionInput = {
  jobName: string;
  skillId: number;
  /**
   * Skills already occupying the component's other slots, for the native duplicate / same-type
   * rejects. Omit for the job-group gate alone. Multi-hit skills are never blanket-rejected:
   * only an exact id repeat (`0x200000000`) or a same-`type` repeat without the skill's own
   * `0x1000` "Type Duplicate OK" bit (`0x300000000`) fails.
   */
  existingSkillIds?: readonly number[];
};

/**
 * Reproduce `CanSetSkill 0x14cfe40` for one candidate slot, in native order: job-group mask,
 * then exact duplicate, then same-`type` (unless the existing skill allows type duplicates).
 * Rank and per-player learned-skill ownership are deliberately NOT part of this gate.
 */
export function canSetSkill({ jobName, skillId, existingSkillIds = [] }: SkillAdmissionInput): SkillAdmissionVerdict {
  const skillFlags = skillAdmissionFlags(skillId);
  const jobGroupMask = jobGroupMaskForJob(jobName);
  const entry: SkillCatalogEntry | undefined = SKILL_BY_ID.get(skillId);
  if (skillFlags === null || jobGroupMask === null || entry === undefined) {
    return {
      status: "unknown",
      failure: 0,
      reason:
        skillFlags === null
          ? `Skill id ${skillId} has no recovered SkillData row, so its +0x1c flag is unknown.`
          : `Job '${jobName}' has no recovered job group (Job.csv group -> JobGroupData.flag_ +0x1c), so its admission mask is unknown.`,
      jobGroupMask,
      skillFlags,
    };
  }

  const gate = jobGroupGateFailure(skillFlags, jobGroupMask);
  if (gate !== 0) {
    const needed = ADMISSION_PAIRS.filter((pair) => (skillFlags & pair.skill) !== 0)
      .map((pair) => pair.label)
      .join(", ");
    return {
      status: "rejected",
      failure: gate,
      reason: `Skill '${entry.nameText.replace(/<0>/g, entry.nameArg)}' (id ${skillId}) is ${needed} but '${jobName}' job group ${jobGroupMask} cannot equip it.`,
      jobGroupMask,
      skillFlags,
    };
  }

  for (const existingId of existingSkillIds) {
    const existing = SKILL_BY_ID.get(existingId);
    if (!existing) continue;
    if (existing.id === entry.id) {
      return {
        status: "rejected",
        failure: CAN_SET_SKILL_DUPLICATE,
        reason: `Skill id ${skillId} is already in another slot (native duplicate reject 0x200000000).`,
        jobGroupMask,
        skillFlags,
      };
    }
    if ((existing.flags & SKILL_FLAG_TYPE_DUPLICATE_OK) === 0 && existing.type === entry.type) {
      return {
        status: "rejected",
        failure: CAN_SET_SKILL_SAME_TYPE,
        reason: `Skill id ${skillId} repeats type ${entry.type} already used by id ${existingId} (native same-type reject 0x300000000).`,
        jobGroupMask,
        skillFlags,
      };
    }
  }

  return {
    status: "admitted",
    failure: 0,
    reason: `Job '${jobName}' job group ${jobGroupMask} satisfies the skill's admission bits.`,
    jobGroupMask,
    skillFlags,
  };
}
