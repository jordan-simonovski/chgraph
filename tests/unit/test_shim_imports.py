import subprocess
import sys


def test_shim_never_imports_chdb():
    # Run in a clean interpreter: importing the shim must not pull in chdb (INV-1: only
    # the daemon touches chdb; the shim must stay cheap and lock-free).
    code = "import sys; import chgraph.shim; sys.exit(1 if 'chdb' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
