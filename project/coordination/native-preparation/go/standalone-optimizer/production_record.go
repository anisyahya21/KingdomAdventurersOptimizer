package main

import (
	"encoding/json"
	"os"
)

const entitlementSemanticsVersion = "original-earned-with-queued-fallback-v1"

func workloadProductionIdentity(w Workload, executableHash, operation string) (ProductionIdentity, error) {
	identity := ProductionIdentity{Revision: buildRevision, ExecutableSHA256: executableHash, KernelSHA256: w.KernelSHA256, SourceSetSHA256: buildRevision, PolicySemanticsVersion: entitlementSemanticsVersion}
	scope := NormalizeExecutionScope(w.ExecutionScope)
	for _, item := range []struct {
		path   string
		target *string
	}{{w.CatalogPath, &identity.CatalogSHA256}, {w.FactsPath, &identity.FactsSHA256}} {
		if item.path == "" {
			continue
		}
		data, err := os.ReadFile(item.path)
		if err != nil {
			return identity, err
		}
		*item.target = digest(data)
	}
	_ = scope
	_ = operation
	return identity, nil
}

func applyProductionScope(record *Record, result *BattleResult, w Workload, identity ProductionIdentity) {
	scope := NormalizeExecutionScope(w.ExecutionScope)
	result.InventoryVerified = false
	var intent struct {
		FinishPolicy string `json:"finishPolicy"`
	}
	_ = json.Unmarshal(record.Intent, &intent)
	class := ClassifyProduction(scope, identity, intent.FinishPolicy, ProductionOutcome{Earned: result.Earned, EarnedValid: result.EarnedValid, EarnedBasis: result.EarnedBasis, Completed: result.Completed, Error: result.Error})
	record.ExecutionMode = class.ExecutionMode
	record.TestPurpose = class.TestPurpose
	record.FinishDiagnostic = class.FinishDiagnostic
	record.StrategyEvidence = class.StrategyEvidence
	record.ScoreEligible = class.ScoreEligible
	record.Diagnostic = class.Diagnostic
	record.Verified = class.StrategyEvidence
	record.ProductionValidation = scope.ProductionValidation
	record.Provenance["executionScope"] = jsonValue(scope)
	record.Provenance["productionIdentity"] = jsonValue(identity)
}
