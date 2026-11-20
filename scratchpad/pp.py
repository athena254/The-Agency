import os
f='src/agency/factory/prompts.py'
s=open(f).read()
print('===',f)
for i,l in enumerate(s.splitlines(),1):
    if 'def ' in l or 'class ' in l or 'publish' in l or 'authoriz' in l or 'approver' in l:
        print(i,l)
EOF