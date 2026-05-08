"""FastAPI entry: ShotGrid AMI + SSE logs + Nuke launcher."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

from . import config
from .engine import MagoEngine
from .shotgrid import (
    fetch_shots_from_sg,
    fetch_versions_from_sg,
    get_cached_colorspaces,
)
from .ui import FORMAT_DEFS, build_html

# studio sys.path + SG secrets file (hwang_edit lessons: multipart needs importing fastapi only)
config.load_sg_env_file()
config.ensure_sys_path()

app = FastAPI(title="MAGO TRANSCODER")
engine = MagoEngine()

try:
    get_cached_colorspaces()
except OSError as e:
    print(f"[OCIO] startup cache skip: {e}")


@app.api_route("/", response_class=HTMLResponse, methods=["GET", "POST"])
async def home(
    request: Request,
    ids: str | None = Query(None),
    names: str | None = Query(None),
) -> HTMLResponse:
    """AMI may GET with query params or POST form (application/x-www-form-urlencoded)."""
    final_ids = (ids or "").strip()
    final_names = (names or "").strip()

    if request.method == "POST":
        form = await request.form()
        if not final_ids:
            body_ids = form.get("selected_ids") or form.get("ids") or ""
            final_ids = str(body_ids).strip()
        if not final_names:
            body_names = form.get("name") or form.get("names") or ""
            final_names = str(body_names).strip()

    cs = get_cached_colorspaces()
    return HTMLResponse(content=build_html(cs, final_ids, final_names))


@app.get("/api/sg-info")
async def sg_info(ids: str = Query(""), names: str = Query("")) -> JSONResponse:
    shots: list[dict[str, Any]] = []

    if ids:
        raw_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        if raw_ids:
            shots = await asyncio.to_thread(fetch_versions_from_sg, raw_ids)
            if not shots:
                shots = await asyncio.to_thread(fetch_shots_from_sg, raw_ids)

    if not shots and names:
        for name in names.split(","):
            name = name.strip()
            if name:
                shots.append(
                    {
                        "id": 0,
                        "version_name": name,
                        "shot_name": name,
                        "frame_in": 1001,
                        "frame_out": 1001,
                        "source_path": "",
                        "movie_path": "",
                        "thumbnail": "",
                        "project": "",
                        "status": "",
                    }
                )

    return JSONResponse({"shots": shots})


@app.post("/api/render")
async def handle_render(request: Request) -> StreamingResponse:
    payload = await request.json()
    task_id = str(payload.get("task_id") or uuid.uuid4())
    log_queue: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(engine.run_transcode(payload, log_queue, task_id))

    async def event_stream() -> Any:
        yield f"data: {json.dumps({'log': '[API] Nuke 엔진 연결…'})}\n\n"
        try:
            while True:
                if task.done() and log_queue.empty():
                    break
                try:
                    msg = await asyncio.wait_for(log_queue.get(), timeout=0.25)
                    yield f"data: {json.dumps({'log': msg})}\n\n"
                except asyncio.TimeoutError:
                    continue
        finally:
            if not task.done():
                task.cancel()
                print(f"[API] stream 종료 → task {task_id} cancel")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.delete("/api/render/{task_id}")
async def cancel_render(task_id: str) -> JSONResponse:
    engine.kill_process(task_id)
    return JSONResponse({"status": "terminated", "task_id": task_id})


@app.get("/api/formats")
async def get_formats() -> JSONResponse:
    return JSONResponse(FORMAT_DEFS)


@app.get("/api/colorspaces")
async def get_colorspaces() -> JSONResponse:
    return JSONResponse({"colorspaces": get_cached_colorspaces(), "ocio_config": config.OCIO_CONFIG_PATH})


def main() -> None:
    print("-" * 60)
    print("  MAGO TRANSCODER")
    print(f"  Host   : http://{config.HOST}:{config.PORT}")
    print(f"  Nuke   : {config.NUKE_EXEC}")
    print(f"  Script : {config.NUKE_CONVERTER}")
    print(f"  OCIO   : {config.OCIO_CONFIG_PATH}")
    print("-" * 60)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
