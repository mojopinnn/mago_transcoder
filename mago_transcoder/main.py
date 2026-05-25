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
import shutil
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
        if os.path.exists(new_dir):
            return JSONResponse({"status": "error", "message": "이미 존재하는 폴더 이름입니다."}, status_code=400)
            
        os.makedirs(new_dir, exist_ok=True)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/rename-folder")
async def rename_folder(request: Request) -> JSONResponse:
    payload = await request.json()
    current_path = payload.get("current_path")
    old_name = payload.get("old_name")
    new_name = payload.get("new_name")
    
    if not current_path or not old_name or not new_name:
        return JSONResponse({"status": "error", "message": "파라미터 누락"}, status_code=400)
        
    try:
        old_dir = os.path.join(current_path, old_name)
        new_dir = os.path.join(current_path, new_name)
        
        if not os.path.exists(old_dir):
            return JSONResponse({"status": "error", "message": "대상 폴더가 존재하지 않습니다."}, status_code=400)
        if os.path.exists(new_dir):
            return JSONResponse({"status": "error", "message": "이미 존재하는 이름입니다."}, status_code=400)
            
        os.rename(old_dir, new_dir)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/delete-folder")
async def delete_folder(request: Request) -> JSONResponse:
    payload = await request.json()
    current_path = payload.get("current_path")
    folder_name = payload.get("folder_name")
    
    if not current_path or not folder_name:
        return JSONResponse({"status": "error", "message": "파라미터 누락"}, status_code=400)
        
    try:
        target_dir = os.path.join(current_path, folder_name)
        if not os.path.exists(target_dir):
            return JSONResponse({"status": "error", "message": "대상 폴더가 존재하지 않습니다."}, status_code=400)
            
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir)
        else:
            os.remove(target_dir)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/view-folder", response_class=HTMLResponse)
async def view_folder(path: str = Query(""), mode: str = Query("")) -> Any:
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
        parent_url = f"/view-folder?path={urllib.parse.quote(parent_dir)}{'&mode='+mode if mode else ''}"
        parent_link = f'<a href="{parent_url}" style="text-decoration:none;font-weight:bold;color:#3b82f6;display:inline-block;">[상위 폴더로 이동]</a>'

    safe_path_js = path.replace("\\", "\\\\")
    
    # mode == "select" 일 때 표시할 최상단 선택 버튼 렌더링
    select_btn_html = ""
    if mode == "select":
        select_btn_html = f"""
        <div style="margin-bottom: 20px; padding: 12px; background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.4); border-radius: 8px; text-align: center;">
            <button onclick="selectCurrentFolder()" style="background: #10b981; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px;">✅ 이 폴더를 아웃풋으로 선택</button>
            <script>
                function selectCurrentFolder() {{
                    if (window.opener && window.opener.receiveSelectedOutput) {{
                        window.opener.receiveSelectedOutput('{safe_path_js}');
                        window.close();
                    }} else {{
                        alert('부모 창을 찾을 수 없습니다.');
                    }}
                }}
            </script>
        </div>
        """

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
                .focused-folder {{ background: rgba(59,130,246,0.2); outline: 1px solid #3b82f6; border-radius: 4px; }}
                .context-menu {{ position: absolute; background: #1f2937; border: 1px solid #374151; border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); padding: 4px 0; min-width: 180px; z-index: 1000; font-family: system-ui, sans-serif; font-size: 13px; }}
                .context-menu-item {{ padding: 8px 16px; cursor: pointer; color: #d1d5db; display: flex; align-items: center; gap: 8px; }}
                .context-menu-item:hover {{ background: #374151; color: #fff; }}
            </style>
            <script>
                let selectedItem = null;
                let selectedName = "";
                let selectedFullPath = "";
                let renameMode = false;

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
                                const response = await fetch('/api/create-folder', {{
                                    method: 'POST',
                                    headers: {{ 'Content-Type': 'application/json' }},
                                    body: JSON.stringify({{ current_path: '{safe_path_js}', folder_name: safeName }})
                                }});
                                const data = await response.json();
                                if (data.status === 'error') {{
                                    alert(data.message);
                                    input.focus();
                                    input.select();
                                    return;
                                }}
                                location.reload();
                            }} catch (e) {{
                                alert('폴더 생성 요청 실패');
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

                document.addEventListener('DOMContentLoaded', () => {{
                    document.addEventListener('click', (e) => {{
                        const ctxMenu = document.getElementById('context-menu');
                        if (ctxMenu) ctxMenu.remove();
                        
                        if (!e.target.closest('.folder') && !e.target.closest('.file') && !e.target.closest('.context-menu')) {{
                            if (selectedItem && !renameMode) {{
                                selectedItem.classList.remove('focused-folder');
                                selectedItem = null;
                                selectedName = "";
                                selectedFullPath = "";
                            }}
                        }}
                    }});

                    document.addEventListener('keydown', (e) => {{
                        if (renameMode || !selectedItem) return;
                        if (e.key === 'F2') {{
                            startRename();
                        }} else if (e.key === 'Delete') {{
                            deleteSelected();
                        }}
                    }});
                }});

                function handleItemClick(e, itemEl, itemName, fullPath) {{
                    e.preventDefault();
                    if (renameMode) return;

                    if (selectedItem === itemEl) {{
                        startRename();
                    }} else {{
                        if (selectedItem) selectedItem.classList.remove('focused-folder');
                        selectedItem = itemEl;
                        selectedName = itemName;
                        selectedFullPath = fullPath;
                        selectedItem.classList.add('focused-folder');
                    }}
                }}

                function handleItemDblClick(e, url) {{
                    e.preventDefault();
                    if (renameMode) return;
                    location.href = url;
                }}

                function handleItemContextMenu(e, itemEl, itemName, fullPath) {{
                    e.preventDefault();
                    if (renameMode) return;
                    
                    if (selectedItem && selectedItem !== itemEl) {{
                        selectedItem.classList.remove('focused-folder');
                    }}
                    selectedItem = itemEl;
                    selectedName = itemName;
                    selectedFullPath = fullPath;
                    selectedItem.classList.add('focused-folder');

                    const existing = document.getElementById('context-menu');
                    if (existing) existing.remove();

                    const menu = document.createElement('div');
                    menu.id = 'context-menu';
                    menu.className = 'context-menu';
                    menu.style.left = e.pageX + 'px';
                    menu.style.top = e.pageY + 'px';
                    
                    menu.innerHTML = `
                        <div class="context-menu-item" onclick="openOSExplorer()">📂 OS 탐색기 열기</div>
                        <div class="context-menu-item" onclick="copyPath()">📋 경로 복사</div>
                        <div class="context-menu-item" onclick="startRename()">✏️ 이름 변경 (F2)</div>
                        <div class="context-menu-item" onclick="deleteSelected()" style="color:#ef4444">❌ 삭제 (Del)</div>
                    `;
                    document.body.appendChild(menu);
                }}

                async function openOSExplorer() {{
                    try {{
                        await fetch('/api/open-folder', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ path: selectedFullPath }})
                        }});
                    }} catch(e) {{ alert("폴더 열기 실패"); }}
                }}

                function copyPath() {{
                    navigator.clipboard.writeText(selectedFullPath).then(() => {{}});
                }}

                function startRename() {{
                    if (!selectedItem) return;
                    renameMode = true;
                    
                    const span = selectedItem.querySelector('span');
                    const originalHTML = selectedItem.innerHTML;
                    
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.value = selectedName;
                    input.style.cssText = "background: rgba(0,0,0,0.5); border: 1px solid #fcd34d; color: #fcd34d; padding: 2px 6px; font-family: monospace; outline: none; border-radius: 3px; width: auto;";
                    
                    span.replaceWith(input);
                    input.focus();
                    input.select();

                    const finishRename = async (isCancel) => {{
                        input.removeEventListener('blur', blurHandler);
                        const newName = input.value.replace(/[^a-zA-Z0-9_.-]/g, '_');
                        
                        if (isCancel || newName === selectedName || newName === '') {{
                            selectedItem.innerHTML = originalHTML;
                            renameMode = false;
                            return;
                        }}

                        try {{
                            const res = await fetch('/api/rename-folder', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{ current_path: '{safe_path_js}', old_name: selectedName, new_name: newName }})
                            }});
                            const data = await res.json();
                            if (data.status === 'error') {{
                                alert(data.message);
                                selectedItem.innerHTML = originalHTML;
                            }} else {{
                                location.reload();
                            }}
                        }} catch (e) {{
                            alert("이름 변경 실패");
                            selectedItem.innerHTML = originalHTML;
                        }}
                        renameMode = false;
                    }};

                    const blurHandler = () => finishRename(false);
                    input.addEventListener('blur', blurHandler);
                    input.addEventListener('keydown', (e) => {{
                        e.stopPropagation();
                        if (e.key === 'Enter') {{
                            input.blur();
                        }} else if (e.key === 'Escape') {{
                            finishRename(true);
                        }}
                    }});
                }}

                async function deleteSelected() {{
                    if (!selectedItem) return;
                    if (!confirm(`'` + selectedName + `' 항목을 정말 삭제하시겠습니까?`)) return;
                    
                    try {{
                        const res = await fetch('/api/delete-folder', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ current_path: '{safe_path_js}', folder_name: selectedName }})
                        }});
                        const data = await res.json();
                        if (data.status === 'error') {{
                            alert(data.message);
                        }} else {{
                            location.reload();
                        }}
                    }} catch (e) {{
                        alert("삭제 요청 실패");
                    }}
                }}
            </script>
        </head>
        <body>
            {select_btn_html}
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
        item_url = f"/view-folder?path={urllib.parse.quote(full_item_path)}{'&mode='+mode if mode else ''}"
        cls = "folder" if is_dir else "file"
        icon = "📁 " if is_dir else "📄 "
        
        safe_item_js = item.replace("\\", "\\\\").replace("'", "\\'")
        safe_full_path_js = full_item_path.replace("\\", "\\\\").replace("'", "\\'")
        
        events = f"""
            onclick="handleItemClick(event, this, '{safe_item_js}', '{safe_full_path_js}')"
            ondblclick="handleItemDblClick(event, '{item_url}')"
            oncontextmenu="handleItemContextMenu(event, this, '{safe_item_js}', '{safe_full_path_js}')"
        """
        html_content += f'<a href="{item_url}" class="{cls}" {events}>{icon}<span>{item}</span></a>\n'
        
    html_content += "</div></body></html>"
    return HTMLResponse(html_content)


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
