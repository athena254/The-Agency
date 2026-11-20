s=open('src/agency/factory/requested_agent.py').read().splitlines()
for i,l in enumerate(s,1):
    if 'transition(' in l or '.publish(' in l:
        print(i,l)
EOF