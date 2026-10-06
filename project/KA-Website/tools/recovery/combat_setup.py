"""Assemble explicit ally stats and original encounter formation for research.

This prepares inputs; it does not predict fight outcomes or invent missing stats.
"""
import json
from combat_scenario import load_scenario
from combat_initial_state import formation
from combat_runtime_data import load_data
from combat_encounters import special_enemy_baseline
from combat_parameters import fighter_parameter, equipment_contribution, average_training_level
from combat_parameters import equip_master_lifted_types, equipment_affinity
from combat_resolution import SystemRandomState, random_below, skill_mp_cost


def prepare_setup(data):
    scenario = load_scenario(data)
    profiles = load_data('weapon-skill-profiles.json')
    equipment = {r['id']: r for r in profiles['equipment']}
    skills = {r['id']: r for r in profiles['skills']}
    lib = SystemRandomState(scenario['libSeed'])
    enemies = special_enemy_baseline(scenario['encounterId'], scenario['defeatCount'],
                                    lambda n: random_below(lib.next_int(), n))
    members = []
    for unit in scenario['ownUnits']:
        # Possessed type-48 EQUIP_MASTER rows lift a matching equipment type's declared job
        # affinity -1/0 to 1 before any contribution is computed (JobData.GetAffinity 0x16208d0).
        lifted = equip_master_lifted_types(skills[s] for s in unit['skills'])
        rows = [dict(equipment[s['id']], level=s['level'],
                     affinity=equipment_affinity(equipment[s['id']]['type'], s['affinity'], lifted))
                for s in unit['equipment']]
        def value(pid, maximum=False):
            return fighter_parameter(pid, unit['parameters'][pid], rows,
                lambda row, p, level: equipment_contribution(row, p, level,
                    affinity=row['affinity'], human=unit['human']),
                human=unit['human'], ally=True, maximum=maximum)
        effective = {pid: dict(value=value(pid), maximum=value(pid, True))
                     for pid in unit['parameters']}
        training = average_training_level(unit['parameters'], unit['human'])
        formation_skill = next((skills[s]['value'] for s in unit['skills']
                                if skills[s]['type'] == 60), None)
        members.append(dict(name=unit['name'], effectiveParameters=effective,
            effectiveDefense=effective[14]['value'], averageTrainingLevel=training,
            formationValue=formation_skill, visitor=unit['visitor'],
            leaderIdentity=unit['leaderIdentity'], monster=not unit['human'],
            ownerPlayer=unit.get('ownerPlayer', False), weaponId=unit['weaponId'],
            weaponRange=equipment[unit['weaponId']]['shootingRange'],
            skillCosts=[dict(skillId=s, cost=skill_mp_cost(skills[s]['minMp'],
                skills[s]['maxMp'], training, monster=not unit['human']))
                for s in unit['skills']]))
    placed = formation(members, 0, len(enemies['fighters']))
    return dict(status='Prepared research inputs; not a fight prediction', terrain=-1,
        encounter=enemies, ownFormationOrder=[r['incomingIndex'] for r in placed],
        ownUnits=sorted(placed, key=lambda r: r['incomingIndex']),
        holyHerbStock=scenario['holyHerbStock'], inputs=scenario['inputs'],
        limits=['Raw clone inputs must be supplied; displayed stats alone do not determine training or MP cost.',
                'Source-world initialization, complete phase integration and live replay remain pending.',
                'Equipment availability, job affinity and pet ownership must be captured separately.'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario')
    args = parser.parse_args()
    from pathlib import Path
    print(json.dumps(prepare_setup(json.loads(Path(args.scenario).read_text(encoding='utf-8'))), indent=2))
