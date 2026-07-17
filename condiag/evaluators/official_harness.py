"""ConDiag v4 OfficialHarnessGateway — thin wrapper around swebench.harness.

Gateway is thin: canonical SWE-bench row → make_test_spec → run_instance → report.
No custom testing logic, no self-written Docker exec, no fallback evaluator.

Prerequisites (done once per machine):
  1. sweb.eval.* images pulled (swebench/ namespace)
  2. sweb.env.* images tagged (from eval images, same hash)
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import docker

logger = logging.getLogger("condiag.harness")

# All our images are stored with swebench/ prefix (mini-SWE-agent convention)
SWEBENCH_NAMESPACE = "swebench"


@dataclass
class EvalResult:
    status: str = "UNKNOWN"          # RESOLVED | UNRESOLVED | ERROR | TIMEOUT
    report: dict = field(default_factory=dict)
    test_log_path: str = ""
    report_log_path: str = ""
    duration_seconds: float = 0.0
    error_info: str = ""
    container_id: str = ""
    mode: str = "official"


@dataclass
class FailureWitness:
    failed_tests: list[str] = field(default_factory=list)
    error_message: str = ""
    stack_frames: list[dict] = field(default_factory=list)
    raw_log_preview: str = ""
    sanitized: bool = False

    def to_dict(self) -> dict:
        return {
            "failed_tests": self.failed_tests,
            "error_message": self.error_message,
            "stack_frames": self.stack_frames,
            "raw_log_preview": self.raw_log_preview[:2000],
            "sanitized": self.sanitized,
        }


class OfficialHarnessGateway:
    def __init__(self, *, run_id: str = "condiag", rm_image: bool = False,
                 force_rebuild: bool = False, timeout: int | None = 600,
                 model_name: str = "condiag-agent"):
        self.run_id = run_id
        self.rm_image = rm_image
        self.force_rebuild = force_rebuild
        self.timeout = timeout
        self.model_name = model_name
        self._docker_client: docker.DockerClient | None = None

    @property
    def docker_client(self) -> docker.DockerClient:
        if self._docker_client is None:
            self._docker_client = docker.from_env()
        return self._docker_client

    def evaluate(self, instance_spec, model_patch: str, *, run_id: str | None = None) -> EvalResult:
        if run_id is not None:
            self.run_id = run_id
        """Run official SWE-bench evaluation on a patch.

        Thin layer:
          1. Build canonical SWE-bench instance dict
          2. make_test_spec(namespace="swebench")
          3. run_instance(force_rebuild=False) → reuses existing images
          4. Return report
        """
        t0 = time.time()
        iid = getattr(instance_spec, "instance_id",
                      instance_spec.get("instance_id", "?") if isinstance(instance_spec, dict) else "?")
        logger.info("OfficialHarness evaluate %s (run=%s)", iid, self.run_id)

        pred = {"instance_id": iid, "model_name_or_path": self.model_name, "model_patch": model_patch or ""}
        swe_instance = self._build_swebench_instance(instance_spec)

        from swebench.harness.test_spec.test_spec import make_test_spec
        try:
            test_spec = make_test_spec(swe_instance, namespace=SWEBENCH_NAMESPACE)
        except Exception as e:
            return EvalResult(status="ERROR", error_info=f"make_test_spec: {e}", duration_seconds=time.time() - t0)

        # Verify images exist locally (reuse check)
        client = self.docker_client
        for key, label in [(test_spec.instance_image_key, "eval"), (test_spec.env_image_key, "env")]:
            try:
                client.images.get(key)
            except docker.errors.ImageNotFound:
                return EvalResult(status="ERROR", error_info=f"{label} image not found: {key}",
                                  duration_seconds=time.time() - t0)

        from swebench.harness.run_evaluation import run_instance
        try:
            result = run_instance(
                test_spec=test_spec, pred=pred,
                rm_image=self.rm_image, force_rebuild=self.force_rebuild,
                client=client, run_id=self.run_id, timeout=self.timeout,
            )
        except Exception as e:
            return EvalResult(status="ERROR", error_info=str(e), duration_seconds=time.time() - t0)

        # Locate log files
        from swebench.harness.constants import RUN_EVALUATION_LOG_DIR, LOG_REPORT, LOG_TEST_OUTPUT
        ms = self.model_name.replace("/", "__")
        ld = RUN_EVALUATION_LOG_DIR / self.run_id / ms / iid
        tl = str(ld / LOG_TEST_OUTPUT) if (ld / LOG_TEST_OUTPUT).exists() else ""
        rl = str(ld / LOG_REPORT) if (ld / LOG_REPORT).exists() else ""

        resolved = result.get("resolved", False)
        return EvalResult(
            status="RESOLVED" if resolved else "UNRESOLVED", report=result,
            test_log_path=tl, report_log_path=rl,
            duration_seconds=time.time() - t0, mode="official",
        )

    def _build_swebench_instance(self, spec) -> dict:
        """Build SWE-bench instance dict from InstanceSpec.
        Uses canonical SWE-bench row (attached as _swebench_row) when available.
        """
        def get(key, default=""):
            if hasattr(spec, key): return getattr(spec, key) or default
            return spec.get(key, default) if isinstance(spec, dict) else default

        iid = get("instance_id")
        sb_row = getattr(spec, "_swebench_row", None) or (
            spec.get("_swebench_row") if isinstance(spec, dict) else None
        )

        if sb_row:
            logger.info("Canonical SWE-bench row for %s", iid)
            return {
                "instance_id": iid, "repo": sb_row.get("repo", get("repo")),
                "base_commit": sb_row.get("base_commit", get("base_commit")),
                "test_patch": sb_row.get("test_patch", get("test_patch")),
                "problem_statement": sb_row.get("problem_statement", get("problem_statement")),
                "version": sb_row.get("version", get("version")),
                "FAIL_TO_PASS": sb_row.get("FAIL_TO_PASS", "[]"),
                "PASS_TO_PASS": sb_row.get("PASS_TO_PASS", "[]"),
                "patch": get("gold_patch", ""),
                "hints_text": sb_row.get("hints_text", ""),
                "created_at": sb_row.get("created_at", ""),
                "environment_setup_commit": sb_row.get("environment_setup_commit", ""),
            }

        logger.info("No canonical SWE-bench row for %s (using ContextBench data)", iid)
        f2p = get("fail_to_pass", []); p2p = get("pass_to_pass", [])
        if isinstance(f2p, str): f2p = json.loads(f2p)
        if isinstance(p2p, str): p2p = json.loads(p2p)
        return {
            "instance_id": iid, "repo": get("repo"), "base_commit": get("base_commit"),
            "test_patch": get("test_patch"), "problem_statement": get("problem_statement"),
            "version": get("version"),
            "FAIL_TO_PASS": json.dumps(f2p), "PASS_TO_PASS": json.dumps(p2p),
            "patch": get("gold_patch", ""), "hints_text": "", "created_at": "",
            "environment_setup_commit": get("environment_setup_commit"),
        }

    def extract_witness(self, eval_result: EvalResult) -> FailureWitness:
        fw = FailureWitness()
        if eval_result.status != "UNRESOLVED":
            return fw
        lp = eval_result.test_log_path
        if not lp or not Path(lp).exists():
            fw.error_message = "No test log"
            return fw
        raw = Path(lp).read_text(encoding="utf-8", errors="replace")
        fw.raw_log_preview = raw[:2000]
        failed = []
        for line in raw.split("\n"):
            s = line.strip()
            if s.startswith("FAILED "):
                parts = s.split("::")
                if len(parts) >= 2: failed.append(parts[-1].split()[0])
            m = re.search(r"FAIL:\s+(\w+)", s)
            if m: failed.append(m.group(1))
        fw.failed_tests = list(set(failed))
        em = re.search(r"(AssertionError|TypeError|ValueError|AttributeError)[^:\n]*:\s*([^\n]+)", raw)
        if em: fw.error_message = em.group(0).strip()
        frames = []
        for m in re.finditer(r'File\s+"([^"]+)",\s*line\s+(\d+)(?:,\s*in\s+(\w+))?', raw):
            fp = m.group(1)
            if fp.startswith("/testbed/"): fp = fp[len("/testbed/"):]
            frames.append({"file": fp, "line": int(m.group(2)), "function": m.group(3) or ""})
        fw.stack_frames = frames[:15]
        fw.sanitized = True
        return fw
