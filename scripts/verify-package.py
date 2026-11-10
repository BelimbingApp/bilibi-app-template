#!/usr/bin/env python3
"""Prove packaging determinism by repackaging the same checked program."""
from pathlib import Path
import subprocess
import sys
import tempfile

import recipe

from publish import verify_files

tools = Path(__file__).resolve().parent.parent
root = recipe.ROOT
before = verify_files(root / 'dist')
# Keep proof under build/, never in the install archive or Git index.
with tempfile.TemporaryDirectory(prefix='package-proof-', dir=recipe.BUILD) as temp:
    saved = Path(temp) / 'dist'
    (root / 'dist').rename(saved)
    try:
        subprocess.run([sys.executable, str(tools / 'scripts/package.py'), *sys.argv[1:]], check=True)
        after = verify_files(root / 'dist')
        if before != after:
            raise ValueError('Packaging the same checked bytes produced different asset digests')
    except BaseException:
        if not (root / 'dist').exists():
            saved.rename(root / 'dist')
        raise
print('Repeated packaging produced identical SHA-256 values for all assets.')
print('This does not claim two clean compiler builds produce identical programs.')
