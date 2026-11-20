import re
p='tests/test_skill_registry.py'
s=open(p).read()
for i,line in enumerate(s.splitlines(),1):
    if 'publish' in line or 'list_events' in line or 'transition' in line:
        print(i, line)
EOF