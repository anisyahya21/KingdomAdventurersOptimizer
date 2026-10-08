"""Run saved C++ objective and learner fixtures without launching battles."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "tests" / "mechanism-objectives"
OUTPUT = ROOT / "runtime" / "objective-verification" / "cpp"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def invoke(exe, flag, fixture, output):
    subprocess.run([str(exe), flag, str(fixture), str(output)], check=True)
    return read_json(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path,
                        help="Freshly built corrected C++ executable")
    args = parser.parse_args()
    exe = args.exe.resolve()
    if not exe.is_file():
        parser.error(f"executable does not exist: {exe}")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    objective_in = FIXTURES / "objective-v3-oracle.json"
    parent_in = FIXTURES / "active-parent-fixture.json"
    actions_in = FIXTURES / "controlled-actions-fixture.json"
    feedback_in = FIXTURES / "learner-feedback-replay-fixture.json"

    objective = invoke(exe, "--objective-oracle", objective_in,
                       OUTPUT / "objective-v3-actual.json")
    parent = invoke(exe, "--active-parent-oracle", parent_in,
                    OUTPUT / "active-parent-actual.json")
    actions = invoke(exe, "--controlled-actions-oracle", actions_in,
                     OUTPUT / "controlled-actions-actual.json")
    feedback = invoke(exe, "--learner-feedback-replay-oracle", feedback_in,
                      OUTPUT / "learner-feedback-replay-actual.json")

    expected_objective = read_json(objective_in)
    assert len(expected_objective["cases"]) == len(objective["cases"])
    for expected_case, actual_case in zip(expected_objective["cases"], objective["cases"]):
        assert expected_case["expected"] == actual_case.get("actual", actual_case), expected_case["id"]

    expected_parent = read_json(FIXTURES / "active-parent-expected.json")
    parent_keys = ("candidate", "source", "choicePool", "weights")
    assert {key: expected_parent[key] for key in parent_keys} == {
        key: parent[key] for key in parent_keys
    }

    expected_actions = read_json(FIXTURES / "controlled-actions-expected.json")
    action_keys = ("op", "unitIndex", "parameter", "target", "minimum", "maximum",
                   "learnerWeight", "learnerComponents")
    assert len(expected_actions["actions"]) == len(actions["actions"])
    for expected_action, actual_action in zip(expected_actions["actions"], actions["actions"]):
        assert {key: expected_action[key] for key in action_keys} == {
            key: actual_action[key] for key in action_keys
        }
    assert expected_actions["guidedChoices"] == actions["guidedChoices"]

    state = feedback["state"]
    assert feedback["firstPassApplied"] == ["child-improved", "child-unproductive"]
    assert feedback["restartPassApplied"] == ["child-incomplete"]
    assert feedback["repeatedRestartApplied"] == []
    assert feedback["budgetResumeCounts"] == [37, 193]
    assert state["pendingCandidateIds"] == ["child-no-evidence"]
    assert state["mechanismImprovements"] == {
        "child-improved": ["potential", "setup"],
        "child-unproductive": [],
        "child-incomplete": ["potential", "setup"],
    }
    operators = state["mechanismLearnerState"]["operatorStats"]["0"]
    assert operators["set-stat"] == {"attempts": 7, "improved": 3, "unproductive": 3}
    assert operators["stat:atk"]["attempts"] == 1
    assert operators["stat:speed"]["attempts"] == 1
    assert operators["stat:lck"]["attempts"] == 1
    assert state["mechanismLearnerState"]["statAnchors"]["0"] == {"13": 180, "16": 160}
    scales = state["mechanismLearnerState"]["statScales"]["0"]
    assert scales["13"]["1.05"] == {"attempts": 1, "planned": 1}
    assert scales["15"]["1.15"] == {"attempts": 1, "planned": 1}
    assert scales["16"]["1.2"] == {"attempts": 1, "planned": 1}
    assert all(child["childRecord"]["earnedCount"] == 0
               for child in read_json(feedback_in)["children"])

    report = {
        "schema": "ka-cpp-objective-offline-verification-1",
        "status": "pass",
        "nativeExecutableSha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "objectiveOracleCases": len(expected_objective["cases"]),
        "activeParentFixture": "pass",
        "controlledActionCount": len(actions["actions"]),
        "guidedChoiceCases": len(actions["guidedChoices"]),
        "restartFeedbackFixture": "pass",
        "feedbackFirstPassCount": len(feedback["firstPassApplied"]),
        "feedbackAfterResumeCount": len(feedback["restartPassApplied"]),
        "repeatedResumeFeedbackCount": len(feedback["repeatedRestartApplied"]),
        "budgetResumeCounts": feedback["budgetResumeCounts"],
        "battlesLaunched": 0,
        "outputDirectory": "runtime/objective-verification/cpp",
    }
    report_path = OUTPUT / "verification-report.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
