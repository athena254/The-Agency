import pathlib
files = ['src/agency/agents/personal/agent.py','src/agency/agents/work/agent.py','src/agency/agents/business/agent.py','src/agency/agents/security/agent.py','src/agency/autonomy/blacksmith.py']
for f in files:
    p = pathlib.Path(f)
    t = p.read_text()
    t = t.replace('\nEOF\n', '\n').replace('\nEOF', '\n')
    p.write_text(t)
print('done')