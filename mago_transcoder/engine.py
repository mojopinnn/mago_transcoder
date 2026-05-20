"""Subprocess runner for Nuke CLI (Unix-friendly process groups)."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from . import config


def _preexec_child() -> Any:
    """Unix: new session so killpg can stop Nuke tree. Windows: no-op."""
    if os.name == "nt":
        return None
    return os.setsid  # type: ignore[attr-defined, unused-ignore]


class MagoEngine:
    def __init__(self) -> None:
        self.active_processes: dict[str, asyncio.subprocess.Process] = {}

    async def run_transcode(self, payload: dict[str, Any], log_queue: asyncio.Queue, task_id: str) -> None:
        shot_name = payload.get("shot_name", "Unknown")
        frames = payload.get("frames", "1001-1001")
        fmt = payload.get("format", "exr")
        codec = payload.get("codec", "piz")
        bitdepth = payload.get("bitdepth", "half")
        colorspace_in = payload.get("colorspace_in", "linear")
        colorspace = payload.get("colorspace", "linear")
        source_path = payload.get("source_path", "")
        output_path = payload.get("output_path", "")
        fps = payload.get("fps")

        await log_queue.put(f"[Engine] '{shot_name}' 트랜스코딩 시작")
        await log_queue.put(
            f"[Engine] 프레임: {frames} | 포맷: {str(fmt).upper()} | 코덱: {codec} | 비트뎁스: {bitdepth}"
        )
        await log_queue.put(f"[Engine] 컬러: {colorspace_in} → {colorspace}")
        
        # Nuke 실행 명령 구성 (사내 툴 참고)
        # --nukex: NukeX 기능 사용
        # -i: 대화형 모드 비활성화 (no interactive)
        # -t: 터미널 모드 (no UI)
        cmd: list[str] = [
            config.NUKE_EXEC,
            "--nukex",
            "-i",
            "-t",
            config.NUKE_CONVERTER,
            "--shot",
            shot_name,
            "--frames",
            frames,
            "--format",
            str(fmt),
            "--codec",
            str(codec),
            "--bitdepth",
            str(bitdepth),
            "--colorspace-in",
            str(colorspace_in),
            "--colorspace",
            str(colorspace),
            "--ocio",
            config.OCIO_CONFIG_PATH,
        ]
        
        if source_path:
            cmd += ["--source", source_path]
        if output_path:
            cmd += ["--output", output_path]
        if fps not in (None, "", 0, "0"):
            cmd += ["--fps", str(fps)]

        await log_queue.put(f"[CMD] {' '.join(cmd)}")

        # 환경 변수 준비 (라이선스 정보 등 포함)
        current_env = os.environ.copy()
        current_env.update(config.NUKE_ENV)

        try:
            preexec = _preexec_child()
            # stderr=asyncio.subprocess.STDOUT를 사용하여 에러 출력도 로그에 합침
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=current_env,
                preexec_fn=preexec,
            )
            self.active_processes[task_id] = process

            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                safe = line.decode(errors="ignore").strip()
                if safe:
                    await log_queue.put(safe)

            await process.wait()

            if process.returncode == 0:
                await log_queue.put("[SUCCESS] 트랜스코딩이 완료되었습니다.")
            else:
                rc = process.returncode or 0
                status = "중단됨" if rc < 0 else f"실패 (Code: {rc})"
                await log_queue.put(f"[FAILED] 작업 {status}")

        except asyncio.CancelledError:
            await log_queue.put("[SYSTEM] 사용자가 작업을 중단했습니다. Nuke 프로세스를 종료합니다.")
            self.kill_process(task_id)
            raise
        except Exception as e:
            await log_queue.put(f"[ERROR] 엔진 실행 중 오류 발생: {e}")
        finally:
            self.active_processes.pop(task_id, None)

    def kill_process(self, task_id: str) -> None:
        process = self.active_processes.get(task_id)
        if not process:
            return
        try:
            if os.name == "nt":
                # Windows에서는 강제 종료(kill) 시도
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            print(f"[Cleanup] Task {task_id} (PID {process.pid}) 종료 요청 완료.")
        except Exception as e:
            print(f"[Cleanup] 종료 중 오류: {e}")
