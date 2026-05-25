"""Subprocess runner for Nuke CLI (Unix-friendly process groups)."""

from __future__ import annotations

import asyncio
import os
import signal
import re
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
        
        # [수정] 단일 프레임 렌더링 최적화
        frame_arg = "--frames"
        if "-" not in frames:
            frame_arg = "-F" if frames.isdigit() else "--frames"

        cmd: list[str] = [
            config.NUKE_EXEC,
            "--nukex",
            "-i",
            "-t",
            config.NUKE_CONVERTER,
            "--shot",
            shot_name,
            frame_arg,
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
            # [추가 방어 로직] Nuke 렌더 실행 전 아웃풋 디렉토리 자동 생성
            try:
                out_dir = os.path.dirname(output_path)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                    await log_queue.put(f"[SYSTEM] 아웃풋 디렉토리 생성 완료: {out_dir}")
            except Exception as e:
                await log_queue.put(f"[ERROR] 아웃풋 디렉토리 생성 실패: {e}")

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
            
            # 진행률 파싱용 정규식 (예: Frame 10 (1 of 10))
            # 또는 Nuke 기본 출력: "Writing /path/to/file.0100.exr took 0.12 seconds"
            # Nuke 커맨드라인 렌더링은 보통 파일이 쓰여질 때마다 로그가 나옵니다.
            # 커스텀 컨버터 스크립트에서 "Frame X (Y of Z)" 형태로 뱉는다고 가정하고 정규식 작성
            progress_pattern = re.compile(r'(?:Frame|프레임)\s*\d+\s*\((\d+)\s*(?:of|/)\s*(\d+)\)', re.IGNORECASE)
            
            # 전체 프레임 수 계산 (기본 백업용)
            total_frames = 1
            if "-" in frames:
                try:
                    f_start, f_end = map(int, frames.split("-"))
                    total_frames = abs(f_end - f_start) + 1
                except:
                    pass
            current_frame_idx = 0

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                safe = line.decode(errors="ignore").strip()
                if safe:
                    # 1. 명시적인 Progress 패턴 매칭: Frame 1001 (1 of 50)
                    match = progress_pattern.search(safe)
                    if match:
                        current = int(match.group(1))
                        total = int(match.group(2))
                        percent = int((current / total) * 100)
                        await log_queue.put(f"[PROGRESS] {percent}")
                    
                    # 2. 범용 Nuke 파일 쓰기 로그 매칭
                    elif "Writing" in safe and "took" in safe:
                        current_frame_idx += 1
                        percent = int((current_frame_idx / total_frames) * 100)
                        # 100%를 초과하지 않도록 방어
                        percent = min(percent, 100)
                        await log_queue.put(f"[PROGRESS] {percent}")
                        
                    # 3. 에러 발생 시 방어 로직 (Error, License, Exception 등)
                    elif any(err in safe.lower() for err in ["error:", "license failure", "traceback", "exception"]):
                        await log_queue.put(safe)
                        await log_queue.put("[ERROR_STATE]")
                    else:
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
