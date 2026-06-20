# MVER_RENDER

ShotGrid **Action Menu (AMI)** → **FastAPI** → **Nuke** (`-t` / `nuke_converter.py`) → **SSE** 콘솔 로그.

## 요구 사항

- **Linux** 권장: Nuke subprocess는 `setsid` + `killpg`로 끊김 정리(Windows는 `terminate` 폴백).
- Python **3.10+**
- Nuke 14+ (스튜디오 런처 경로는 환경 변수로 지정)
- `pip install -r requirements.txt`  
  **AMI가 POST(form)로 올 때는 `python-multipart`가 필수** (없으면 `request.form()`에서 500).

## 설정

1. `.env.example`을 참고해 서버 환경 변수 설정(또는 셸에서 export).
2. ShotGrid 스크립트 키는 **코드에 넣지 말고** `SG_ENV_FILE`(기본 `…/sg_comp_api`)에  
   `SG_SCRIPT_NAME=…` / `SG_API_KEY=…` 형식으로 보관.
3. **NUKE_CONVERTER** 기본값은 이 레포 루트의 `nuke_converter.py`. 렌더 노드에 레포를 두면 그대로 동작.

실행 (레포 루트에서):

```bash
pip install -r requirements.txt
python -m mago_transcoder
```

AMI URL 예: `http://<서버>:8000/` (프록시 예외에 사내 IP 포함).

## 레이아웃

| 경로 | 설명 |
|------|------|
| `nuke_converter.py` | Nuke 안에서 실행되는 트랜스코더 스크립트 |
| `mago_transcoder/` | FastAPI 앱 (`main.py`), 엔진(`engine.py`), ShotGrid(`shotgrid.py`), UI(`ui_page.html`) |

MXF는 요청에 따라 제외되었습니다. MOV만 컨테이너로 단일 `execute(first,last)` 처리합니다.

---

## GitHub에 `mago_transcoder` 레포로 올리기

1. GitHub에서 **New repository** → 이름 `mago_transcoder` → **Public** 등 선택 → **Create**  
   (README를 추가하지 않아도 됨; 로컬에서 푸시할 예정이면 빈 레포가 편합니다.)

2. 로컬에서 이 레포 디렉터리로 이동한 뒤:

```bash
git init
git branch -M main
git add .
git commit -m "Initial import: MAGO TRANSCODER unified server and Nuke converter"
git remote add origin https://github.com/<YOUR_USER>/mago_transcoder.git
git push -u origin main
```

3. **비밀 정보**(ShotGrid 키, 내부 IP, OCIO 경로)는 커밋하지 말고 `.env`(`.gitignore`에 포함됨) 또는 서버 전용 설정으로만 두세요.

4. 이후 개발은 브랜치 전략 예: `feat/ami-post`, `fix/ocio-cache` 등으로 PR 후 `main` 병합.

SSH 원격을 쓰면 `git@github.com:<YOUR_USER>/mago_transcoder.git` 형태로 `remote add` 하면 됩니다.
