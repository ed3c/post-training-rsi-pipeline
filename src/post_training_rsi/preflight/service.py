from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..audit.contracts import AuditCheck, AuditStatus, overall_status
from ..config import PipelineConfig
from ..control_plane.validation import canonical_json
from ..lineage import ArtifactStore
from .contracts import (
    DestinationAuthorization,
    PreflightContractError,
    PreflightTarget,
    ProviderPreflightReport,
)

# The in-process member of each backend vocabulary PipelineConfig accepts
# (mock|openai_compatible, mock|command, deterministic|command, local|command).
# Anything not listed here is treated as external, so a backend added later
# fails closed into the stricter branch instead of inheriting mock admission.
LOCAL_TEACHER_BACKENDS = frozenset({"mock"})
LOCAL_TRAINING_BACKENDS = frozenset({"mock"})
LOCAL_EVALUATION_BACKENDS = frozenset({"deterministic"})
LOCAL_SERVING_BACKENDS = frozenset({"local"})

# Approvals each target must have configured before it may be admitted.
REQUIRED_APPROVALS: dict[PreflightTarget, tuple[str, ...]] = {
    PreflightTarget.REFERENCE: (),
    PreflightTarget.TEACHER: ("dataset_review_required",),
    PreflightTarget.TRAINING: (
        "dataset_review_required",
        "checkpoint_review_required",
    ),
    PreflightTarget.END_TO_END_RSI: (
        "dataset_review_required",
        "checkpoint_review_required",
    ),
    PreflightTarget.END_TO_END_COEVOLUTION: (
        "dataset_review_required",
        "checkpoint_review_required",
        "harness_review_required",
    ),
}


class ProviderPreflight:
    """Admission checks for a hybrid-cloud run, before anything leaves the process.

    Every check reads configuration, environment variable *names*, and an optional
    authorization receipt. Nothing here opens a socket, starts a subprocess, calls
    a GPU API, or touches a serving endpoint — the point is to fail before a paid
    or irreversible resource exists.
    """

    def __init__(
        self,
        config: PipelineConfig,
        *,
        workspace: str | Path,
        clock: Callable[[], str] | None = None,
        environment: Mapping[str, str] | None = None,
        resolve_executable: Callable[[str], str | None] | None = None,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.clock = clock or _utc_now
        self.environment = dict(os.environ if environment is None else environment)
        self.resolve_executable = resolve_executable or shutil.which
        self.config_sha256 = hashlib.sha256(
            canonical_json(config.to_dict()).encode("utf-8")
        ).hexdigest()

    def run(
        self,
        *,
        target: PreflightTarget | str,
        authorization: Mapping[str, Any] | None = None,
        strict: bool = False,
        write_report: bool = True,
    ) -> ProviderPreflightReport:
        target = PreflightTarget(target)
        checks: list[AuditCheck] = []
        inventory = self._inventory(target)

        self._check_config(checks)
        self._check_adapter_inventory(checks, target, inventory)
        self._check_secret_names(checks, target)
        self._check_teacher_url(checks, target)
        self._check_commands(checks, target)
        self._check_serving_commands(checks, target)
        self._check_artifact_path(checks, target)
        self._check_budgets(checks)
        self._check_approvals(checks, target)
        self._check_benchmarks(checks, target)
        self._check_authorization(checks, target, authorization)

        status = overall_status(checks, strict=strict)
        report = ProviderPreflightReport(
            generated_at=self.clock(),
            target=target,
            strict=strict,
            status=status,
            config_sha256=self.config_sha256,
            checks=tuple(checks),
            inventory=inventory,
            report_path=None,
        )
        if not write_report:
            return report
        path = self.workspace / "reports" / "provider-preflight.json"
        report = report.with_report_path(path.as_posix())
        ArtifactStore(self.workspace).write_report(path.name, report.to_dict())
        return report

    # -- inventory -----------------------------------------------------------

    def _inventory(self, target: PreflightTarget) -> dict[str, Any]:
        adapters = self.config.adapters
        return {
            "target": target.value,
            "teacher_backend": adapters.teacher.backend,
            "teacher_external": _is_external(
                adapters.teacher.backend, LOCAL_TEACHER_BACKENDS
            ),
            "teacher_origin": _origin(adapters.teacher.base_url),
            "training_backend": adapters.training.backend,
            "training_external": _is_external(
                adapters.training.backend, LOCAL_TRAINING_BACKENDS
            ),
            "evaluation_backend": adapters.evaluation.backend,
            "evaluation_external": _is_external(
                adapters.evaluation.backend, LOCAL_EVALUATION_BACKENDS
            ),
            "serving_backend": adapters.serving.backend,
            "serving_external": _is_external(
                adapters.serving.backend, LOCAL_SERVING_BACKENDS
            ),
            "secret_names": [adapters.teacher.api_key_env],
            "mutations_allowed": ["reports/provider-preflight.json"],
        }

    # -- checks --------------------------------------------------------------

    def _check_config(self, checks: list[AuditCheck]) -> None:
        try:
            self.config.validate()
        except Exception as exc:
            checks.append(
                _failure(
                    "preflight-config",
                    "PipelineConfig",
                    exc,
                    "Fix the configuration before requesting admission.",
                )
            )
            return
        checks.append(
            _check(
                "preflight-config",
                AuditStatus.PASS,
                "PipelineConfig",
                "Configuration revalidated and hashed.",
                config_sha256=self.config_sha256,
            )
        )

    def _check_adapter_inventory(
        self,
        checks: list[AuditCheck],
        target: PreflightTarget,
        inventory: Mapping[str, Any],
    ) -> None:
        external = sorted(
            name
            for name in ("teacher", "training", "evaluation", "serving")
            if inventory[f"{name}_external"]
        )
        if target is PreflightTarget.REFERENCE and external:
            checks.append(
                _check(
                    "preflight-adapter-inventory",
                    AuditStatus.FAIL,
                    "adapters",
                    "Reference target must not select an external adapter.",
                    external_adapters=external,
                    recovery_hint=(
                        "Select a production target explicitly, or restore the "
                        "mock/local backends for the reference path."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-adapter-inventory",
                AuditStatus.PASS,
                "adapters",
                "Adapter backends classified without construction or execution.",
                external_adapters=external,
            )
        )

    def _check_secret_names(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        teacher = self.config.adapters.teacher
        if not _is_external(teacher.backend, LOCAL_TEACHER_BACKENDS):
            checks.append(
                _check(
                    "preflight-secret-names",
                    AuditStatus.PASS,
                    teacher.api_key_env,
                    "Local Teacher backend needs no provider credential.",
                )
            )
            return
        present = teacher.api_key_env in self.environment
        value = self.environment.get(teacher.api_key_env, "")
        if not present or not value.strip():
            checks.append(
                _check(
                    "preflight-secret-names",
                    AuditStatus.FAIL,
                    teacher.api_key_env,
                    "Required Teacher credential environment variable is absent or empty.",
                    present=present,
                    recovery_hint=(
                        "Export the credential in the execution environment. Never "
                        "record its value in configuration or evidence."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-secret-names",
                AuditStatus.PASS,
                teacher.api_key_env,
                "Required credential name is present; its value was not read into evidence.",
            )
        )

    def _check_teacher_url(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        teacher = self.config.adapters.teacher
        external = _is_external(teacher.backend, LOCAL_TEACHER_BACKENDS)
        if not external:
            if teacher.base_url:
                checks.append(
                    _check(
                        "preflight-teacher-url",
                        AuditStatus.WARN,
                        "adapters.teacher.base_url",
                        "Local Teacher backend declares an unused base URL.",
                        recovery_hint=(
                            "Remove the URL, or select the external backend it "
                            "belongs to, so the intended destination is unambiguous."
                        ),
                    )
                )
            else:
                checks.append(
                    _check(
                        "preflight-teacher-url",
                        AuditStatus.PASS,
                        "adapters.teacher.base_url",
                        "Local Teacher backend declares no external destination.",
                    )
                )
            return

        if not teacher.base_url:
            checks.append(
                _check(
                    "preflight-teacher-url",
                    AuditStatus.FAIL,
                    "adapters.teacher.base_url",
                    "External Teacher backend requires an explicit base URL.",
                    recovery_hint="Set the exact destination origin in configuration.",
                )
            )
            return

        problems = _url_problems(teacher.base_url)
        if problems:
            checks.append(
                _check(
                    "preflight-teacher-url",
                    AuditStatus.FAIL,
                    _origin(teacher.base_url) or "adapters.teacher.base_url",
                    "External Teacher URL violates the transmission policy.",
                    problems=problems,
                    recovery_hint=(
                        "Use an https origin with no embedded credentials, query, "
                        "or fragment. Credentials belong in the environment."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-teacher-url",
                AuditStatus.PASS,
                _origin(teacher.base_url) or "",
                "External Teacher URL origin accepted.",
            )
        )

    def _check_commands(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        specs = (
            ("adapters.training.command", self.config.adapters.training.command),
            ("adapters.evaluation.command", self.config.adapters.evaluation.command),
            (
                "adapters.serving.deploy_command",
                self.config.adapters.serving.deploy_command,
            ),
            (
                "adapters.serving.undeploy_command",
                self.config.adapters.serving.undeploy_command,
            ),
        )
        unresolved: list[str] = []
        missing_scripts: list[str] = []
        for subject, command in specs:
            if not command:
                continue
            if self.resolve_executable(command[0]) is None:
                unresolved.append(f"{subject}: {command[0]}")
            for argument in command[1:]:
                if _looks_like_path(argument) and not Path(argument).exists():
                    missing_scripts.append(f"{subject}: {argument}")

        if unresolved:
            checks.append(
                _check(
                    "preflight-commands",
                    AuditStatus.FAIL,
                    "adapters.*.command",
                    "A configured command executable does not resolve.",
                    unresolved=sorted(unresolved),
                    recovery_hint=(
                        "Install the worker on the execution host or correct the "
                        "command. The executable was resolved, never invoked."
                    ),
                )
            )
            return
        if missing_scripts:
            checks.append(
                _check(
                    "preflight-commands",
                    AuditStatus.WARN,
                    "adapters.*.command",
                    "A path-like worker argument does not exist on this host.",
                    missing=sorted(missing_scripts),
                    recovery_hint=(
                        "Confirm the worker script ships to the execution host; "
                        "this host may legitimately differ from the runner."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-commands",
                AuditStatus.PASS,
                "adapters.*.command",
                "Configured command executables resolve without invocation.",
            )
        )

    def _check_serving_commands(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        # PipelineConfig.validate() owns this invariant: a command backend must
        # define both commands and a local backend may define neither. Preflight
        # records the resulting pairing as admission evidence rather than
        # re-deciding it, so there is one place to change if the rule moves.
        serving = self.config.adapters.serving
        checks.append(
            _check(
                "preflight-serving-commands",
                AuditStatus.PASS,
                "adapters.serving",
                "Serving teardown is configured for every configured deploy.",
                backend=serving.backend,
                deploy_configured=bool(serving.deploy_command),
                undeploy_configured=bool(serving.undeploy_command),
                enforced_by="PipelineConfig.validate",
            )
        )

    def _check_artifact_path(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        allowed = self.config.adapters.training.allow_external_artifact_path
        if allowed and target is not PreflightTarget.REFERENCE:
            checks.append(
                _check(
                    "preflight-artifact-path",
                    AuditStatus.FAIL,
                    "adapters.training.allow_external_artifact_path",
                    "Production targets may not accept artifacts outside the workspace.",
                    recovery_hint=(
                        "Disable the escape, or land an allowlisted storage contract "
                        "first. This slice defines no trusted external store."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-artifact-path",
                AuditStatus.PASS,
                "adapters.training.allow_external_artifact_path",
                "Artifact paths stay inside the workspace boundary.",
                allow_external_artifact_path=allowed,
            )
        )

    def _check_budgets(self, checks: list[AuditCheck]) -> None:
        adapters = self.config.adapters
        budget = self.config.budget
        controls: dict[str, float] = {
            "budget.total_limit_usd": budget.total_limit_usd,
            "budget.per_iteration_limit_usd": budget.per_iteration_limit_usd,
            "adapters.teacher.timeout_seconds": adapters.teacher.timeout_seconds,
            "adapters.teacher.max_attempts": adapters.teacher.max_attempts,
            "adapters.teacher.input_cost_per_million": (
                adapters.teacher.input_cost_per_million
            ),
            "adapters.teacher.output_cost_per_million": (
                adapters.teacher.output_cost_per_million
            ),
            "adapters.training.timeout_seconds": adapters.training.timeout_seconds,
            "adapters.training.max_attempts": adapters.training.max_attempts,
            "adapters.evaluation.timeout_seconds": adapters.evaluation.timeout_seconds,
            "adapters.serving.timeout_seconds": adapters.serving.timeout_seconds,
        }
        invalid = sorted(
            name
            for name, value in controls.items()
            if not math.isfinite(float(value)) or float(value) <= 0.0
            if not name.endswith("cost_per_million")
        )
        nonfinite_costs = sorted(
            name
            for name, value in controls.items()
            if name.endswith("cost_per_million")
            and (not math.isfinite(float(value)) or float(value) < 0.0)
        )
        broken = invalid + nonfinite_costs
        if broken:
            checks.append(
                _check(
                    "preflight-budgets",
                    AuditStatus.FAIL,
                    "budget",
                    "Retry, timeout, cost, or budget control is not a usable finite bound.",
                    invalid=broken,
                    recovery_hint=(
                        "An unbounded control cannot stop a paid run; set a finite "
                        "positive limit."
                    ),
                )
            )
            return
        # PipelineConfig.validate() already rejects a per-iteration limit above
        # the total, and preflight-config runs that first, so there is no
        # reachable case for this component to re-decide.
        checks.append(
            _check(
                "preflight-budgets",
                AuditStatus.PASS,
                "budget",
                "Retry, timeout, cost, and budget controls are finite and bounded.",
            )
        )

    def _check_approvals(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        required = REQUIRED_APPROVALS[target]
        missing = sorted(
            name for name in required if not getattr(self.config.approval, name)
        )
        if missing:
            checks.append(
                _check(
                    "preflight-approvals",
                    AuditStatus.FAIL,
                    "approval",
                    "Target requires human review gates that are not enabled.",
                    missing=missing,
                    recovery_hint=(
                        "Enable the review gates for this target. Approval gates "
                        "fail closed by contract."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-approvals",
                AuditStatus.PASS,
                "approval",
                "Required human review gates are enabled for this target.",
                required=list(required),
            )
        )

    def _check_benchmarks(
        self, checks: list[AuditCheck], target: PreflightTarget
    ) -> None:
        if not target.is_end_to_end:
            checks.append(
                _check(
                    "preflight-benchmarks",
                    AuditStatus.PASS,
                    "benchmark_texts",
                    "Target does not require benchmark decontamination inputs.",
                )
            )
            return
        if not self.config.benchmark_texts:
            checks.append(
                _check(
                    "preflight-benchmarks",
                    AuditStatus.FAIL,
                    "benchmark_texts",
                    "End-to-end targets require benchmark texts for decontamination.",
                    recovery_hint=(
                        "Without benchmark texts the contamination gate cannot "
                        "reject a leaked evaluation item."
                    ),
                )
            )
            return
        checks.append(
            _check(
                "preflight-benchmarks",
                AuditStatus.PASS,
                "benchmark_texts",
                "Benchmark decontamination inputs are present.",
                benchmark_text_count=len(self.config.benchmark_texts),
                benchmark_id=self.config.rsi.benchmark_id,
            )
        )

    def _check_authorization(
        self,
        checks: list[AuditCheck],
        target: PreflightTarget,
        authorization: Mapping[str, Any] | None,
    ) -> None:
        teacher = self.config.adapters.teacher
        external = _is_external(teacher.backend, LOCAL_TEACHER_BACKENDS)
        if not (target.leaves_process and external):
            checks.append(
                _check(
                    "preflight-authorization",
                    AuditStatus.PASS,
                    "destination-authorization",
                    "No Dataset content leaves the process, so no receipt is required.",
                )
            )
            return

        if authorization is None:
            checks.append(
                _check(
                    "preflight-authorization",
                    AuditStatus.FAIL,
                    "destination-authorization",
                    "External Teacher transmission requires a destination authorization receipt.",
                    recovery_hint=(
                        "Obtain a human-signed receipt bound to this exact config "
                        "hash and origin before any data leaves the process."
                    ),
                )
            )
            return

        try:
            receipt = DestinationAuthorization.from_mapping(dict(authorization))
        except (PreflightContractError, ValueError) as exc:
            checks.append(
                _failure(
                    "preflight-authorization",
                    "destination-authorization",
                    exc,
                    "Restore the exact signed receipt; do not hand-edit its fields.",
                )
            )
            return

        origin = _origin(teacher.base_url)
        problems: list[str] = []
        if receipt.config_sha256 != self.config_sha256:
            problems.append("config_sha256 does not match this configuration")
        if origin is None or receipt.origin != origin:
            problems.append("origin does not match the configured Teacher destination")
        if receipt.stage != "teacher":
            problems.append("stage does not authorize Teacher transmission")
        if receipt.is_expired_at(self.clock()):
            problems.append("authorization has expired")

        if problems:
            checks.append(
                _check(
                    "preflight-authorization",
                    AuditStatus.FAIL,
                    receipt.authorization_id,
                    "Destination authorization does not bind this transmission.",
                    problems=problems,
                    recovery_hint=(
                        "A receipt approves exact bytes and one destination. Obtain "
                        "a new review rather than reusing a stale decision."
                    ),
                )
            )
            return

        checks.append(
            _check(
                "preflight-authorization",
                AuditStatus.PASS,
                receipt.authorization_id,
                "Destination authorization binds this configuration, origin, and stage.",
                approved_by=receipt.approved_by,
                expires_at=receipt.expires_at,
                data_classes=list(receipt.data_classes),
            )
        )


def load_authorization_file(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreflightContractError("authorization file must contain a JSON object")
    return value


def _is_external(backend: str, local: frozenset[str]) -> bool:
    return backend.strip().casefold() not in local


def _origin(base_url: str | None) -> str | None:
    if not base_url:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _url_problems(base_url: str) -> list[str]:
    from urllib.parse import urlsplit

    problems: list[str] = []
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return ["URL is not parseable"]
    if parsed.scheme != "https":
        problems.append("scheme must be https")
    if not parsed.hostname:
        problems.append("host is missing")
    if parsed.username or parsed.password:
        problems.append("URL must not embed credentials")
    if parsed.query:
        problems.append("URL must not carry a query string")
    if parsed.fragment:
        problems.append("URL must not carry a fragment")
    return problems


def _looks_like_path(argument: str) -> bool:
    return "/" in argument or argument.endswith((".py", ".sh"))


def _check(
    check_id: str,
    status: AuditStatus,
    subject: str,
    message: str,
    **details: Any,
) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        status=status,
        subject=subject,
        message=message,
        details=details,
    )


def _failure(
    check_id: str,
    subject: str,
    error: Exception,
    recovery_hint: str,
) -> AuditCheck:
    return _check(
        check_id,
        AuditStatus.FAIL,
        subject,
        "Preflight verification failed.",
        error=str(error),
        error_type=type(error).__name__,
        recovery_hint=recovery_hint,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__: Sequence[str] = ("ProviderPreflight", "load_authorization_file")
