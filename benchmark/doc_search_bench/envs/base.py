from __future__ import annotations

from pathlib import Path

from ..observability import BenchmarkRuntimeLogger
from ..types import CaseRunResult, RunConfig, TaskSuite


class BaseBenchmarkEnv:
    def __init__(self, *, config: RunConfig, benchmark_root: Path, run_id: str) -> None:
        self.config = config
        self.benchmark_root = benchmark_root
        self.run_id = run_id
        self.runtime_logger = BenchmarkRuntimeLogger(benchmark_root=benchmark_root, run_id=run_id)
        self.run_root = self.runtime_logger.run_dir
        self.raw_root = self.run_root / "raw"
        self.raw_root.mkdir(parents=True, exist_ok=True)

    def effective_repeat_count(self, task) -> int:
        repeat_count = max(1, int(task.case_repeat_count))
        if self.config.max_attempts_per_case is None:
            return repeat_count
        return min(repeat_count, max(1, int(self.config.max_attempts_per_case)))

    def run_suites(self, suites: list[TaskSuite]) -> list[CaseRunResult]:
        results: list[CaseRunResult] = []
        for suite in suites:
            suite_attempt_count = sum(self.effective_repeat_count(task) for task in suite.cases)
            self.runtime_logger.emit(
                "套件开始",
                context=[("run_id", self.run_id), ("split", suite.split), ("suite", suite.suite_id)],
                result=[("layer", suite.layer), ("用例数", len(suite.cases)), ("尝试数", suite_attempt_count)],
                payload={"suite_id": suite.suite_id},
            )
            for task in suite.cases:
                repeat_count = self.effective_repeat_count(task)
                self.runtime_logger.emit(
                    "用例开始",
                    context=[("suite", suite.suite_id), ("case", task.case_id)],
                    result=[
                        ("track", task.benchmark_track),
                        ("交互模式", task.interaction_mode),
                        ("重跑次数", repeat_count),
                    ],
                    payload={"case_id": task.case_id},
                )
                for attempt_index in range(1, repeat_count + 1):
                    self.runtime_logger.emit(
                        "尝试开始",
                        context=[("case", task.case_id), ("attempt", attempt_index)],
                        result=[
                            ("track", task.benchmark_track),
                            ("用户模拟", task.user_simulation_config.driver),
                            ("最大轮次", task.max_turns),
                        ],
                        payload={"attempt_index": attempt_index},
                    )
                    try:
                        results.append(self.run_case(task, attempt_index=attempt_index))
                    except Exception as exc:
                        self.runtime_logger.emit(
                            "运行异常",
                            level="错误",
                            context=[
                                ("suite", suite.suite_id),
                                ("case", task.case_id),
                                ("attempt", attempt_index),
                            ],
                            result=[("异常类型", type(exc).__name__)],
                            detail=str(exc),
                            payload={"error": str(exc)},
                        )
                        raise
        return results

    def run_case(self, task, *, attempt_index: int = 1) -> CaseRunResult:  # pragma: no cover - abstract by convention
        raise NotImplementedError
