s=open('src/agency/kernel/audit.py').read().splitlines()
for i,l in enumerate(s,1):
    if 'def append' in l or 'class AuditLog' in l or 'def __init__' in l:
        print(i,l)
EOF