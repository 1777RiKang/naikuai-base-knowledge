"""Build a public r3 distribution without committing generated databases."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


def build(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("public_r3_builder", PUBLIC / "scripts/build_public.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = output / "naikuai-kb-public.sqlite"
    module.build_public_kb(db_path=str(db), pub_root=str(PUBLIC), timestamp="2026-10-24T06:15:00+00:00")
    for name in ("README.md", "LICENSE.md", "source-manifest.json"):
        shutil.copyfile(PUBLIC / name, output / name)
    manifest = {
        "schema_version": "unified-r3-public",
        "kb_version": "8.6.26.10-public",
        "release_version": (ROOT / 'VERSION').read_text(encoding='utf-8').strip(),
        "repository": "1777RiKang/naikuai-base-knowledge",
        "files": [{"path": name, "size_bytes": (output / name).stat().st_size,
                   "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
                  for name in (db.name, "README.md", "LICENSE.md", "source-manifest.json")],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Public r3 distribution built; database integrity is covered by build/verify_public_r3.py.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist/public-r3")
    build(parser.parse_args().out)
