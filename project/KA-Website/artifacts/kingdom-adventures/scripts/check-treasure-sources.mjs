import { createServer } from 'vite';
import assert from 'node:assert/strict';
process.env.PORT='5173';process.env.BASE_PATH='/';
const server=await createServer({server:{middlewareMode:true},appType:'custom'});
try {
  const lookup=await server.ssrLoadModule('/src/lib/treasure-lookup.ts');
  const sources=await server.ssrLoadModule('/src/lib/item-sources.ts');
  const loot=await server.ssrLoadModule('/src/lib/monster-loot.ts');
  assert.equal(lookup.TREASURE_BOXES.length,1295);
  for(const name of ['Mine: Energy','Stables','Recover Energy','Holy Herb','A/ Light Staff']) {
    const source=sources.getItemSource(name).sources.find(s=>s.kind==='Gacha');
    assert(source,name);assert.equal(source.treasureId,undefined);
    assert(source.details.includes('normal'));
  }
  assert(sources.getItemSource('S/ Yggdrasil Sword').sources.some(s=>s.kind==='Gacha' && s.details.includes('S-rank')));
  assert(sources.getItemSource('Road').sources.some(s=>s.kind==='Gacha' && s.quantity==='7'));
  assert(!sources.getTreasureSources(730).some(s=>s.kind==='Gacha'));
  const arena=lookup.TREASURE_BOXES.filter(b=>(b.flag&8)!==0);
  assert.equal(arena.length,83);
  for (const box of arena) {
    const links=sources.getTreasureSources(box.id).filter(s=>s.kind==='Arena');
    assert.equal(links.length,1);
    assert.equal(links[0].boxRate,undefined);
    assert.equal(sources.treasureSourceLabel(box.id,box.name),'Arena battle reward');
  }
  assert(!sources.getTreasureSources(729).some(s=>s.kind==='Arena'));
  assert(sources.getItemSource('S/ Wairo Shield').sources.some(s=>s.treasureId===403 && s.location==='Legendary Cave' && s.rate===20));
  assert(sources.getTreasureSources(403).some(s=>s.location==='Legendary Cave'));
  for (const id of [680,688,696,704,712,720]) {
    assert(!sources.getTreasureSources(id).some(s=>s.location==='Legendary Cave'));
  }
  assert(sources.getTreasureSources(688).some(s=>s.kind==='Wairo Dungeon'));
  assert(!sources.getItemSource('S/ Wairo Shield').sources.some(s=>s.treasureId===688 && s.location==='Legendary Cave'));
  assert.equal(lookup.TREASURE_BOXES.filter(b=>sources.getTreasureSources(b.id).some(s=>s.location==='Legendary Cave')).length,21);
  assert(sources.getTreasureSources(728).some(s=>s.title==='Legendary Cave · White completion chest'));
  assert(sources.getTreasureSources(358).some(s=>s.title==='Normal cave · Area #0 · Level 1 · Exploration chest'));
  assert(sources.getTreasureSources(448).some(s=>s.title==='Normal cave · Area #0 · Level 1 · White completion chest'));
  assert(lookup.gatheringPool(100,100).some(b=>b.id===0));
  assert(!lookup.gatheringPool(100,101).some(b=>b.id===0));
  const cloth=sources.getItemSource('Pretty Cloth');
  assert(cloth.sources.some(s=>s.kind==='Terrain' && s.terrainType===3 && s.minLevel===50 && s.treasureId===57));
  assert(cloth.sources.some(s=>s.kind==='Monster' && s.monsterName==='Falcone' && s.rate===70));
  assert(!cloth.sources.some(s=>s.title==='Dungeon #' || s.title.startsWith('Crafting facility (')));
  assert(sources.getItemSource('Sturdy Board').sources.some(s=>s.quantity==='1-3'));
  assert.deepEqual(sources.searchTreasureBoxes('Tuesday Extreme').map(b=>b.id),[710,711]);
  assert(sources.searchTreasureBoxes('kairo box').some(b=>b.id===710));
  for (const id of [710,711]) {
    const origins=sources.getTreasureSources(id);
    assert.equal(origins.length,1); // Contents must not multiply the same source.
    assert.equal(origins[0].day,'Tuesday');assert.equal(origins[0].difficulty,'Extreme');
    assert.equal(origins[0].boxRate,50);assert.equal(origins[0].monsterName,'Kairobot Mage');
    assert(!('rate' in origins[0]));assert(!('quantity' in origins[0]));
  }
  assert.equal(lookup.chanceLabel(5),'5% · 1 in 20');
  assert.equal(sources.getTreasureSources(221).find(s=>s.monsterName==='Falcone').boxRate,5);
  const crafting=await server.ssrLoadModule('/src/lib/skill-crafting.ts');
  assert.equal(crafting.isSkillCraftable({name:'Myriad Arrows'}),false);
  assert.equal(crafting.isSkillCraftable({name:'All-Out Sprint'}),true);
  assert(!sources.getItemSource('Myriad Arrows').sources.some(s=>s.kind==='Skill crafting' || s.kind==='Skill Shop'));
  assert(sources.getItemSource('All-Out Sprint').sources.some(s=>s.kind==='Skill crafting' && s.title==='Skill Shop'));
  assert.equal(sources.getDailyRankSource(584).day,'Friday');
  assert.equal(sources.getDailyRankSource(584).rankLabel,'E');
  assert.equal(sources.getDailyRankSource(591).day,'Saturday');
  for(const box of lookup.TREASURE_BOXES.filter(b=>(b.flag&2)!==0)) assert(sources.getDailyRankSource(box.id));
  assert(sources.getItemSource('Wood Pouch').sources.some(s=>s.treasureId===584 && s.kind==='Daily Rank Reward'));
  for(const boss of lookup.TREASURE_SPECIAL_BOSSES) {
    const pool=lookup.TREASURE_BOXES.filter(b=>b.group===boss.rewardGroup);
    for(const box of pool) assert(sources.getTreasureSources(box.id).some(s=>s.title===boss.title && Math.abs(s.boxRate-100/pool.length)<1e-9));
  }
  for(const target of sources.ITEM_SOURCE_TARGETS) {
    assert.equal(new Set(target.sources.map(s=>s.key)).size,target.sources.length);
    for(const s of target.sources) if(s.treasureId!==undefined) assert(lookup.TREASURE_BY_ID.has(s.treasureId));
  }
  const item=loot.LOOT_ITEMS.find(i=>i.name==='Pretty Cloth');assert(item);
  const result=loot.getLootResult('Snow',45,item.id);
  assert(Math.abs(result.monsters.find(m=>m.name==='Falcone').itemChance-0.035)<1e-12);
  // A direct reference stays valid past the treasure row's gathering-selector bounds.
  assert(loot.getLootResult('Snow',1500,item.id).monsters.some(m=>m.name==='Falcone' && m.itemChance>0));
  console.log('PASS: shared source joins, quantity ranges, selection boundaries, monster direct lookup, 3.5% Falcone base reward.');
} finally {await server.close();}
