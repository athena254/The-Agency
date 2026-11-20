s=open('src/agency/factory/requested_agent.py').read().splitlines()
for i,l in enumerate(s,1):
    if l.strip()=='if gaps:':
        print(i)
EOF