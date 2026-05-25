"""ShotGrid helpers + OCIO colorspace list (cached)."""

from __future__ import annotations

import os
from typing import Any

try:
    import sgtk
except ImportError:
    sgtk = None

from . import config


def create_sg_connection():  # type: ignore[no-untyped-def]
    try:
        import shotgun_api3
    except ImportError:
        return None

    script_name = os.environ.get("SG_SCRIPT_NAME", "")
    api_key = os.environ.get("SG_API_KEY", "")
    if not script_name or not api_key:
        return None
    try:
        return shotgun_api3.Shotgun(
            config.SG_SERVER,
            script_name=script_name,
            api_key=api_key,
            http_proxy=config.SG_PROXY or None,
        )
    except Exception as e:
        print(f"[SG] 연결 실패: {e}")
        return None


def get_sgtk_output_path(shot_id: int) -> str:
    """SGTK 템플릿 'nuke_shot_render_mono_dpx' 기반으로 아웃풋 경로 계산."""
    if not sgtk:
        return ""

    try:
        # SGTK 엔진 및 컨텍스트 생성
        mgr = sgtk.bootstrap.ToolkitManager()
        # 이미 세션이 있는 경우를 대비해 직접 추상화된 방식으로 접근하거나 프로젝트 기반으로 처리
        # 여기서는 가장 일반적인 path 기반 또는 id 기반 컨텍스트 생성을 가정
        # (실제 환경에 따라 sgtk.sgtk_from_path 등을 사용할 수도 있음)
        sg = create_sg_connection()
        if not sg:
            return ""

        shot = sg.find_one("Shot", [["id", "is", shot_id]], ["project"])
        if not shot:
            return ""

        if not shot.get("project"):
            print(f"[SGTK] 프로젝트 정보가 누락된 샷입니다. (Shot ID: {shot_id})")
            return ""

        tk = sgtk.sgtk_from_entity(shot["project"]["type"], shot["project"]["id"])
        ctx = tk.context_from_entity("Shot", shot_id)
        template = tk.templates.get("nuke_shot_render_mono_dpx")

        if not template:
            return ""

        fields = ctx.as_template_fields(template)
        fields["version"] = 1
        fields["SEQ"] = "####"

        return template.apply_fields(fields).replace("\\", "/")
    except Exception as e:
        print(f"[SGTK] 경로 계산 실패: {e}")
        return ""


def fetch_versions_from_sg(version_ids: list[int]) -> list[dict[str, Any]]:
    sg = create_sg_connection()
    if not sg:
        return []

    fields = [
        "id",
        "code",
        "entity",
        "sg_first_frame",
        "sg_last_frame",
        "sg_path_to_frames",
        "sg_path_to_movie",
        "project",
        "image",
        "sg_status_list",
    ]

    versions = sg.find("Version", [["id", "in", version_ids]], fields)
    results: list[dict[str, Any]] = []
    for v in versions:
        shot_name = ""
        shot_id = 0
        frame_in = v.get("sg_first_frame") or 1001
        frame_out = v.get("sg_last_frame") or 1001
        cs_in = ""
        cs_out = ""

        if v.get("entity"):
            shot_entity = v["entity"]
            shot_id = shot_entity["id"]
            shot_name = shot_entity.get("name", "")
            shot_data = sg.find_one(
                "Shot",
                [["id", "is", shot_id]],
                ["sg_cut_in", "sg_cut_out", "code", "sg_in_plate_colorspace", "sg_out_plate_colorspace"],
            )
            if shot_data:
                frame_in = shot_data.get("sg_cut_in") or frame_in
                frame_out = shot_data.get("sg_cut_out") or frame_out
                shot_name = shot_data.get("code", shot_name)
                cs_in = shot_data.get("sg_in_plate_colorspace") or ""
                cs_out = shot_data.get("sg_out_plate_colorspace") or ""

        # 컬러 폴백 로직: 아웃풋이 비어있으면 인풋 복사
        if cs_in and not cs_out:
            cs_out = cs_in

        output_path = get_sgtk_output_path(shot_id) if shot_id else ""

        results.append(
            {
                "id": v["id"],
                "version_name": v.get("code", f"Version_{v['id']}"),
                "shot_name": shot_name,
                "frame_in": frame_in,
                "frame_out": frame_out,
                "source_path": v.get("sg_path_to_frames", ""),
                "movie_path": v.get("sg_path_to_movie", ""),
                "thumbnail": v.get("image", ""),
                "project": v.get("project", {}).get("name", ""),
                "status": v.get("sg_status_list", ""),
                "output_path": output_path,
                "colorspace_in": cs_in,
                "colorspace_out": cs_out,
            }
        )
    return results


def fetch_shots_from_sg(shot_ids: list[int]) -> list[dict[str, Any]]:
    sg = create_sg_connection()
    if not sg:
        return []

    fields = [
        "id", "code", "sg_cut_in", "sg_cut_out", "project", "image", 
        "sg_status_list", "sg_in_plate_colorspace", "sg_out_plate_colorspace"
    ]
    shots = sg.find("Shot", [["id", "in", shot_ids]], fields)
    results: list[dict[str, Any]] = []
    for s in shots:
        cs_in = s.get("sg_in_plate_colorspace") or ""
        cs_out = s.get("sg_out_plate_colorspace") or ""
        
        if cs_in and not cs_out:
            cs_out = cs_in

        output_path = get_sgtk_output_path(s["id"])

        results.append(
            {
                "id": s["id"],
                "version_name": s.get("code", f"Shot_{s['id']}"),
                "shot_name": s.get("code", ""),
                "frame_in": s.get("sg_cut_in") or 1001,
                "frame_out": s.get("sg_cut_out") or 1001,
                "source_path": "",
                "movie_path": "",
                "thumbnail": s.get("image", ""),
                "project": s.get("project", {}).get("name", ""),
                "status": s.get("sg_status_list", ""),
                "output_path": output_path,
                "colorspace_in": cs_in,
                "colorspace_out": cs_out,
            }
        )
    return results


def load_ocio_colorspaces() -> list[str]:
    colorspaces: list[str] = []
    path = config.OCIO_CONFIG_PATH
    if not os.path.isfile(path):
        print(f"[OCIO] config 없음: {path} — 기본 목록 사용")
        return ["linear", "sRGB", "ACEScg", "ACES2065-1", "rec709"]

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        in_cs_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "colorspaces:":
                in_cs_block = True
                continue
            if in_cs_block:
                if stripped.startswith("- !<ColorSpace>"):
                    continue
                if stripped.startswith("name:"):
                    cs_name = stripped.split("name:", 1)[1].strip()
                    if cs_name:
                        colorspaces.append(cs_name)
                elif stripped.startswith("- ") and not stripped.startswith("- !"):
                    break
    except Exception as e:
        print(f"[OCIO] 파싱 실패: {e}")
        return ["linear", "sRGB", "ACEScg", "ACES2065-1", "rec709"]

    return colorspaces if colorspaces else ["linear", "sRGB", "ACEScg", "ACES2065-1", "rec709"]


_CACHED_COLORSPACES: list[str] | None = None


def get_cached_colorspaces() -> list[str]:
    global _CACHED_COLORSPACES
    if _CACHED_COLORSPACES is None:
        _CACHED_COLORSPACES = load_ocio_colorspaces()
        print(f"[OCIO] {len(_CACHED_COLORSPACES)} colorspaces cached")
    return _CACHED_COLORSPACES
