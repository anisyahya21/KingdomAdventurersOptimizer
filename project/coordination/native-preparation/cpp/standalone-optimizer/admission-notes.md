# C++ scenario admission slice

`admit(scenario, tables)` ports the ordinary explicit roster/input branch of `combat_scenario.load_scenario` into C++. Supply a tables bundle containing `tables["weapon-skill-profiles"]` (`skills` and `equipment` arrays); validation returns a lossless scenario copy and rejects invalid or unsupported forms. `nlohmann::json` is vendored at `coordination/native-preparation/cpp/include/nlohmann/json.hpp`.

Implemented source branches: schema and required scenario scalars; encounter/tick/defeat ranges; raw human/monster parameter presence and signed-32 fields; skill IDs and invocation levels; equipment/weapon identity and category; friend/vehicle/flags/parameter-link restrictions; explicit inputs, finish policy, Holy Herb stock/trigger/watch limits.

The declared `isolated-scene0` start profile is supported with signed32 cells and empty startingStatus. Explicitly rejected pending port: `housePets`/`householdOwners` normalization; captured `prePlacement`; nonempty startingStatus; item rows, finite stock and item inputs. The accepted input type is `holy_herb`; finish dispatch is controlled by finishPolicy. Admission validates intent; preparation and execution supply the remaining optimizer path.
