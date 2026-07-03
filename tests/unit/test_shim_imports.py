import subprocess
import sys


def test_shim_never_imports_chdb():
    # Run in a clean interpreter: importing the shim must not pull in chdb (INV-1: only
    # the daemon touches chdb; the shim must stay cheap and lock-free).
    code = "import sys; import chgraph.shim; sys.exit(1 if 'chdb' in sys.modules else 0)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    # returncode == 0 is the real guarantee (chdb absent from sys.modules); anything
    # else could be a genuine chdb-import regression OR an unrelated crash on import —
    # surface stdout/stderr so the two are distinguishable instead of a bare 1 != 0.
    assert result.returncode == 0, (
        f"expected returncode 0 (chdb not imported); got {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
