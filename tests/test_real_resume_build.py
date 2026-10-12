from pathlib import Path
import base64
import os
import shutil
import subprocess
import tempfile
import unittest

from cloud.preparation import MASTER, verify_resume


@unittest.skipUnless(shutil.which('pdflatex'), 'LaTeX unavailable locally; required CI compiler runs this test')
class RealResumeBuildTests(unittest.TestCase):
    def test_current_master_compiles_and_passes_strict_pdf_qa(self):
        with tempfile.TemporaryDirectory(prefix='apply-master-qa-') as folder:
            root = Path(folder)
            shutil.copyfile(MASTER, root / 'resume.tex')
            shutil.copyfile(MASTER.parent / 'v8preamble.tex', root / 'v8preamble.tex')
            for _ in range(2):
                result = subprocess.run(['pdflatex', '-interaction=nonstopmode', '-halt-on-error', '-no-shell-escape', 'resume.tex'], cwd=root, capture_output=True, timeout=120)
                self.assertEqual(result.returncode, 0, 'Actual master PDF compilation failed')
            qa = verify_resume(root / 'resume.tex', root / 'resume.pdf')
            if not qa['verified'] and os.environ.get('MIGRATION_BACKUP_KEY'):
                from scripts.encrypted_backup import create_backup
                key_path = root / 'diagnostic-key.bin'
                key_path.write_bytes(base64.b64decode(os.environ['MIGRATION_BACKUP_KEY'], validate=True))
                key_path.chmod(0o600)
                files = {path.name: path for path in root.iterdir() if path.is_file() and path != key_path}
                create_backup(files, Path('run-output/master-qa.aesgcm'), key_path=key_path, private_destination_verified=True)
            self.assertTrue(qa['verified'], qa)


if __name__ == '__main__':
    unittest.main()
