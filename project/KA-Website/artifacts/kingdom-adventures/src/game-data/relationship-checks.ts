import { getJobsForBuilding } from "./job-buildings";

export const EXPECTED_SURVEY_CORPS_HQ_OWNER_JOBS = [
  "Carpenter",
  "Farmer",
  "Merchant",
  "Mover",
  "Rancher",
] as const;

export function getSurveyCorpsHqOwnerCheck() {
  const expected = [...EXPECTED_SURVEY_CORPS_HQ_OWNER_JOBS].sort();
  const actual = getJobsForBuilding("Survey Corps HQ").map((owner) => owner.jobName).sort();

  return {
    actual,
    expected,
    missing: expected.filter((jobName) => !actual.includes(jobName)),
    extra: actual.filter((jobName) => !expected.includes(jobName as typeof expected[number])),
  };
}

export function assertSurveyCorpsHqOwners() {
  const result = getSurveyCorpsHqOwnerCheck();
  if (result.missing.length > 0 || result.extra.length > 0) {
    throw new Error(
      `Survey Corps HQ owner mismatch. Missing: ${result.missing.join(", ") || "none"}. Extra: ${result.extra.join(", ") || "none"}.`,
    );
  }
}
