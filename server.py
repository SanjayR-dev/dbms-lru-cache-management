"""
Web UI + REST API for buffer pool lab.

Uses MongoDB when available; falls back to SQLite (data/buffer_lab.db) automatically.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from db.backend import get_active_backend
from db.session import connection_info, startup_database, use_store
from repository import parse_reference_string

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_PAGES = 2000
API_VERSION = "3.0.4"


def _seed_defaults() -> None:
    from repository import parse_reference_string as prs

    with use_store() as (repo, disk):
        pages = list(range(0, 16)) + [7, 1, 2, 3, 4, 5]
        disk.seed_pages(pages)
        refs = prs(
            [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1],
            write_every_nth=5,
        )
        repo.save_workload(
            "classic-20", 3, refs, description="Standard 3-frame trace with periodic writes"
        )
        repo.save_workload(
            "belady-12",
            3,
            prs([1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]),
            description="Belady anomaly demonstration",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend = startup_database()
    app.state.db_backend = backend
    _seed_defaults()
    yield


app = FastAPI(title="LRU Buffer Pool Lab", version=API_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=500, content={"detail": str(exc)})


class SimulateBody(BaseModel):
    frames: int = Field(ge=1, le=64, default=3)
    pages: list[int] = Field(min_length=1, max_length=MAX_PAGES)
    mode: Literal["memory", "db_backed"] = "memory"
    policy: Literal["lru", "fifo", "optimal"] = "lru"
    write_every_nth: int = Field(ge=0, le=100, default=0)
    persist: bool = True
    workload_id: Optional[int] = None
    auto_seed: bool = True


class WorkloadBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    frames: int = Field(ge=1, le=64)
    pages: list[int] = Field(min_length=1, max_length=MAX_PAGES)
    write_every_nth: int = Field(ge=0, le=100, default=0)
    description: Optional[str] = None


class SeedBody(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=500)
    prefix: str = "row"


@app.get("/api/health")
def api_health() -> dict:
    info = connection_info()
    return {
        "ok": True,
        "version": API_VERSION,
        "backend": get_active_backend(),
        "database": info.get("database"),
        "features": ["mongodb", "sqlite", "disk", "history", "lru", "fifo", "optimal", "clear_history"],
    }


@app.get("/api/db/status")
def api_db_status() -> dict:
    with use_store() as (repo, _disk):
        return {"connection": connection_info(), "tables": repo.db_stats()}


@app.get("/api/disk/pages")
def api_disk_pages(limit: int = 200) -> dict:
    with use_store() as (_repo, disk):
        return {"pages": disk.list_pages(limit=limit), "count": disk.page_count()}


@app.post("/api/disk/seed")
def api_disk_seed(body: SeedBody) -> dict:
    with use_store() as (_repo, disk):
        created = disk.seed_pages(body.page_ids, prefix=body.prefix)
        return {"created": created, "total_on_disk": disk.page_count()}


@app.get("/api/workloads")
def api_list_workloads() -> dict:
    with use_store() as (repo, _disk):
        return {"workloads": repo.list_workloads()}


@app.get("/api/workloads/{workload_id}")
def api_get_workload(workload_id: int) -> dict:
    with use_store() as (repo, _disk):
        wl = repo.get_workload(workload_id)
        if not wl:
            raise HTTPException(status_code=404, detail="Workload not found")
        return wl


@app.post("/api/workloads")
def api_create_workload(body: WorkloadBody) -> dict:
    refs = parse_reference_string(body.pages, body.write_every_nth)
    with use_store() as (repo, _disk):
        wl = repo.save_workload(body.name, body.frames, refs, body.description)
        return {"id": wl["_id"], "name": wl["name"]}


@app.get("/api/runs")
def api_list_runs(limit: int = 30) -> dict:
    with use_store() as (repo, _disk):
        return {"runs": repo.list_runs(limit=limit)}


def _clear_all_runs() -> dict:
    with use_store() as (repo, _disk):
        deleted = repo.delete_all_runs()
        return {"deleted": deleted, "ok": True}


@app.delete("/api/runs")
def api_delete_all_runs() -> dict:
    return _clear_all_runs()


@app.post("/api/runs/clear")
def api_clear_all_runs_post() -> dict:
    return _clear_all_runs()


@app.get("/api/runs/{run_id}")
def api_get_run(run_id: int) -> dict:
    with use_store() as (repo, _disk):
        run = repo.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run


@app.post("/api/simulate")
def api_simulate(body: SimulateBody) -> dict:
    refs = parse_reference_string(body.pages, body.write_every_nth)
    with use_store() as (repo, _disk):
        try:
            if body.mode == "db_backed":
                return repo.run_db_backed_simulation(
                    body.frames,
                    refs,
                    policy=body.policy,
                    workload_id=body.workload_id,
                    persist=body.persist,
                    auto_seed=body.auto_seed,
                )
            return repo.run_memory_simulation(
                body.frames,
                refs,
                policy=body.policy,
                workload_id=body.workload_id,
                persist=body.persist,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc) + " Use POST /api/disk/seed or enable auto_seed.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/simulate/compare")
def api_simulate_compare(body: SimulateBody) -> dict:
    refs = parse_reference_string(body.pages, body.write_every_nth)
    page_ids = [r["page_id"] for r in refs]
    from buffer_pool import optimal_hit_ratio

    with use_store() as (repo, _disk):
        rows = []
        for policy in ("lru", "fifo", "optimal"):
            result = repo.run_memory_simulation(
                body.frames, refs, policy=policy, persist=False
            )
            rows.append(
                {
                    "policy": policy,
                    "hit_ratio": result["stats"]["hit_ratio"],
                    "hits": result["stats"]["hits"],
                    "misses": result["stats"]["misses"],
                    "evictions": result["stats"]["evictions"],
                }
            )
        return {
            "frame_count": body.frames,
            "reference_count": len(page_ids),
            "optimal_hit_ratio": round(optimal_hit_ratio(body.frames, page_ids), 6),
            "policies": rows,
        }


def _static_file(name: str) -> FileResponse:
    path = STATIC_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Missing static file: {name}")
    headers = {"Cache-Control": "no-cache"} if name.endswith((".js", ".html")) else {}
    return FileResponse(path, headers=headers)


@app.get("/")
def serve_index() -> FileResponse:
    return _static_file("index.html")


@app.get("/styles.css")
def serve_css() -> FileResponse:
    return _static_file("styles.css")


@app.get("/app.js")
def serve_js() -> FileResponse:
    return _static_file("app.js")


def _open_browser(url: str) -> None:
    time.sleep(0.8)
    webbrowser.open(url)


def main() -> None:
    import uvicorn

    host = "127.0.0.1"
    port = int(os.environ.get("PORT", "8765"))
    url = f"http://{host}:{port}/"
    print(f"Buffer Pool Lab v{API_VERSION}")
    print(f"Open {url}  (Ctrl+C to stop)")
    if os.environ.get("SKIP_BROWSER", "").strip() not in ("1", "true", "yes"):
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
