"""FastAPI entry: ShotGrid AMI + SSE logs + Nuke launcher."""

from __future__ import annotations

# 1. config 모듈을 먼저 가져와서 경로 시스템을 확장합니다.
from . import config
config.load_sg_env_file()
config.ensure_sys_path()

# 2. 그 다음 기존 표준 라이브러리와 외부 패키지들을 임포트합니다.
import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
import uvicorn
import urllib.parse
import os

from .engine import MagoEngine
from .shotgrid import (
    fetch_shots_from_sg,
    fetch_versions_from_sg,
    get_cached_colorspaces,
)
from .ui import FORMAT_DEFS, build_html

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


@app.post("/api/create-folder")
async def create_folder(request: Request) -> JSONResponse:
    payload = await request.json()
    current_path = payload.get("current_path")
    folder_name = payload.get("folder_name")
    
    if not current_path or not folder_name:
        return JSONResponse({"status": "error", "message": "파라미터 누락"}, status_code=400)
        
    try:
        new_dir = os.path.join(current_path, folder_name)
        os.makedirs(new_dir, exist_ok=True)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/view-folder", response_class=HTMLResponse)
async def view_folder(path: str = Query("")) -> Any:
    """웹 브라우저 기반 자체 파일 뷰어"""
    if not path or not os.path.exists(path):
        return HTMLResponse("<h1>경로가 존재하지 않습니다.</h1>", status_code=404)

    if os.path.isfile(path):
        return FileResponse(path)

    try:
        items = os.listdir(path)
        items.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
    except Exception as e:
        return HTMLResponse(f"<h1>디렉토리 읽기 오류: {e}</h1>", status_code=500)

    parent_dir = os.path.dirname(path)
    if parent_dir == path:
        parent_link = ""
    else:
        parent_url = f"/view-folder?path={urllib.parse.quote(parent_dir)}"
        parent_link = f'<a href="{parent_url}" style="text-decoration:none;font-weight:bold;color:#3b82f6;display:inline-block;">[상위 폴더로 이동]</a>'

    html_content = f"""
    <html>
        <head>
            <title>File Viewer: {path}</title>
            <style>
                body {{ font-family: monospace; background: #0b0d11; color: #d1d5db; padding: 20px; }}
                a {{ color: #60a5fa; text-decoration: none; display: block; padding: 4px 0; }}
                a:hover {{ text-decoration: underline; color: #93c5fd; }}
                .folder {{ font-weight: bold; color: #fcd34d; display: flex; align-items: center; gap: 6px; padding: 4px 0; }}
                .file {{ color: #d1d5db; display: flex; align-items: center; gap: 6px; padding: 4px 0; }}
                .action-row {{ margin-bottom: 20px; display: flex; gap: 16px; align-items: center; }}
                .create-btn {{ background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.4); color: #93c5fd; cursor: pointer; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-family: monospace; }}
                .create-btn:hover {{ background: rgba(59,130,246,0.2); }}
                #new-folder-input {{ background: rgba(0,0,0,0.5); border: 1px solid #fcd34d; color: #fcd34d; padding: 2px 6px; font-family: monospace; outline: none; border-radius: 3px; }}
            </style>
            <script>
                function showNewFolderInput() {{
                    if (document.getElementById('new-folder-row')) return;
                    
                    const list = document.getElementById('item-list');
                    const row = document.createElement('div');
                    row.id = 'new-folder-row';
                    row.className = 'folder';
                    
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.id = 'new-folder-input';
                    input.value = 'untitled_folder';
                    
                    row.innerHTML = '📁 ';
                    row.appendChild(input);
                    list.insertBefore(row, list.firstChild);
                    
                    input.focus();
                    input.select();
                    
                    const handleCreate = async () => {{
                        const rawName = input.value;
                        const safeName = rawName.replace(/[^a-zA-Z0-9_-]/g, '_');
                        
                        if (safeName && safeName !== '') {{
                            try {{
                                await fetch('/api/create-folder', {{
                                    method: 'POST',
                                    headers: {{ 'Content-Type': 'application/json' }},
                                    body: JSON.stringify({{ current_path: '{path.replace("\\", "\\\\")}', folder_name: safeName }})
                                }});
                                location.reload();
                            }} catch (e) {{
                                alert('폴더 생성 실패');
                                row.remove();
                            }}
                        }} else {{
                            row.remove();
                        }}
                    }};
                    
                    input.addEventListener('blur', handleCreate);
                    input.addEventListener('keydown', (e) => {{
                        if (e.key === 'Enter') {{
                            input.blur();
                        }} else if (e.key === 'Escape') {{
                            input.removeEventListener('blur', handleCreate);
                            row.remove();
                        }}
                    }});
                }}
            </script>
        </head>
        <body>
            <h2>📁 {path}</h2>
            <div class="action-row">
                {parent_link}
                <button class="create-btn" onclick="showNewFolderInput()">[새 폴더 생성]</button>
            </div>
            <div id="item-list">
    """
    
    for item in items:
        full_item_path = os.path.join(path, item)
        is_dir = os.path.isdir(full_item_path)
        item_url = f"/view-folder?path={urllib.parse.quote(full_item_path)}"
        cls = "folder" if is_dir else "file"
        icon = "📁 " if is_dir else "📄 "
        html_content += f'<a href="{item_url}" class="{cls}">{icon}<span>{item}</span></a>\n'
        
    html_content += "</div></body></html>"
    return HTMLResponse(html_content)


@app.get("/api/browse-folder")
async def browse_folder() -> JSONResponse:
    """tkinter를 사용하여 OS 네이티브 폴더 선택 창을 띄웁니다."""
    import tkinter as tk
    from tkinter import filedialog

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder_path = filedialog.askdirectory(parent=root, title="출력 폴더 선택")
        root.destroy()
        return JSONResponse({"path": folder_path.replace("\\", "/") if folder_path else ""})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/open-folder")
async def open_folder(request: Request) -> JSONResponse:
    payload = await request.json()
    path = payload.get("path")
    if not path or not os.path.exists(path):
        return JSONResponse({"status": "error", "message": "존재하지 않는 경로입니다."}, status_code=400)

    try:
        import platform
        import subprocess

        system = platform.system()
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":  # macOS
            subprocess.run(["open", path])
        else:  # Linux
            subprocess.run(["xdg-open", path])
        
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


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
