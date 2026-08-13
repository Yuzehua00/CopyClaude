import io
src = io.open(r'src/copy_claude/tui/app.py', encoding='utf-8').read().splitlines()
out = []
for i, line in enumerate(src, 1):
    if '_break_llm' in line or 'def _update_header' in line or 'def _handle_event' in line:
        out.append('%5d: %s' % (i, line))
io.open('_bl.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('ok')
