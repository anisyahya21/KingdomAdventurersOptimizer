package main

import "strings"

// ProductionValidation carries optional offline coordinator evidence with a
// workload and its records. It is descriptive metadata, not a runtime gate.
type ProductionValidation struct {
	Schema                  string   `json:"schema"`
	CoordinatorThreadID     string   `json:"coordinatorThreadId"`
	Language                string   `json:"language"`
	Revision                string   `json:"revision"`
	ExecutableSHA256        string   `json:"executableSha256"`
	KernelSHA256            string   `json:"kernelSha256"`
	CatalogSHA256           string   `json:"catalogSha256"`
	FactsSHA256             string   `json:"factsSha256"`
	SourceSetSHA256         string   `json:"sourceSetSha256"`
	PolicySemanticsVersion  string   `json:"policySemanticsVersion"`
	ReportPath              string   `json:"reportPath"`
	ReportSHA256            string   `json:"reportSha256"`
	ValidatedOperations     []string `json:"validatedOperations"`
	ValidatedIntentFeatures []string `json:"validatedIntentFeatures"`
	Status                  string   `json:"status"`
}

// ProductionIdentity is retained in provenance to bind a record to the
// runtime and workload identity observed by the caller.
type ProductionIdentity struct {
	Revision               string `json:"revision"`
	ExecutableSHA256       string `json:"executableSha256"`
	KernelSHA256           string `json:"kernelSha256"`
	CatalogSHA256          string `json:"catalogSha256"`
	FactsSHA256            string `json:"factsSha256"`
	SourceSetSHA256        string `json:"sourceSetSha256"`
	PolicySemanticsVersion string `json:"policySemanticsVersion"`
}

// ExecutionScope is workload metadata. Missing values resolve to diagnostic,
// so older inputs never become promotion eligible by default.
type ExecutionScope struct {
	ExecutionMode        string                `json:"executionMode,omitempty"`
	TestPurpose          string                `json:"testPurpose,omitempty"`
	ProductionValidation *ProductionValidation `json:"productionValidation,omitempty"`
}

func NormalizeExecutionScope(scope ExecutionScope) ExecutionScope {
	if strings.TrimSpace(scope.ExecutionMode) == "" {
		scope.ExecutionMode = "diagnostic"
	}
	if strings.TrimSpace(scope.TestPurpose) == "" {
		scope.TestPurpose = "diagnostic"
	}
	return scope
}

// ProductionOutcome carries the native result's original Earned validity and
// basis. No separate inventory or dispatched-count claim is inferred here.
type ProductionOutcome struct {
	Earned       *int32
	EarnedValid  bool
	EarnedBasis  string
	NativeFields map[string]any
	Completed    bool
	Error        string
}

// ProductionClassification keeps the on-verdict finish cut separate from
// Earned validity. A diagnostic-purpose production run can execute and retain
// its native result, while remaining ineligible for strategy promotion.
type ProductionClassification struct {
	ExecutionScope
	Diagnostic       bool   `json:"diagnostic"`
	FinishDiagnostic bool   `json:"finishDiagnostic"`
	EarnedValid      bool   `json:"earnedValid"`
	EarnedBasis      string `json:"earnedBasis,omitempty"`
	StrategyEvidence bool   `json:"strategyEvidence"`
	ScoreEligible    bool   `json:"scoreEligible"`
}

// ClassifyProduction uses the native result's existing Earned validity. The
// identity argument is intentionally not an online authorization token;
// coordinator validation remains offline evidence in the workload metadata.
func ClassifyProduction(scope ExecutionScope, _ ProductionIdentity, finishPolicy string, result ProductionOutcome) ProductionClassification {
	scope = NormalizeExecutionScope(scope)
	class := ProductionClassification{
		ExecutionScope:   scope,
		FinishDiagnostic: finishPolicy == "on-verdict",
	}
	if result.Completed && result.Error == "" && result.EarnedValid && result.Earned != nil && *result.Earned >= 0 {
		class.EarnedValid = true
		class.EarnedBasis = result.EarnedBasis
	}
	productionPurpose := scope.ExecutionMode == "production" && scope.TestPurpose == "production"
	class.ScoreEligible = productionPurpose && class.EarnedValid
	class.StrategyEvidence = class.ScoreEligible
	class.Diagnostic = !class.ScoreEligible
	return class
}
