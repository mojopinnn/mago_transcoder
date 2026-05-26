#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAGO TRANSCODER — Nuke Converter (terminal / -t mode)

회사 파이프라인에 복사 시 `mago_transcoder` 서버가 다음 인자로 CLI와 맞춰야 함:
  --shot --frames --format --codec --bitdepth
  --colorspace-in (입력)  --colorspace (출력)
  --ocio [--source] [--output] [--fps]
"""

import argparse
import glob
import os
import re
import sys

try:
    import nuke
except ImportError:
    # Nuke 내부에서 실행되지 않을 경우 에러 메시지 출력 후 종료
    print("[ERROR] 'nuke' 모듈을 찾을 수 없습니다. Nuke 내부에서 실행 중인지 확인하세요.")
    sys.exit(1)


FORMAT_EXT = {
    "exr": "exr",
    "dpx": "dpx",
    "tiff": "tiff",
    "png": "png",
    "mov": "mov",
}

# MOV: 단일 클립 — 한 번에 first~last execute
CONTAINER_FORMATS = frozenset({"mov"})


def parse_args():
    parser = argparse.ArgumentParser(description="MAGO Nuke Converter")
    parser.add_argument("--shot", required=True, help="샷 이름")
    parser.add_argument("--frames", required=True, help="1001-1100 또는 1001")
    parser.add_argument("--format", default="exr", help="exr/dpx/tiff/png/mov")
    parser.add_argument("--codec", default="piz", help="포맷별 코덱/압축")
    parser.add_argument("--bitdepth", default="half", help="비트뎁스")
    parser.add_argument("--colorspace", default="linear", help="출력 컬러스페이스")
    parser.add_argument(
        "--colorspace-in",
        default="linear",
        dest="colorspace_in",
        help="입력 컬러스페이스 (OCIO 노드 in)",
    )
    parser.add_argument("--ocio", default="", help="OCIO config 경로")
    parser.add_argument("--source", default="", help="소스 #### 시퀀스")
    parser.add_argument("--output", default="", help="출력 #### 또는 클립 경로")
    parser.add_argument(
        "--fps",
        type=float,
        default=0.0,
        help="MOV 시 root fps (0이면 Nuke 기본값 유지)",
    )
    parser.add_argument("--slate", action="store_true", help="슬레이트 합성 여부")
    parser.add_argument("--slate_version", default=None, help="슬레이트 표기 커스텀 버전 (예: v002)")
    parser.add_argument("--project", default="", help="프로젝트 코드명 (예: ews, wm)")
    args, _ = parser.parse_known_args()
    return args


def parse_frame_range(frames_str: str):
    if "-" in frames_str:
        parts = frames_str.split("-")
        return int(parts[0]), int(parts[1])
    try:
        f = int(frames_str)
        return f, f
    except ValueError:
        print("[ERROR] 잘못된 프레임 범위 형식입니다: %s" % frames_str)
        sys.exit(1)


def configure_write_node(write_node, fmt: str, codec: str, bitdepth: str):
    ext = FORMAT_EXT.get(fmt, "exr")
    write_node["file_type"].setValue(ext)

    if fmt == "exr":
        compression_map = {
            "none": "none",
            "zip1": "Zip (1 scanline)",
            "zip16": "Zip (16 scanlines)",
            "piz": "PIZ Wavelet (lossless)",
            "pxr24": "PXR24 (lossy)",
            "b44": "B44",
            "b44a": "B44A",
            "dwaa": "DWAA",
            "dwab": "DWAB",
        }
        comp = compression_map.get(codec, "PIZ Wavelet (lossless)")
        write_node["compression"].setValue(comp)
        bd_map = {"half": "16 bit half", "float": "32 bit float"}
        write_node["datatype"].setValue(bd_map.get(bitdepth, "16 bit half"))
        print(f"[Nuke] EXR 설정: compression={comp}, datatype={write_node['datatype'].value()}")

    elif fmt == "dpx":
        bd_map = {"8": "8 bit", "10": "10 bit", "12": "12 bit", "16": "16 bit"}
        write_node["datatype"].setValue(bd_map.get(bitdepth, "10 bit"))

    elif fmt == "tiff":
        comp_map = {"none": "None", "lzw": "LZW", "deflate": "Deflate"}
        write_node["compression"].setValue(comp_map.get(codec, "None"))
        bd_map = {"8": "8 bit", "16": "16 bit", "32": "32 bit float"}
        write_node["datatype"].setValue(bd_map.get(bitdepth, "16 bit"))

    elif fmt == "png":
        bd_map = {"8": "8 bit", "16": "16 bit"}
        write_node["datatype"].setValue(bd_map.get(bitdepth, "8 bit"))

    elif fmt == "mov":
        codec_map = {
            "h264": "H.264",
            "h265": "H.265 (HEVC)",
            "prores422": "Apple ProRes 422",
            "prores4444": "Apple ProRes 4444",
            "dnxhd": "DNxHD",
        }
        write_node["codec"].setValue(codec_map.get(codec, "Apple ProRes 422"))
        if bitdepth == "10":
            try:
                write_node["bitDepth"].setValue(10)
            except Exception:
                pass


def configure_ocio_root(ocio_path: str):
    if not ocio_path or not os.path.exists(ocio_path):
        print(f"[Nuke] OCIO 미지정 혹은 경로 누락: {ocio_path} — 컬러 변환 없이 진행합니다.")
        return False

    try:
        os.environ["OCIO"] = ocio_path
        nuke.root()["colorManagement"].setValue("OCIO")
        nuke.root()["OCIO_config"].setValue("custom")
        nuke.root()["customOCIOConfigPath"].setValue(ocio_path)
        print(f"[Nuke] OCIO Root 설정 활성화: {ocio_path}")
        return True
    except Exception as e:
        print(f"[Nuke] OCIO Root 설정 오류: {e}")
        return False


def resolve_source_path(args, _frame_in: int) -> str:
    if args.source:
        return args.source
    # 기본 경로 규칙 (스튜디오 상황에 맞게 수정 가능)
    base = f"/storage/projects/comp/{args.shot}/render/####.exr"
    print(f"[Nuke] 소스 경로 미지정, 기본값 사용: {base}")
    return base


def resolve_output_path(args, fmt: str) -> str:
    if args.output:
        return args.output
    
    # 출력 경로가 명시되지 않은 경우 에러 처리
    print("[ERROR] 출력 경로(--output)가 지정되지 않았습니다. UI에서 경로를 확인해 주세요.")
    sys.exit(1)


def main():
    args = parse_args()
    frame_in, frame_out = parse_frame_range(args.frames)
    fmt = (args.format or "exr").lower()

    print("[Nuke] ----------------------------------------")
    print(f"[Nuke] Shot      : {args.shot}")
    print(f"[Nuke] Frames    : {frame_in} - {frame_out}")
    print(f"[Nuke] Format    : {fmt.upper()}")
    print(f"[Nuke] Codec     : {args.codec}")
    print(f"[Nuke] BitDepth  : {args.bitdepth}")
    print(f"[Nuke] Color     : {args.colorspace_in} -> {args.colorspace}")
    print("[Nuke] ----------------------------------------")

    nuke.scriptClear()

    if args.fps and args.fps > 0 and fmt in CONTAINER_FORMATS:
        try:
            nuke.root()["fps"].setValue(args.fps)
            print(f"[Nuke] Root FPS 설정: {args.fps}")
        except Exception as e:
            print(f"[Nuke] FPS 설정 실패 (무시): {e}")

    src_path = resolve_source_path(args, frame_in)
    
    # 소스 파일 존재 여부 엄격하게 체크
    first_frame_path = src_path.replace("####", str(frame_in).zfill(4)).replace("%04d", str(frame_in).zfill(4))
    if not os.path.exists(first_frame_path):
        print(f"[ERROR] 소스 파일을 찾을 수 없습니다: {first_frame_path}")
        sys.exit(1)

    ocio_enabled = configure_ocio_root(args.ocio)

    out_path = resolve_output_path(args, fmt)

    # 1. 가상 스크립트 이름 주입 (Nuke Root 속이기)
    if args.slate:
        base_name = os.path.splitext(os.path.basename(out_path))[0]
        # 버전 치환 로직 (v011 -> v002 등)
        if args.slate_version:
            # v로 시작하고 숫자가 붙은 패턴을 찾아 치환
            new_base_name = re.sub(r"v\d+", args.slate_version, base_name)
            # 만약 치환되지 않았다면 (패턴이 없을 경우) 뒤에 붙임
            if new_base_name == base_name and not re.search(r"v\d+", base_name):
                new_base_name = f"{base_name}_{args.slate_version}"
            base_name = new_base_name

        nk_file = os.path.join(os.path.dirname(out_path), f"{base_name}.nk")
        nuke.root()["name"].setValue(nk_file)
        print(f"[Nuke] 가상 스크립트 이름 주입: {nk_file}")

    read_node = nuke.createNode("Read")
    read_node["file"].setValue(src_path)
    read_node["first"].setValue(frame_in)
    read_node["last"].setValue(frame_out)
    read_node["origfirst"].setValue(frame_in)
    read_node["origlast"].setValue(frame_out)

    if ocio_enabled and args.colorspace_in:
        try:
            read_node["colorspace"].setValue(args.colorspace_in)
            print(f"[Nuke] Read 노드 컬러 설정: {args.colorspace_in}")
        except Exception as e:
            print(f"[Nuke] Read 노드 컬러 설정 실패: {e}")

    # 2. 슬레이트 기즈모 로드 및 연결
    last_node = read_node

    if args.slate and args.project:
        gizmo_file = None
        gizmo_node_name = ""

        gizmo_dir = "/storage/inhouse/env/nuke/NukeShared/Repository/Nodes/MAGO/Slate"
        if os.path.exists(gizmo_dir):
            nuke.pluginAddPath(gizmo_dir)
            gizmo_list = glob.glob(os.path.join(gizmo_dir, f"*{args.project}*.gizmo"))

            for g in gizmo_list:
                if "nocolor" in g:
                    gizmo_file = g
                    break

            if not gizmo_file and gizmo_list:
                gizmo_file = gizmo_list[0]

        if gizmo_file and os.path.exists(gizmo_file):
            gizmo_node_name = os.path.basename(gizmo_file).replace(".gizmo", "")
            try:
                try:
                    nuke.load(gizmo_node_name)
                except Exception as load_e:
                    print(f"[WARN] nuke.load 무시: {load_e}")

                for n in nuke.selectedNodes():
                    n.setSelected(False)

                slate_node = nuke.createNode(gizmo_node_name, inpanel=False)
                slate_node.setInput(0, read_node)

                last_node = slate_node
                print(f"[Nuke] {args.project} 프로젝트 슬레이트 기즈모 적용 성공: {gizmo_node_name}")

            except Exception as e:
                print(f"[ERROR] 슬레이트 노드 생성 실패: {e}")
        else:
            print(f"[ERROR] 프로젝트({args.project})용 슬레이트 기즈모를 찾을 수 없습니다. (경로: {gizmo_dir})")

    write_node = nuke.createNode("Write")
    write_node["file"].setValue(out_path)
    write_node.setInput(0, last_node)
    configure_write_node(write_node, fmt, args.codec, args.bitdepth)

    if ocio_enabled and args.colorspace:
        try:
            write_node["colorspace"].setValue(args.colorspace)
            print(f"[Nuke] Write 노드 컬러 설정: {args.colorspace}")
        except Exception as e:
            print(f"[Nuke] Write 노드 컬러 설정 실패: {e}")

    # 출력 디렉토리 생성
    out_dir = os.path.dirname(out_path.replace("####", "0000"))
    if out_dir and not os.path.exists(out_dir):
        try:
            os.makedirs(out_dir)
            print(f"[Nuke] 출력 디렉토리 생성 완료: {out_dir}")
        except Exception as e:
            print(f"[ERROR] 출력 디렉토리 생성 실패: {e}")
            sys.exit(1)

    total = frame_out - frame_in + 1
    print(f"[Nuke] 렌더링 시작: 총 {total} 프레임")

    try:
        if fmt in CONTAINER_FORMATS:
            print("[Nuke] 동영상 포맷 (MOV) 렌더링 중...")
            nuke.execute(write_node, frame_in, frame_out)
        else:
            for frame in range(frame_in, frame_out + 1):
                nuke.execute(write_node, frame, frame)
                done = frame - frame_in + 1
                pct = int(float(done) / total * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(f"[Nuke] [{bar}] {pct:3d}% | {frame}/{frame_out}", flush=True)
    except Exception as e:
        print(f"[ERROR] 렌더링 중 치명적 오류 발생: {e}")
        sys.exit(1)

    print(f"[SUCCESS] 모든 작업 완료 -> {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"[ERROR] 예외 발생: {e}")
        traceback.print_exc()
        sys.exit(1)
