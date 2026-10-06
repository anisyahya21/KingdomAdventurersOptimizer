"""First combat evidence pass. Run from workspace root with .venv/Scripts/python.exe.
Exports research only; does not alter website data or implement a simulator.
"""
import bisect
import hashlib
import json
import struct
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'RE-evidence/20260912-combat'
TABLES = ROOT / 'RE-evidence/20260911-treasure/xls-original/English.lproj'
NATIVE = ROOT / 'RE-evidence/G2.1/39257e72291d'


def table(name):
    return {int(r[0]): r for line in (TABLES / f'{name}.txt').read_text(encoding='utf-8-sig').splitlines()
            if (r := line.split('\t'))[0].isdigit()}


def array(row, pos, depth):
    count = int(row[pos]); pos += 1
    assert count >= 0
    values = []
    for _ in range(count):
        if depth == 1:
            value = int(row[pos]); pos += 1
        else:
            value, pos = array(row, pos, depth - 1)
        values.append(value)
    return values, pos


def main():
    OUT.mkdir(exist_ok=True)
    monsters, skills = table('Monster'), table('Skill')
    encounters = []
    used = set()
    for rid, r in table('SpecialBoss').items():
        enemies, pos = array(r, 6, 3)
        reward = int(r[pos]); terms, pos = array(r, pos + 1, 2)
        assert pos + 2 == len(r)
        assert len(enemies) == 1
        assert len(enemies[0]) == 5 * (int(r[4]) + 1)
        followers = []
        for index, pair in enumerate(enemies[0]):
            assert len(pair) == 2 and pair[1] == 100
            mid, rate = pair
            assert mid in monsters
            used.add(mid)
            followers.append(dict(selectionIndex=index, monsterId=mid, name=monsters[mid][1], checkRate=rate))
        boss = int(r[5]); assert boss in monsters; used.add(boss)
        encounters.append(dict(id=rid, title=r[1], levelField=int(r[2]), bossLevelField=int(r[3]),
                               difficulty=int(r[4]), bossId=boss, bossName=monsters[boss][1],
                               enemyTable=enemies, followers=followers, rewardGroup=reward,
                               appearanceTerms=terms, chipId=int(r[pos])))
    catalog = []
    for mid in sorted(used):
        r = monsters[mid]
        parameters, pos = array(r, 7, 2)
        exps, pos = array(r, pos, 1)
        sale, pos = array(r, pos, 2)
        assert pos + 19 == len(r), (mid, pos, len(r))
        sid = int(r[pos + 14]); assert sid in skills
        catalog.append(dict(id=mid, name=r[1], parametersRaw=parameters, skillId=sid,
                            skillSourceRow=skills[sid], skillAdditionItemId=int(r[pos + 15]),
                            note='skillId is loaded into the initial skill list; activation rules and parameter curves remain unvalidated'))
    assert len(encounters) == 20
    payload = dict(status='Static original-table/native research; no game runtime validation',
                   encounters=encounters, monsters=catalog,
                   limits=['Selection index is not final formation position.',
                           'levelField and bossLevelField are raw table fields, not certified final combat levels.',
                           'Skill rows are preserved raw; descriptions do not prove combat formulas.'])
    (OUT / 'encounters.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    equipment = []
    for rid, row in table('Equip').items():
        parameters, pos = array(row, 15, 2)
        prices, end = array(row, pos + 3, 1)
        assert end + 6 == len(row), (rid, end, len(row))
        equipment.append(dict(id=rid, name=row[1], category=int(row[2]), type=int(row[3]),
                              motion=int(row[9]), attribute=int(row[14]), parameters=parameters,
                              shootingRange=int(row[pos]), flags=int(row[-1]),
                              projectileFlag=bool(int(row[-1]) & 512)))
    skill_profiles = []
    field_names = ['category', 'type', 'value', 'count', 'shootingRange', 'searchingRange',
                   'range', 'minMp', 'maxMp', 'motion', 'requiredEquipType', 'res', 'img',
                   'seb', 'impactImg', 'impactSeb', 'iconU', 'iconV', 'currency']
    for sid, row in skills.items():
        prices, pos = array(row, 21, 1)
        assert pos + 10 == len(row), (sid, pos, len(row))
        skill_profiles.append(dict(id=sid, **{name: int(row[index+2]) for index, name in enumerate(field_names)},
                                   nameText=row[pos+5], nameArg=row[pos+6],
                                   explainText=row[pos+7], explainArg=row[pos+8], flags=int(row[-1])))
    profiles = dict(status='Original table fields and traced native selectors; not complete weapon/skill behavior',
                    equipment=equipment, skills=skill_profiles,
                    normalProjectileSkills=dict(bow=[s['id'] for s in skill_profiles if s['flags'] & 16384],
                                                gun=[s['id'] for s in skill_profiles if s['flags'] & 32768]),
                    limits=['Motion IDs are preserved; no animation-derived timing formula is assumed.',
                            'Skill count/value/ranges require type-specific consumers.',
                            'Catalog inclusion does not prove player equip/learn eligibility.'])
    assert profiles['normalProjectileSkills'] == dict(bow=[1], gun=[2])
    (OUT / 'weapon-skill-profiles.json').write_text(json.dumps(profiles, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    native_script = json.loads((NATIVE / 'dump/script.json').read_text(encoding='utf-8'))
    methods = native_script['ScriptMethod']
    names = {m['Address']: m['Name'] for m in methods}
    addresses = sorted(names)
    targets = [0x1476854,0x15840e4,0x15840e8,0x15840ec,0x15840f0,0x1584190,0x1584b90,0x15854bc,0x1585a18,
               0x15cdeec, 0x14f28a4, 0x163295c, 0x1456b3c, 0x1456a0c, 0x162f1e8, 0x14fbd04, 0x144cdc8,
               0x14492d8, 0x16f4f14, 0x16a8fa0, 0x16ac77c, 0x1477c5c, 0x147780c,
               0x1582b10, 0x1583438, 0x158708c, 0x15870a8, 0x15882c8,
               0x158955c, 0x15895c8, 0x15895e0, 0x15897cc, 0x15891d0,
               0x15833c4, 0x1588254, 0x158344c, 0x1588338,
               0x15bede8, 0x16602c8, 0x162f590, 0x15bf7e4, 0x15bf900,
               0x15a4a3c, 0x14d1490, 0x14d1ed4, 0x166070c, 0x162f508,
               0x16657b0, 0x1bc4880, 0x14d1030, 0x14d1dcc, 0x145f4c4,
               0x162fee8, 0x14c8fc4, 0x16ac6ec, 0x1631dd8,
               0x16aa254, 0x14eccac, 0x147405c, 0x14d0b5c,
               0x1474148, 0x14c9d5c, 0x1690388, 0x15be0a8,
               0x16609ec, 0x16825cc, 0x16825dc, 0x15bcfe0,
               0x1682554, 0x1682868, 0x16828a4, 0x16828c0,
               0x16207b8, 0x168c460, 0x168ca4c, 0x168cf80,
               0x145ff98, 0x22a513c, 0x168d178, 0x16204c0, 0x16208d0,
               0x168c380, 0x168c588, 0x168c65c, 0x168c624, 0x168c62c,
               0x22a53dc, 0x162cc34, 0x16657ec, 0x1620504,
               0x14ed864, 0x1632ab0, 0x1632a94, 0x168cc54, 0x168cde4,
               0x168f910, 0x168f930, 0x1584bf0, 0x15835a8,
               0x1442914, 0x168d1e8, 0x1460024, 0x168cad8, 0x168cb94,
               0x23fe33c, 0x145fee4, 0x168e214,
               0x145fdfc, 0x22a4e94, 0x22a4edc, 0x22a4f28, 0x22a4f48, 0x22a51f0,
               0x15854c0, 0x1585568, 0x1585a18, 0x1585a78, 0x1585ba0,
               0x1587d60, 0x158511c, 0x1585170, 0x1588690, 0x1588554,
               0x15e0718, 0x15e099c, 0x15e0c2c, 0x15e1c90, 0x15de668,
               0x15e2534, 0x145e8e8, 0x145e0c4, 0x145e000, 0x145e364,
               0x15e20a4, 0x168ebfc, 0x15cece0, 0x161f418, 0x161f5d0,
               0x145e7d8, 0x1621014, 0x168f988, 0x168f994, 0x168e8cc,
               0x161fac8, 0x161fae4,
               0x1583cf0, 0x1583cf8, 0x1583b48, 0x1584b90, 0x15854bc,
               0x1585e34, 0x1585f04, 0x15861f4, 0x14c5738,
               0x16828c8, 0x22141a8, 0x1585d2c, 0x1481e54, 0x165dea8,
               0x14c4284, 0x14c42e0, 0x148a950, 0x14dc884, 0x149079c,
               0x168f034, 0x191e2ec, 0x15df0e0, 0x16323a4, 0x15e03d8,
               0x15dff08, 0x14832d0, 0x191e33c, 0x16610f4, 0x161c200,
               0x1585250, 0x15884f8, 0x168e49c, 0x168e66c, 0x168e760,0x1477df0,0x18b2f00,0x1661e30,0x147b240,0x147af94,0x18b0450,0x15828f8,0x1587aa0,0x14c1b08,0x14c0e34,0x15ddc04,0x15ebb78,0x16aa254,0x16aa5a4,0x15e92e0,0x16aaa60,0x14c1450,0x15ddbfc,0x1583cf0,0x168f950,0x168f96c,0x1587d50,0x1660de4,0x147405c,0x1682824,0x147e2d8,0x14cec98,0x14ced54,0x14ceabc,0x14d1a50,0x14d1b10,0x14c8438,0x14c846c,0x16831d0,0x16833ec,0x1682a9c,0x1682b98,0x1682554,0x16825bc,0x16826cc,0x147df30,0x165e91c,0x14ce320,0x14ce450,0x1542a64,0x1542c4c,0x155cc88,0x155d078,0x160fc18,0x161047c,0x161077c,0x14d2928,0x22a4e94,0x22a4edc,0x145112c,0x23a6f34,0x231e644,0x23a5b94,0x2660020,0x26601f0,0x26602a0,0x147ede4,0x1474148,0x1479f70,0x147ef84,0x15bcfe0,0x14d497c,0x168e3c4,0x1587e64,0x14ef040,0x14ef240,0x1588078,0x1ae1b2c,0x14ef4e4,0x14ef338,0x1588414,0x1586444,0x1586954,0x1586a7c,0x14f3554,0x15899bc,0x1586a80,0x15870cc,0x1587aa4,0x14ed618,0x1584194,0x15845dc,0x15849d8,0x1588b38,0x1588b88,0x1588cb8,0x18c6404,0x1584310,0x158491c,0x1588f70,0x148c2e0,0x15e002c,0x148c3cc,0x148c8d8,0x15e204c,0x15e22f8,0x15e23b0,0x15e0498,0x15deb70,0x15df3a8,0x15df918,0x1588d78,0x23dd6a4,0x15de668,0x15e20dc,0x15e2190,0x15e2240,0x14d09d4,0x1632094,0x15018f0,0x15bfbf8,0x15896ec,0x1589714,0x1589b64,0x15e2418,0x1686390,0x147e8d4,0x1470c94,0x14ce0d8,0x146dc04,0x15ddb68,0x15017f4,0x16825cc,0x16825dc,0x16825f8,0x1500fe4,0x1500f14,0x14ffc8c,0x14dc884,0x15ed5e8,0x1500c10,0x165ddd8,0x145fc10,0x165ed2c,0x2651004,0x165e91c,0x1583550,0x158357c,0x1444988,0x14607f8,0x146081c,0x14608e4,0x1478ea0,0x155d4f8,0x155d678,0x155de48,0x1560000,0x15852ec,0x1589624,0x1589ad0,0x14f2e08,0x15bed5c,0x1472564,0x14d0214,0x1492f84,0x158ab14,0x15bbd2c,0x162f490,0x15bbdb4,0x161ca48,0x16657ec,0x1620504,0x162ed78,0x1bcf0a8,0x1cd31a8,0x1cd1e68,0x1cd1c3c,0x1cd3094,0x147ede4,0x147f134,0x147f2d4,0x147afbc,0x147f62c,0x147eab8,0x147f548,0x147ef84,0x147f09c,0x147f470,0x147f48c,0x1beec40,0x14fff90,0x1500b14,0x1b2ec28,0x1b2f4f8,0x1b2fe84,0x1b2fd50,0x1584150,0x15895f8,0x15e0620,0x155de18,0x155dc40,0x155dde8,0x155decc,0x155d678,0x14f2834,0x168e800,0x168f480,0x1479108,0x15bc35c,0x14709bc,0x14cd8f4,0x14cda30,0x16bbc28,0x1582a88,0x22141b8,0x168ee44,0x168f9c4,0x168ea2c,0x15e0ad4,0x15e0a14,0x1666d60,0x16655c8,0x1666da8,0x147a35c,0x14ecb1c,0x2694ce0,0x14c194c,0x1c95ce8,0x269eec4,0x1dec758,0x1bcf588,0x1bcf55c,0x1bcf614,0x156d360,0x15acd74,0x151fb30,0x14fd10c,0x1522660,0x158eb54,0x14ca1ec,0x14ca268,0x1473e30,0x1473e70,0x147ac40,0x14d4e98,0x147dfd8,0x147e030,0x14d4f28,0x155db94,0x234f940,0x2350e38,0x2351a5c,0x155d4f8,0x158eab8,0x1479108,0x165ee34,0x1480594,0x148058c,0x1586354,0x15863c0,0x242d520,0x24049bc,0x2404ee4,0x1589930,0x1471b84,0x23fe33c,0x23dd630,0x15bfb58,0x14dc718,0x2351a64,0x1500a04,0x1500a90,0x147e0a8,0x1671ce8,0x1671cf0,0x1671d1c,0x15cebf4,0x1595a30,0x1595ba0,0x15dc464,0x15dc510,0x15bc280,0x148cca4,0x146f154,0x146f2a4,0x148b7fc,0x165ea88,0x148b6cc,0x148b764,0x14caccc,0x14cae7c,0x1635b24,0x14d1880,0x14d1de4,0x14ce670,0x1587178,0x14c4284,0x14c42e0,0x14f1a68,0x14f0118,0x14f3160,0x14f316c,0x14f3184,0x14f3190,0x14f22dc,0x14f187c,0x16aa5a4,0x16aaa60,0x14f29b8,0x14f34ac,0x14f2ed0,0x14f2eb0,0x14ed47c,0x167b834,0x167aea0,0x167acac,0x162b344,0x162b3bc,0x167ad84,0x167adf8,0x1632094,0x15890c0,0x1589ca4,0x1589e08,0x1500e38,0x1500e54,0x1660ef8,0x1500f28,0x1479c08,0x1479ad0]
    targets += [0x1475c5c,0x15afd9c,0x1470264,0x18cf5cc,0x14ed3d4,
                0x14ef5a8,0x14f0090,0x14f3070,0x14f307c,0x14f30d0,0x14f3130,0x14f3138,0x14f35fc,0x1479898,
                0x16ab1cc,0x237d768,0x2387114,0x231c02c,0x231b7c8,0x147a3f4,
                0x237ff40,0x237fd7c,0x237fb88,0x1665628,0x237d0d4,0x22f44c8,0x14ed474,0x23438b4]
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    manifest = []
    with (NATIVE / 'inputs/libil2cpp.so').open('rb') as stream:
        elf = ELFFile(stream)
        assert elf['e_machine'] == 'EM_AARCH64'
        relocations = {r['r_offset']: r['r_addend'] for section in elf.iter_sections()
                       if section.name.startswith('.rela') for r in section.iter_relocations()}
        segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        for start in dict.fromkeys(targets):
            assert start in names
            end = addresses[bisect.bisect_right(addresses, start)]
            seg = next(s for s in segments if s['p_vaddr'] <= start and end <= s['p_vaddr'] + s['p_filesz'])
            offset = seg['p_offset'] + start - seg['p_vaddr']
            stream.seek(offset); data = stream.read(end - start)
            lines = []
            for ins in cs.disasm(data, start):
                dest = int(ins.op_str[1:], 16) if ins.mnemonic in ('b', 'bl') and ins.op_str.startswith('#') else 0
                lines.append(f'{ins.address:x}: {ins.mnemonic} {ins.op_str} {names.get(dest, "")}')
            (OUT / f'{start:x}.asm').write_text('\n'.join(lines) + '\n', encoding='utf-8')
            manifest.append(dict(rva=hex(start), name=names[start], offset=hex(offset),
                                 end=hex(end), sha256=hashlib.sha256(data).hexdigest()))
    sources = [TABLES / f'{n}.txt' for n in ['SpecialBoss', 'Monster', 'Skill', 'Equip']]
    metadata = (NATIVE / 'inputs/global-metadata.dat').read_bytes()
    header = struct.unpack_from('<20I', metadata)
    assert header[:2] == (0xfab11baf, 31)
    data_start, data_size = header[18:20]
    # Field referenced by FighterSystem.cctor via GOT0x2f64c98 -> metadata0x304bcc0.
    field_hash = '28CBA7A75FF2C938ABB19128CC8A2599CBE86407C74A5C1F5E8B4BC0B35D386D'
    field_address = relocations[0x2f64c98]
    field = next(m for m in native_script['ScriptMetadata'] if m['Address'] == field_address)
    assert field['Name'] == 'Field$<PrivateImplementationDetails>.' + field_hash
    matches = [p for p in range(data_start, data_start + data_size - 23)
               if hashlib.sha256(metadata[p:p+24]).hexdigest().upper() == field_hash]
    assert len(matches) == 1
    priorities = list(struct.unpack_from('<6i', metadata, matches[0]))
    assert priorities == [0, 3, 6, 9, 10, 13]
    formation = dict(status='Static native recovery; full battle runtime unvalidated',
                     priorities=priorities, priorityFieldHash=field_hash, metadataOffset=hex(matches[0]),
                     priorityFieldReference=dict(got='0x2f64c98', metadataAddress=hex(field_address), name=field['Name']),
                     sortKeys=['priority ascending', 'Param.GetValue(entity, Defense14, 0) descending', 'incoming index ascending'],
                     formationSkills=[dict(id=sid, type=int(r[3]), value=int(r[4]), name=r[-5])
                                      for sid, r in skills.items() if int(r[3]) == 60],
                     categoryOverrides=['first possessed type60 skill value, else2', 'visitor overrides with3',
                                        'entity identity equal to TeamMember.Leader overrides with5'],
                     offsets=dict(hasMonster=1, hasOwnerPlayer=2),
                     grid='column=index%5; row=index//5; offset=max(3,opponentCount//5+1); ownCell=(column,offset+1+row); opponentCell=(column,offset-row)')
    (OUT / 'formation-rules.json').write_text(json.dumps(formation, indent=2) + '\n', encoding='utf-8')
    constants = {}
    for label, got, count in [('heightExcludedComponents', 0x2f650a0, 3),
                              ('rotateRequiredComponents', 0x2f662d0, 4),
                              ('effectSebIds', 0x2f688e8, 48),
                              ('humanAverageParamIds', 0x2f68ae0, 12),
                              ('invocationDefault', 0x2f5d8f0, 3),
                              ('invocationRecovery', 0x2f5d8d8, 3),
                              ('invocationResurrection', 0x2f5d930, 3),
                              ('humanAnimationSebBases', 0x2f688e0, 43),
                              ('humanBehavior36Variants', 0x2f688d8, 3)]:
        field = next(m for m in native_script['ScriptMetadata'] if m['Address'] == relocations[got])
        digest = field['Name'].split('.')[-1]
        offsets = [p for p in range(data_start, data_start + data_size - count * 4 + 1)
                   if hashlib.sha256(metadata[p:p + count * 4]).hexdigest().upper() == digest]
        assert len(offsets) == 1
        constants[label] = dict(values=list(struct.unpack_from('<' + 'i' * count, metadata, offsets[0])),
                                got=hex(got), metadataOffset=hex(offsets[0]), fieldHash=digest)
    with (NATIVE / 'inputs/libil2cpp.so').open('rb') as stream:
        elf = ELFFile(stream)
        address = 0x752bf0
        seg = next(s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD'
                   and s['p_vaddr'] <= address < s['p_vaddr'] + s['p_filesz'])
        stream.seek(seg['p_offset'] + address - seg['p_vaddr'])
        constants['attackIntervalExponent'] = dict(value=struct.unpack('<d', stream.read(8))[0], rva=hex(address))
    (OUT / 'skill-combat-constants.json').write_text(json.dumps(constants, indent=2) + '\n', encoding='utf-8')
    sources += [NATIVE / 'inputs/libil2cpp.so', NATIVE / 'inputs/global-metadata.dat', NATIVE / 'dump/script.json', NATIVE / 'dump/dump.cs']
    audit = dict(sourceHashes={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                 methods=manifest, encounters=len(encounters), followerEntries=sum(len(e['followers']) for e in encounters),
                 uniqueMonsters=len(catalog), validation='Complete row consumption, nested dimensions, ID joins, all encounter counts/rates and ELF-backed method slices checked')
    (OUT / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: audit[k] for k in ['encounters', 'followerEntries', 'uniqueMonsters', 'validation']}))


if __name__ == '__main__':
    main()
