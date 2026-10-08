#pragma once

#include <nlohmann/json.hpp>
#include <string>
#include "preparation.hpp"

namespace kaopt {
using Json = nlohmann::json;
std::string sha256_text(const std::string& data);

// Implemented by the native admission/preparation and battle modules.
// Invalid raw intents must throw or return an object containing {"error":...}.
Json admit(const Json& raw, const Json& tables);
Json execute(const Json& snapshot, const std::string& kernelPath, bool timingTelemetry = false);

// Propose search children, exact named-axis tuning points, or paired skill-removal arms. Every
// child is validated through C++ admission and preparation; the request may carry paired seeds.
// Modes: search (count/seed/mutations), tuning (axis/unit?/targets), skills (count>=128/seedPairs?),
// and evaluate (parent only).
Json propose(const Json& raw, const Json& tables, const Json& request);
// Offline exact parity hook for the portable forest loader and native feature flattener.
Json predict_portable_forest(const std::string& modelPath,const std::string& modelSha256,
                             const std::string& sourceStateSha256,const Json& featureRows);

// Runs a deterministic, resumable search. Returns 0 on clean completion and
// nonzero on invalid configuration or an unrecoverable I/O/native error.
int run(const Json& config);
}
