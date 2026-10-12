from pathlib import Path
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
            self.assertTrue(qa['verified'], qa)


if __name__ == '__main__':
    unittest.main()
