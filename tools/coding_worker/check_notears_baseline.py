"""Record the frozen evaluator's expected failures on unmodified NOTEARS source."""

import argparse
import json
import os
from pathlib import Path

from environment import CodingEnvironment, bounded

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
os.environ["DOCKER_HOST"] = "unix:///run/labpilot-docker.sock"
os.umask(0o077)
args.output.mkdir(mode=0o700, parents=True)
b = json.loads((args.baseline / "baseline.json").read_text())
files = {
    e["path"]: (args.baseline / "source" / e["path"]).read_bytes()
    for e in b["entries"]
    if not e.get("deleted")
}
modes = {e["path"]: int(e["git_mode"], 8) & 0o777 for e in b["entries"] if not e.get("deleted")}
results = {}
for name, tag, selector, expected in [
    ("cpu", "labpilot-coding-cpu-acceptance:s0", "not explicit_plot", "7 failed"),
    ("plot", "labpilot-coding-plot-acceptance:s0", "explicit_plot", "1 failed"),
]:
    code, image = bounded(
        ["/usr/bin/docker", "image", "inspect", tag, "--format", "{{.Id}}"], timeout=10
    )
    assert code == 0
    e = CodingEnvironment(image.decode().strip())
    try:
        e.load(files, modes)
        result = e.execute(
            {
                "command": "python -m pytest -q -p no:cacheprovider -c /dev/null "
                "/opt/labpilot-acceptance/test_notears_cpu_001.py -k '" + selector + "'"
            }
        )
        assert result["returncode"] == 1 and expected in result["output"], result
        (args.output / (name + ".log")).write_text(result["output"])
        results[name] = {
            "image": image.decode().strip(),
            "exit": result["returncode"],
            "expected_baseline_failure": expected,
        }
    finally:
        e.cleanup()
(args.output / "result.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results))
