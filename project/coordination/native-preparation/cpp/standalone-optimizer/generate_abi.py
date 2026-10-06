"""Offline source generation only; never imports or runs the battle runtime."""
import ast
from pathlib import Path
root = Path(__file__).resolve().parents[4]
source = root / 'KA-Website/tools/recovery/ka_abi.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
constants = {}
types = {'c_int32':'std::int32_t','c_int64':'std::int64_t','c_uint32':'std::uint32_t','c_uint64':'std::uint64_t','c_uint8':'std::uint8_t','c_float':'float'}
def number(node):
    return node.value if isinstance(node, ast.Constant) else constants[node.id]
def declaration(node, name):
    dims=[]
    while isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mult):
        dims.append(number(node.right)); node=node.left
    base=types[node.attr] if isinstance(node,ast.Attribute) else node.id
    return base+' '+name+''.join('['+str(n)+']' for n in dims)+';'
out=['#pragma once','#include <cstdint>','// Generated from canonical ka_abi.py ctypes declarations; no runtime import.']
for item in tree.body:
    if isinstance(item,ast.Assign) and isinstance(item.targets[0],ast.Name) and isinstance(item.value,ast.Constant) and isinstance(item.value.value,int):
        constants[item.targets[0].id]=item.value.value
    if isinstance(item,ast.ClassDef):
        if item.name=='KaBattleReport': break
        fields=next((x.value for x in item.body if isinstance(x,ast.Assign) and isinstance(x.targets[0],ast.Name) and x.targets[0].id=='_fields_'),None)
        if fields is None: continue
        out.append('struct '+item.name+' {')
        for field in fields.elts:
            out.append('    '+declaration(field.elts[1],field.elts[0].value))
        out.append('};')
Path(__file__).with_name('native_abi.hpp').write_text('\n'.join(out)+'\n',encoding='utf-8')
