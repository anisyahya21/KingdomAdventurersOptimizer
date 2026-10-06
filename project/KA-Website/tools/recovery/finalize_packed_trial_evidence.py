"""Finalize exact byte accounting after all independent gates, without touching SQLite."""
import datetime,hashlib,json,pathlib
import packed_library_trial as trial

support=pathlib.Path('A:/KingdomAdventurersOptimizer/Community-Knowledge-20260928.packed-trial.sqlite.support')
reportpath=support/'report.json';report=json.loads(reportpath.read_text());dest=pathlib.Path(report['destination'])
stats=json.loads((support/'statistical-output-parity.json').read_text())
readers=json.loads((support/'reader-api-check.json').read_text())
scope=json.loads((support/'source-scope-details.json').read_text())
assert stats['state']==readers['state']=='PASS'
assert dest.exists() and report['exactParity']=='PASS'
report['sourceScopeDetails']=scope;report['statisticalOutputParity']=stats;report['readerApiPerformance']=readers
report['resumeSafetyEvidence']=json.loads((trial.HERE/'studies/battle_binary_corpus/full-trial-safety-check.json').read_text())
report['throughputProbe']=json.loads((trial.HERE/'studies/battle_binary_corpus/full-trial-throughput.json').read_text())
report['verifierThroughputProbe']=json.loads((trial.HERE/'studies/battle_binary_corpus/full-trial-verifier-throughput.json').read_text())
report['population']['counts'].setdefault('exactTextFallbackCells',0)
completed=sum(v['completedResults'] for v in scope['battlePopulations'].values())
report['population'].update(completedBattles=completed,pendingHoldoutRows=report['migratedBattles']-completed,
                           normalPackedBattleRows=completed-report['population']['counts']['fallbackRows'])
for value in report['payloadReadPerformance'].values():
    value['cache']='not controlled; sequential isolated calls after full validation'
    value['measurement']='payload fetch, representation-to-JSON-text decode and digest; source TEXT needs no decode; excludes JSON parsing. See readerApiPerformance for actual store readers.'
report['timings']['independentDestinationStatisticsSeconds']=stats['calculationSeconds']
first=json.loads((support/'log.jsonl').open().readline())
start=datetime.datetime.fromisoformat(first['updatedAt'].replace('Z','+00:00')).timestamp()
end=(support/'reader-api-check.json').stat().st_mtime
report['timings']['fullGateElapsedSeconds']=end-start
report['timings']['fullGateElapsedNote']='Whole trial elapsed through last reader gate; includes waiting/orchestration. Initial UTC timestamp has one-second precision. Individual measured stages remain separate.'
report['verificationTimingDetails']={k:json.loads((support/'verification.json').read_text())[k] for k in ('verificationSeconds','integritySeconds','rowsCompared')}
marker=json.loads((support/'VERIFIED-COMPLETE.json').read_text())
marker.update(statisticalOutputParitySha256=hashlib.sha256((support/'statistical-output-parity.json').read_bytes()).hexdigest(),
              readerApiParitySha256=hashlib.sha256((support/'reader-api-check.json').read_bytes()).hexdigest(),fullRequestedGate='PASS')
trial.atomic(support/'VERIFIED-COMPLETE.json',marker)
for _ in range(12):
    external={str(p.relative_to(support)):p.stat().st_size for p in support.rglob('*') if p.is_file()}
    sidecars={s:pathlib.Path(str(dest)+s).stat().st_size if pathlib.Path(str(dest)+s).exists() else 0 for s in ('-wal','-shm','-journal')}
    total=dest.stat().st_size+sum(external.values())+sum(sidecars.values());saved=report['sourceBytes']-total
    report.update(externalFiles=external,destinationSidecars=sidecars,supportBytesAtMeasurement=sum(external.values()),
        completeFootprintBytesAtMeasurement=total,packedGiB=total/2**30,savedBytes=saved,savedGB=saved/1e9,savedGiB=saved/2**30,
        percentSmaller=100*saved/report['sourceBytes'],sourceDestinationRatio=report['sourceBytes']/total,
        bytesPerRetainedBattle=total/report['migratedBattles'])
    before=reportpath.stat().st_size;trial.atomic(reportpath,report)
    if reportpath.stat().st_size==before:break
else:raise RuntimeError('Evidence byte accounting failed to stabilize')
assert total==dest.stat().st_size+sum(p.stat().st_size for p in support.rglob('*') if p.is_file())+sum(sidecars.values())
compact={k:report[k] for k in ('sourceGiB','packedGiB','savedGiB','percentSmaller','sourceBytes','destinationSQLiteBytes',
         'deployedDefinitionsBytes','completeFootprintBytesAtMeasurement','migratedBattles','exactParity','sourceUnchanged')}
compact.update(fullGateMinutes=report['timings']['fullGateElapsedSeconds']/60,
    fallbackRows=report['population']['counts']['fallbackRows'],fallbackPercent=report['population']['fallbackRowPercent'],
    pendingHoldoutRows=report['population']['pendingHoldoutRows'],allTables=len(report['sourceInventory']['tables']))
print(json.dumps(compact))
