content = open('scheduler.py', encoding='utf-8').read()

# Check line endings
crlf_count = content.count('\r\n')
lf_count = content.count('\n') - crlf_count
print(f'CRLF: {crlf_count}, LF-only: {lf_count}')

# Also check what's in the _process_one area
idx = content.find('async def _process_one')
print(repr(content[idx:idx+100]))
print()

# Try with \r\n
old_with_crlf = content[idx:idx+600]
print('Full match area:')
print(repr(old_with_crlf))
