"""Admin APIs for DocSearch benchmark datasets, runs, and reports."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.benchmark.doc_search import (
    DocSearchBenchmarkStore,
    RUNS_ROOT,
    evaluate_predictions,
    enrich_predictions_with_cases,
    load_jsonl,
    pause_benchmark_run,
    read_json,
    schedule_benchmark_run,
    write_excel_report,
)
from app.legacy.utils.auth import TokenData, get_current_user


router = APIRouter(prefix="/admin/benchmarks", tags=["admin-benchmark"])

SUPPORTED_TRACKS = {"production_flow", "raw_retrieval", "final_list"}
TRACK_ALIASES = {
    "production_flow": "production_flow",
    "productionflow": "production_flow",
    "production-flow": "production_flow",
    "full_chain": "production_flow",
    "fullchain": "production_flow",
    "real_flow": "production_flow",
    "realflow": "production_flow",
    "raw_retrieval": "raw_retrieval",
    "rawretrieval": "raw_retrieval",
    "raw-retrieval": "raw_retrieval",
    "raw": "raw_retrieval",
    "final_list": "final_list",
    "finallist": "final_list",
    "final-list": "final_list",
    "list": "final_list",
}


class StartRunRequest(BaseModel):
    dataset_id: str
    track: str = Field(default="production_flow")
    top_k: int = Field(default=20, ge=1, le=100)


def _normalize_track(track: str) -> str | None:
    normalized = str(track or "").strip().lower()
    if not normalized:
        return None
    normalized = normalized.replace(" ", "").replace("\t", "")
    return TRACK_ALIASES.get(normalized) or (normalized if normalized in SUPPORTED_TRACKS else None)


def _get_store(request: Request) -> DocSearchBenchmarkStore:
    store = getattr(request.app.state, "doc_search_benchmark_store", None)
    if store is None:
        store = DocSearchBenchmarkStore()
        request.app.state.doc_search_benchmark_store = store
    return store


def _get_task_map(request: Request) -> dict[str, asyncio.Task]:
    task_map = getattr(request.app.state, "doc_search_benchmark_tasks", None)
    if task_map is None:
        task_map = {}
        request.app.state.doc_search_benchmark_tasks = task_map
    return task_map


def _ensure_report_excel(run_id: str) -> Path:
    run_dir = RUNS_ROOT / Path(run_id).name
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="运行不存在")

    path = run_dir / "report.xlsx"
    config = read_json(run_dir / "config.json", {})
    status = read_json(run_dir / "status.json", {})
    report = read_json(run_dir / "report.json", {})
    predictions_path = run_dir / "predictions.jsonl"
    if not config or not status:
        raise HTTPException(status_code=404, detail="运行配置不存在，无法生成测试报告")
    if status.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Benchmark 仍在运行中，完成后才能导出测试报告")
    if status.get("status") == "paused" and not report:
        raise HTTPException(status_code=404, detail="测试报告数据不存在，无法生成 Excel")
    if not report:
        raise HTTPException(status_code=404, detail="测试报告数据不存在，无法生成 Excel")
    if not predictions_path.exists():
        raise HTTPException(status_code=404, detail="预测结果不存在，无法生成 Excel")

    predictions = load_jsonl(predictions_path)
    all_cases: list[dict[str, Any]] = []
    dataset_id = str(config.get("dataset_id") or "")
    if dataset_id:
        try:
            all_cases = DocSearchBenchmarkStore().load_cases(dataset_id)
            predictions = enrich_predictions_with_cases(all_cases, predictions)
        except FileNotFoundError:
            pass
    if all_cases:
        report = evaluate_predictions(all_cases, predictions)
    events = load_jsonl(run_dir / "events.jsonl")
    try:
        write_excel_report(
            path,
            config=config,
            status=status,
            report=report,
            cases=all_cases,
            predictions=predictions,
            events=events,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成 Excel 测试报告失败: {exc}") from exc
    return path


@router.get("/doc-search/datasets")
async def list_doc_search_datasets(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    store = _get_store(request)
    return {"items": store.list_datasets()}


@router.get("/doc-search/runs")
async def list_doc_search_runs(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    store = _get_store(request)
    return {"items": store.list_runs()}


@router.post("/doc-search/runs")
async def start_doc_search_run(
    payload: StartRunRequest,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    store = _get_store(request)
    dataset_path = store.dataset_path(payload.dataset_id)
    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail="数据集不存在")

    normalized_track = _normalize_track(payload.track)
    if normalized_track is None:
        raise HTTPException(
            status_code=400,
            detail=f"track 不支持: {payload.track!r}。可选值: production_flow, raw_retrieval, final_list",
        )

    runtime_deps = getattr(request.app.state, "runtime_deps", None)
    if runtime_deps is None:
        raise HTTPException(status_code=500, detail="runtime deps 未初始化")

    run_id = store.create_run(
        dataset_id=payload.dataset_id,
        track=normalized_track,
        top_k=payload.top_k,
        created_by=current_user.username,
    )
    task = schedule_benchmark_run(store=store, runtime_deps=runtime_deps, run_id=run_id)
    _get_task_map(request)[run_id] = task
    return {"run_id": run_id, "status": "queued"}


@router.post("/doc-search/runs/{run_id}/pause")
async def pause_doc_search_run(
    run_id: str,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    task_map = _get_task_map(request)
    result = await pause_benchmark_run(task_map=task_map, run_id=run_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    if result["status"] == "already_done":
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.post("/doc-search/runs/{run_id}/resume")
async def resume_doc_search_run(
    run_id: str,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    store = _get_store(request)
    run_dir = store.run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="运行不存在")

    status = read_json(run_dir / "status.json", {})
    if status.get("status") not in {"paused"}:
        raise HTTPException(status_code=409, detail="只有暂停状态的运行才能继续")

    runtime_deps = getattr(request.app.state, "runtime_deps", None)
    if runtime_deps is None:
        raise HTTPException(status_code=500, detail="runtime deps 未初始化")

    task_map = _get_task_map(request)
    existing_task = task_map.get(run_id)
    if existing_task is not None and not existing_task.done():
        raise HTTPException(status_code=409, detail="该运行已有活跃任务，无法继续")

    task = schedule_benchmark_run(store=store, runtime_deps=runtime_deps, run_id=run_id, resume=True)
    task_map[run_id] = task
    return {"run_id": run_id, "status": "resuming"}


@router.get("/doc-search/runs/{run_id}")
async def get_doc_search_run_detail(
    run_id: str,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    store = _get_store(request)
    try:
        return store.get_run_detail(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="运行不存在") from exc


@router.get("/doc-search/runs/{run_id}/failures.csv")
async def download_doc_search_failures(
    run_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    path = RUNS_ROOT / Path(run_id).name / "failures.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="失败样例文件不存在")
    return FileResponse(path, media_type="text/csv", filename=f"{Path(run_id).name}_failures.csv")


@router.get("/doc-search/runs/{run_id}/report.xlsx")
async def download_doc_search_report_excel(
    run_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    path = _ensure_report_excel(run_id)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{Path(run_id).name}_report.xlsx",
    )


@router.get("/doc-search/overview")
async def get_doc_search_overview(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    del current_user
    store = _get_store(request)
    datasets = store.list_datasets()
    runs = store.list_runs()
    latest_run = runs[0] if runs else None
    running_count = sum(1 for item in runs if item.get("status") == "running")
    completed_count = sum(1 for item in runs if item.get("status") == "completed")
    failed_count = sum(1 for item in runs if item.get("status") == "failed")
    paused_count = sum(1 for item in runs if item.get("status") == "paused")
    latest_summary = (latest_run or {}).get("summary") or {}
    return {
        "datasets": {
            "count": len(datasets),
            "total_cases": sum(int(item.get("case_count") or 0) for item in datasets),
        },
        "runs": {
            "count": len(runs),
            "running_count": running_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "paused_count": paused_count,
        },
        "latest_run": latest_run,
        "latest_metrics": {
            "recall_at_5": latest_summary.get("recall_at_5"),
            "recall_at_10": latest_summary.get("recall_at_10"),
            "recall_at_50": latest_summary.get("recall_at_50"),
            "recall_at_100": latest_summary.get("recall_at_100"),
            "mrr": latest_summary.get("mrr"),
            "no_answer_accuracy": latest_summary.get("no_answer_accuracy"),
        },
        "scope": {
            "primary": "list_retrieval",
            "images_used_by_runner": True,
            "clarification_in_main_score": False,
            "note": "当前默认 runner 走真实资料搜索入口：图片证据分析 + AgentLoop doc_search workflow。固定评测指标统一看 Recall@5/10/50/100 与 MRR；配置中的主榜 Top-K 仅用于判断前台主榜命中、主榜外召回和失败诊断，不把澄清本身计入主分。",
        },
    }
