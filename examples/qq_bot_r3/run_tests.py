"""Run integration and r3-query tests using temporary, synthetic databases."""
import os
import pathlib
import subprocess
import sys
import tempfile
from make_fixture import build_fixture

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    with tempfile.TemporaryDirectory() as temp:
        candidate = build_fixture(pathlib.Path(temp) / "unified-r3")
        env = dict(os.environ, UNIFIED_CANDIDATE_DIR=str(candidate), PYTHONDONTWRITEBYTECODE="1")
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + str(ROOT / "cloud") + os.pathsep + str(ROOT / "unified_r3")
        suites = [
            ["-m", "unittest", "cloud.test_unified_private", "cloud.test_unified_shadow", "cloud.test_trial_plugin_group", "cloud.test_store", "cloud.test_napcat_guard"],
            ["-m", "unittest", "discover", "-s", "unified_r3/tests", "-p", "test_query.py"],
        ]
        for args in suites:
            result = subprocess.run([sys.executable, "-B", *args], cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
