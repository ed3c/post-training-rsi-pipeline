from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Protocol

from ..domain import Checkpoint


class ServingAdapter(Protocol):
    def deploy(self, checkpoint: Checkpoint) -> str: ...
    def undeploy(self, endpoint: str) -> None: ...


@dataclass(slots=True)
class LocalArtifactServingAdapter:
    def deploy(self, checkpoint: Checkpoint) -> str:
        return checkpoint.artifact_path.resolve().as_uri()

    def undeploy(self, endpoint: str) -> None:
        del endpoint


@dataclass(slots=True)
class CommandServingAdapter:
    """Provider-neutral hook for vLLM, SGLang, or a managed serving plane."""

    deploy_command: str
    undeploy_command: str | None = None
    timeout_seconds: int = 20 * 60

    def deploy(self, checkpoint: Checkpoint) -> str:
        env = os.environ.copy()
        env.update(
            {
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(checkpoint.artifact_path.resolve()),
            }
        )
        process = subprocess.run(
            shlex.split(self.deploy_command),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if process.returncode != 0:
            raise RuntimeError(f"serving deploy failed: {process.stderr.strip()}")
        endpoint = process.stdout.strip().splitlines()[-1] if process.stdout.strip() else ""
        if not endpoint:
            raise RuntimeError("serving deploy command did not print an endpoint")
        return endpoint

    def undeploy(self, endpoint: str) -> None:
        if not self.undeploy_command:
            return
        env = os.environ.copy()
        env["RSI_SERVING_ENDPOINT"] = endpoint
        process = subprocess.run(
            shlex.split(self.undeploy_command),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if process.returncode != 0:
            raise RuntimeError(f"serving undeploy failed: {process.stderr.strip()}")
