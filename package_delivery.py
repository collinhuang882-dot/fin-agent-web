"""Package source only. Never includes user environment files, caches or credentials."""
from pathlib import Path
import hashlib
import zipfile

root = Path(__file__).resolve().parent
output = root.parent / 'deliverables'
output.mkdir(exist_ok=True)
archive = output / 'Fin-Agent-Web-1.0.0-source.zip'
excluded = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.openai'}
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in excluded or part.startswith('.venv') for part in relative.parts):
            continue
        if path.name.startswith('.env'):
            continue
        if path.suffix in {'.pyc', '.zip', '.log'}:
            continue
        zipped.write(path, Path('fin-agent-web') / relative)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
print(archive)
print(f'SHA256 {digest}')
