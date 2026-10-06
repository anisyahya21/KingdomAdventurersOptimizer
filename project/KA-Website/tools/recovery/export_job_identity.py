"""Export the canonical combat job identities from the original `KA GameData - Job.csv`.

The generated catalog `src/lib/generated-job-skill-data.ts` only keys the D/C/B/A/S rank tokens,
so a genuine combat (`Rank`) row whose token is outside that set - the single `F Rank Scholar`,
csvId 132 - is absent from `JOB_CATALOG` while the shared job table still carries its curve. This
export records the sheet identity of every combat job row so `src/lib/battle-legality.ts` can
resolve such a job by its real producer row instead of blocking it or substituting another job.

Sources, in order of strength:
  * `data/Sheet csv/KA GameData - Job.csv` (the producer row: id, name, `parameters [][]`);
  * `combat_job_parameters` (native fixture) `PROFILES` - the same rows supplied to the original
    `JobData.GetParam*` / `CreateAdventurerParamSet` / `ExpSystem.LevelUp` getters, which also
    pins `BOUNDED_PARAMS = (10, 11, 12, 25)`;
  * `data/sheet-research/notes/job-notes.md` for the `Rank` (combat) / `Grade` (non-combat) rule.
"""
import csv
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
CSV_PATH = ROOT / "KA-Website" / "data" / "Sheet csv" / "KA GameData - Job.csv"
OUT = ROOT / "KA-Website" / "artifacts" / "kingdom-adventures" / "src" / "game-data" / "native-job-identity.json"

PARAM_ORDER = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
BLOCK_START = 40  # first `parameters [][]` block; each block is 5 cells wide
BLOCK_STRIDE = 5  # [unused, maxLevel, needExpCoefficient, initValue, raiseValue]


def param_blocks(row):
    blocks = []
    for index in range(len(PARAM_ORDER)):
        start = BLOCK_START + index * BLOCK_STRIDE
        cell = row[start + 1:start + 5]
        if len(cell) != 4 or not all(value.strip() for value in cell):
            return None
        blocks.append([int(value) for value in cell])
    return blocks


def main():
    raw = CSV_PATH.read_bytes()
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    job_rows = []
    for row in rows[2:]:
        if len(row) < 13 or " Rank " not in row[1]:
            continue
        token, _, job = row[1].partition(" Rank ")
        blocks = param_blocks(row)
        if blocks is None:
            raise SystemExit(f"could not read parameters for {row[1]!r}")
        job_rows.append({
            "appName": job.strip(),
            "sheetName": row[1],
            "csvId": int(row[0]),
            "sheetRankToken": token.strip(),
            "lifeColumn12": int(row[12]) if row[12].strip() else None,
            "parameters": blocks,
        })
    document = {
        "$comment": (
            "Canonical combat (`Rank`) job identities copied from the original Job.csv row, not "
            "inferred. `parameters` are the 13 `[maxLevel, needExpCoefficient, initValue, "
            "raiseValue]` blocks in native parameter-id order 10..22; the value at each level is "
            "`initValue + raiseValue * (level - 1)` capped at `maxLevel` "
            "(tools/recovery/check_combat_job_parameters.py)."
        ),
        "source": {
            "table": "KA GameData - Job.csv",
            "route": "KA-Website/data/Sheet csv/KA GameData - Job.csv",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "rowCount": len(rows) - 2,
            "nameRule": (
                "`<token> Rank <job>` = combat, `<token> Grade <job>` = non-combat "
                "(data/sheet-research/notes/job-notes.md)."
            ),
            "parameterColumns": {
                "blockStart": BLOCK_START,
                "blockStride": BLOCK_STRIDE,
                "blockOrder": [
                    "unused", "maxLevel", "needExpCoefficient", "initValue", "raiseValue",
                ],
                "parameterOrder": PARAM_ORDER,
            },
        },
        "nativeCrossCheck": {
            "tool": "KA-Website/tools/recovery/check_combat_job_parameters.py",
            "note": (
                "the fixture supplies the same rows to the original getters; its computed "
                "`PROFILES` for 10 job/rank rows agree with these blocks, and its findings pin "
                "`maxValue = value` for the bounded ids 10/11/12 and INT_MAX otherwise."
            ),
            "boundedParams": [10, 11, 12, 25],
        },
        "jobRows": job_rows,
    }
    OUT.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(job_rows), "out": str(OUT), "sha256": document["source"]["sha256"]}))


if __name__ == "__main__":
    main()
