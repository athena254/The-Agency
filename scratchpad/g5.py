import re
s=open('src/agency/capability/resolver.py').read().splitlines()
for i,l in enumerate(s,1):
    if 'unsupported capability type' in l or 'def _adapter' in l or '_SUPPORTED_TYPES' in l:
        print(i, repr(l))
EOF