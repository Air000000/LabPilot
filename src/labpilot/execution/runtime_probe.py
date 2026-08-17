import json
import subprocess

from labpilot.execution.runner_policy import RunnerPolicy
from labpilot.execution.runner_protocol import InfoInspectionRequest

FIXED_PROBE_SOURCE = """import ctypes
import json
import sys
from importlib import metadata

names = json.loads(sys.argv[1])
frameworks = []
issues = []
for name in names:
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        installed = None
    frameworks.append({"distribution_name": name, "installed_version": installed})

runtime_version = None
device_count = None
try:
    cudart = ctypes.CDLL("libcudart.so")
    runtime = ctypes.c_int()
    count = ctypes.c_int()
    if cudart.cudaRuntimeGetVersion(ctypes.byref(runtime)) == 0:
        runtime_version = runtime.value
    else:
        issues.append("CUDA_RUNTIME_QUERY_FAILED")
    if cudart.cudaGetDeviceCount(ctypes.byref(count)) == 0:
        device_count = count.value
    else:
        issues.append("CUDA_DEVICE_QUERY_FAILED")
except (OSError, AttributeError):
    issues.append("CUDA_RUNTIME_UNAVAILABLE")

print(json.dumps({
    "python_version": sys.version,
    "framework_versions": frameworks,
    "cuda_runtime_version": runtime_version,
    "cuda_device_count": device_count,
    "probe_issue_codes": sorted(set(issues)),
}, separators=(",", ":"), sort_keys=True))
"""


def build_probe_argv(policy: RunnerPolicy, inspection: InfoInspectionRequest) -> tuple[str, ...]:
    profile = policy.runtime_profile(inspection.python_profile)
    return (
        "/usr/sbin/runuser",
        "--preserve-environment",
        "--user",
        policy.execution_user,
        "--",
        profile.runtime_path,
        "-c",
        FIXED_PROBE_SOURCE,
        json.dumps(list(inspection.framework_distribution_names), separators=(",", ":")),
    )


def run_probe(policy: RunnerPolicy, inspection: InfoInspectionRequest) -> dict[str, object]:
    result = subprocess.run(
        build_probe_argv(policy, inspection),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
        env=dict(policy.runtime_profile(inspection.python_profile).environment),
    )
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 64 * 1024:
        return {"probe_issue_codes": ["PROBE_FAILED"]}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"probe_issue_codes": ["PROBE_OUTPUT_INVALID"]}
    return value if type(value) is dict else {"probe_issue_codes": ["PROBE_OUTPUT_INVALID"]}
