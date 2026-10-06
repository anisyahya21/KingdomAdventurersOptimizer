"""Strict reader for the two reviewed building INF dialects; suffixes uninterpreted."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class IndexKey:
    domain: str
    id: int

class InfIndex:
    def __init__(self,domain,raw):
        if domain not in ('building/image','building/seb'): raise ValueError('Unreviewed domain')
        self.domain=domain; self.rows={}
        text=raw.decode('utf-8',errors='strict')
        extension='.png' if domain.endswith('/image') else '.seb'
        for line_number,line in enumerate(text.splitlines(),1):
            if not line: continue
            match=re.fullmatch(r'([0-9]+)\t([^\t\r\n,]+)(?:,([^\t\r\n,]+))?',line)
            if not match: raise ValueError(f'Malformed row {line_number}')
            identity=int(match[1]); filename=match[2]; suffix=match[3]
            if identity in self.rows: raise ValueError(f'Duplicate ID {identity}')
            if not filename.endswith(extension): raise ValueError(f'Invalid filename extension at {line_number}')
            self.rows[identity]={'line':line_number,'id':identity,'domain':domain,'filename':filename,'suffix_uninterpreted':suffix}
        if not self.rows: raise ValueError('Empty index')

    def lookup(self,key):
        if not isinstance(key,IndexKey) or key.domain!=self.domain: raise ValueError('Domain mismatch')
        return self.rows[key.id]
