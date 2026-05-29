import glob, hashlib, os, re

def file_hash(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]

def bust(html_path):
    html_dir = os.path.dirname(html_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    def resolve(path):
        clean = re.sub(r'\?.*', '', path)
        if clean.startswith('/'):
            resolved = clean.lstrip('/')
        else:
            resolved = os.path.normpath(os.path.join(html_dir, clean))
        return clean, resolved

    def replace_attr(m):
        attr, path = m.group(1), m.group(2)
        clean, resolved = resolve(path)
        try:
            h = file_hash(resolved)
        except FileNotFoundError:
            return m.group(0)
        return f'{attr}"{clean}?v={h}"'

    def replace_js(m):
        quote, path = m.group(1), m.group(2)
        clean, resolved = resolve(path)
        try:
            h = file_hash(resolved)
        except FileNotFoundError:
            return m.group(0)
        return f'{quote}{clean}?v={h}{quote}'

    updated = re.sub(r'((?:href|src)=)"([^"]+\.(?:css|js|pdf))(?:\?[^"]*)?"', replace_attr, content)
    updated = re.sub(r'([\'"])(/[^\'"\s]+\.pdf)(?:\?[^\'"]*)?\1', replace_js, updated)
    if updated != content:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(updated)
        print(f'  {html_path}')

print('Cache-busted:')
for f in glob.glob('**/*.html', recursive=True):
    if 'pdfjs' not in f:
        bust(f)
