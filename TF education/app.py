from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import re
import io
import math
import asyncio
import difflib
import httpx
import json
import numpy as np
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")

try:
    import openpyxl as _openpyxl
    _XLSX_OK = True
except ImportError:
    _XLSX_OK = False

try:
    import fitz          # pymupdf — PDF 텍스트 추출
    _FITZ_OK = True
except ImportError:
    _FITZ_OK = False

try:
    import pytesseract   # OCR (선택 — 없어도 동작)
    from PIL import Image
    # Windows 기본 설치 경로 자동 설정
    import os as _os
    _tess_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if _os.path.exists(_tess_path):
        pytesseract.pytesseract.tesseract_cmd = _tess_path
    _OCR_OK = True
except ImportError:
    _OCR_OK = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_URL  = "http://121.138.151.6:11434/api/chat"
EMBED_URL   = "http://121.138.151.6:11434/api/embeddings"
MODEL       = "qwen3.6:35b-a3b"
EMBED_MODEL = "bge-m3:latest"
TOP_K          = 3     # 검색해서 모델에 넘길 조각 수 (상위 2~3개)
SIM_THRESHOLD  = 0.3  # 잡음 제거용 낮은 임계값 — 최상위 조각은 미달해도 항상 포함

# ── 매뉴얼 조각 (각 조각 = 독립적으로 임베딩·검색되는 단위) ────────────
DOCS = [
    "1) 폐색 알람(Occlusion / 알람코드 AL-OCC) — p.42\n"
    "증상: 주입 중 \"폐색\" 경고음과 함께 펌프 정지.\n"
    "점검: ① 라인 꺾임·눌림 확인 ② 클램프(잠금) 열림 확인 ③ 필터·삼방활전 막힘 확인.\n"
    "조치: 막힌 원인 해소 후 [재개] 버튼. 해소 후에도 지속되면 압력센서 점검 대상 → 서비스 요청.",

    "2) 압력 상승 경고(High Pressure / 알람코드 AL-PRS) — p.43\n"
    "증상: 주입압이 설정 상한을 넘으면 경고.\n"
    "점검: 카테터 위치·환자 자세·라인 길이 확인, 점도 높은 약액 여부 확인.\n"
    "조치: 원인 제거 후 압력 상한 재설정. 반복되면 압력센서 캘리브레이션 필요(자격 기술자).",

    "3) 배터리 점검·교체 — p.61\n"
    "기준: 완충 후 연속 사용시간 표시 확인. 만충 대비 80% 미만이면 배터리 교체 대상.\n"
    "주의: 정품 배터리만 사용, 임의 분해 금지. 교체 후 충전 사이클 1회 권장.",

    "4) 센서 오류 코드 모음 — p.78\n"
    "E12: 공기방울 감지센서 커넥터 접촉 불량 → 커넥터 재결합 후 전원 재시작.\n"
    "E15: 압력센서 신호 불안정 → 캘리브레이션 필요(자격 기술자 전용).\n"
    "E21: 도어 닫힘 감지 실패 → 도어 래치·자석 위치 확인.\n"
    "E33: 내부 온도 과열 → 통풍구 확인, 30분 냉각 후 재가동.",

    "5) 공기 알람(Air-in-line / 알람코드 AL-AIR) — p.45\n"
    "증상: 라인 내 기포 감지 시 정지.\n"
    "점검: 챔버 액위, 라인 프라이밍 상태, 센서 창 오염 확인.\n"
    "조치: 라인 재프라이밍·기포 제거 후 재개. 센서 창 이물은 마른 천으로 닦기.",

    "6) 세척·소독 절차 — p.90\n"
    "외장: 전원 차단 후 중성세제 적신 천으로 닦고 건조. 분무·침수 금지.\n"
    "소독: 70% 알코올 천 사용 가능, 화면·센서 창은 강한 용제 금지.\n"
    "주기: 환자 교체 시마다 외장 소독 권장.",

    "7) 정기 점검 항목 — p.102\n"
    "일일: 알람음·화면 표시·배터리 잔량 확인.\n"
    "월간: 압력센서 동작, 도어 래치, 충전 상태 점검.\n"
    "연간: 압력센서 캘리브레이션, 누설전류 측정(자격 기술자·점검 기록보관).",

    "8) 작업 안전 원칙 — p.7\n"
    "본 매뉴얼의 점검·조치는 자격 갖춘 기술자 전용. 임의 분해·내부 회로 수리·부품 개조 금지.\n"
    "의심 시 제조사 서비스 요청. 환자 연결 상태에서의 점검은 금지(반드시 라인 분리 후).",
]

# ── 임베딩 캐시 (앱 수명 동안 유지) ──────────────────────────────────
_doc_embeddings: np.ndarray | None = None   # shape (N, D)

# ── 업로드로 추가된 동적 조각 (디스크 영구 저장) ─────────────────────
# key = "장비명|모델명"  (빈 값이면 "default")
import pathlib as _pathlib
_DATA_DIR  = _pathlib.Path(__file__).parent / "data"
_DOCS_FILE = _DATA_DIR / "user_docs.json"
_DATA_DIR.mkdir(exist_ok=True)

def _load_user_docs() -> dict[str, list[str]]:
    if _DOCS_FILE.exists():
        try:
            return json.loads(_DOCS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_user_docs() -> None:
    _DOCS_FILE.write_text(
        json.dumps(_user_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_user_docs: dict[str, list[str]] = _load_user_docs()

def _get_all_docs() -> list[str]:
    """내장 DOCS + 모든 업로드 조각을 펼쳐 반환."""
    result = list(DOCS)
    for chunks in _user_docs.values():
        result.extend(chunks)
    return result

def _device_key(device_name: str, model_name: str) -> str:
    return f"{device_name.strip()}|{model_name.strip()}"

def _get_candidate_indices(device_name: str, model_name: str) -> list[int]:
    """지정 장비/모델의 업로드 조각 + 내장 DOCS 인덱스 반환.
    장비 미지정이면 전체 인덱스."""
    all_docs = _get_all_docs()
    key = _device_key(device_name, model_name)
    if not key.strip("|") or not _user_docs:
        return list(range(len(all_docs)))
    # 내장 DOCS는 항상 포함
    indices = list(range(len(DOCS)))
    offset = len(DOCS)
    for k, chunks in _user_docs.items():
        if k == key:
            indices.extend(range(offset, offset + len(chunks)))
        offset += len(chunks)
    return indices

# ── PDF 문제 해결 섹션 키워드 패턴 ────────────────────────────────────
TROUBLESHOOT_RE = re.compile(
    r"troubleshoot|trouble[\s\-]?shoot|"
    r"문제[\s·]?해결|오류|에러|고장|장애|이상|"
    r"error[\s_]?code|error[\s_]?list|error[\s_]?message|"
    r"alarm|알람|경보|경고|warning|"
    r"E\d{1,3}\b|AL-[A-Z]+|Err[\s]?\d|"
    r"수리|수선|repair|조치|대처|해결|점검|"
    r"fault|failure|malfunction",
    re.IGNORECASE,
)


def build_system_prompt(device_name: str = "", model_name: str = "") -> str:
    """하네스 모드 시스템 프롬프트. 장비/모델 지정 시 해당 장비 한정 문구 포함."""
    device_clause = ""
    if device_name or model_name:
        parts = []
        if device_name: parts.append(f"장비명 '{device_name}'")
        if model_name:  parts.append(f"모델명 '{model_name}'")
        device_clause = (
            f"이 질문은 {', '.join(parts)}에 관한 것이다. "
            f"근거 자료가 해당 장비·모델에 대한 내용인지 반드시 확인하고, "
            f"다른 장비·모델의 자료를 이 장비에 적용하지 말라. "
            f"자료에 해당 장비·모델 정보가 없으면 '자료에서 확인되지 않습니다'라고만 답하라.\n"
        )
    return (
        "아래 근거 자료에 질문의 답이 실제로 있으면 출처와 함께 답하고, "
        "자료에 답이 없으면 추측하지 말고 '자료에서 확인되지 않습니다'라고만 답하라.\n"
        + device_clause +
        "너는 의료기기 수리 매뉴얼 안내 도우미다. "
        "반드시 아래 '참고 자료(매뉴얼 발췌)'에 명시된 내용만 근거로 답한다. "
        "자료에 없는 모델명·증상·알람 코드·오류 코드·절차는 예외 없이 '자료에서 확인되지 않습니다'라고만 답한다. "
        "알람 코드 또는 오류 코드가 자료 목록에 없으면 '미수록 코드 — 해당 코드는 제공된 발췌에 수록되어 있지 않습니다.'라고 명시한다.\n"
        "자료에 있는 내용을 답할 때: 증상을 물으면 '증상 → 점검 → 조치' 순서로 정리한다. "
        "답변 끝에 '근거: <항목명> (p.OO)' 형식으로 출처를 표기하고, 근거가 여러 개면 쉼표로 나열한다.\n"
        "임의 분해·직접 수리·내부 회로 수리를 묻는 경우 절차를 알려주지 않고 "
        "'해당 작업은 자격 기술자 또는 제조사 서비스 대상입니다. 임의 분해·수리는 금지되어 있습니다.'라고만 답한다.\n"
        "모든 답변의 맨 마지막에 반드시 다음 안전 고지를 한 번 덧붙인다: "
        "'⚠️자격 기술자 전용 — 임의 분해·내부 수리 금지, 환자 연결 상태 점검 금지, 의심 시 제조사 서비스 요청.'"
    )

VIBE_SYSTEM_PROMPT = (
    "너는 의료기기 수리 전문 도우미다. "
    "제공된 참고 자료 없이 모델 자체 지식으로 한국어로 자유롭게 답한다. "
    "답변 끝에 '⚡ 바이브 모드: 검색 없이 모델 지식으로 답변'이라고 명시한다."
)

RECALL_SYSTEM_PROMPT = (
    "아래 [리콜대조결과]에 적힌 숫자와 로트만 인용해 한국어로 한두 줄로 답하라. "
    "결과에 없는 로트·건수를 지어내지 마라."
)

# ── 식약처 회수·판매중지 API (IROS_16 v1.1 기준) ────────────────────
# 참고문서: 오퍼레이션명은 getItemNameList / getSerialNumList 등
# 주의: 서비스명에 숫자 1 포함(소문자 l 아님). 파라미터는 serviceKey (소문자 s)

# 조회된 회수 목록 메모리 캐시 (매칭 시 재조회 생략)
_recall_cache: list[dict] = []
_recall_cache_meta: dict  = {}   # {"total_count": n, "fetched": n, "filters": {...}}
MFDS_API_KEY  = "97ec72c17de0c92cdb0946f294aef4498c2bb6f6e0d56eaf3c5c35f727e60692"
# [수정 2026-07-01] 경로명 오타 정정: Rtrv1S1e → RtrvlSle (l↔1 혼동). 이게 HTTP 500 "Unexpected errors"의 원인.
# 데이터셋 15056785 '식약처_의료기기 회수·판매중지정보'. 오퍼레이션은 getItemNameList01/getSerialNumList01(이미 정확).
MFDS_API_BASE = "https://apis.data.go.kr/1471000/MdlpRtrvlSleStpgeInfoService02"

# ── getItemNameList 응답 필드 (문서 기준) ──────────────────────────────
# ITEM_NAME, RECALL_ITEM_SEQ, DEPT_RECEIPT_NO,
# REPORT_STATE_CODE, REPORT_STATE_NAME, REPORT_SUBMIT_DATE,
# REPORT_KIND_CODE, REPORT_KIND_NAME, MEDDEV_ITEM_SEQ, MEA_CLASS_NAME
#
# getSerialNumList 응답 필드:
# SERIAL_NUM(제조번호), RECALL_ITEM_SEQ, REPORT_SUBMIT_DATE 등

async def _mfds_call(client: httpx.AsyncClient, op: str, extra: dict | None = None,
                     page: int = 1, rows: int = 100) -> dict:
    """식약처 API 단일 페이지 호출. 응답 body dict 반환."""
    # ServiceKey를 URL에 직접 삽입 (공공데이터포털 이중인코딩 방지)
    qs = f"serviceKey={MFDS_API_KEY}&pageNo={page}&numOfRows={rows}&type=json"
    if extra:
        for k, v in extra.items():
            qs += f"&{k}={v}"
    url = f"{MFDS_API_BASE}/{op}?{qs}"
    logger.debug("▶ MFDS 요청: %s", url.replace(MFDS_API_KEY, "***KEY***"))
    res = await client.get(url)
    logger.debug("◀ MFDS 응답: HTTP %s | %d bytes | %s",
                 res.status_code, len(res.content), res.text[:300])
    if not res.is_success:
        raise RuntimeError(f"[HTTP {res.status_code}] {res.text}")
    try:
        data = res.json()
    except Exception:
        txt = res.text
        if "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in txt:
            raise RuntimeError("API 키가 등록되지 않았습니다. 공공데이터포털에서 활용신청을 완료하세요.")
        raise RuntimeError(f"응답 파싱 실패: {txt[:300]}")
    return data.get("body", {})

async def _mfds_all_pages(op: str, extra: dict | None = None,
                          max_items: int = 1000, rows_per_page: int = 100) -> tuple[list[dict], int]:
    """식약처 API 최신 max_items건 조회.
    1페이지로 totalCount를 파악한 뒤, 마지막 페이지부터 역산해
    가장 최신 데이터가 담긴 페이지부터 순차 조회한다.
    """
    def _parse(body: dict) -> list[dict]:
        raw = body.get("items", [])
        if isinstance(raw, dict):
            raw = [raw]
        return [{k.upper(): str(v or "").strip() for k, v in it.get("item", it).items()} for it in raw]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ① 1페이지로 totalCount 파악
        first_body   = await _mfds_call(client, op, extra, 1, rows_per_page)
        total_count  = int(first_body.get("totalCount", 0) or 0)
        if total_count == 0:
            return [], 0

        # ② 최신 max_items건을 포함하는 시작 페이지 계산
        # +1: 마지막 페이지가 rows_per_page 미만일 수 있으므로 한 페이지 여유를 둠
        total_pages  = math.ceil(total_count / rows_per_page)
        pages_needed = math.ceil(min(max_items, total_count) / rows_per_page) + 1
        start_page   = max(1, total_pages - pages_needed + 1)
        logger.debug("totalCount=%d totalPages=%d startPage=%d", total_count, total_pages, start_page)

        # ③ start_page ~ 마지막 페이지 순차 조회 (1페이지는 이미 수신했으면 재사용)
        all_items: list[dict] = []
        for page in range(start_page, total_pages + 1):
            body  = first_body if page == 1 else await _mfds_call(client, op, extra, page, rows_per_page)
            items = _parse(body)
            if not items:
                break
            all_items.extend(items)

    # ④ 페이지 경계로 인해 max_items 초과 시 가장 최신(뒤쪽)만 유지
    if len(all_items) > max_items:
        all_items = all_items[-max_items:]

    return all_items, total_count

def _sim(a: str, b: str) -> float:
    a = re.sub(r"\s+", " ", str(a or "")).strip().lower()
    b = re.sub(r"\s+", " ", str(b or "")).strip().lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def _score_asset_vs_recall(asset: dict, recall: dict,
                            serial_hit_set: set[str] | None = None) -> tuple[int, list[str]]:
    """자산 1건 vs 회수 항목 1건 → (점수, 근거 목록).
    점수 체계:
      제조번호 일치   = +100 (최우선 — serial_hit_set 또는 recall의 MAKE_NO 직접 비교)
      품목명 유사도   =  0~50
      분류명 유사도   =  0~20  (MEA_CLASS_NAME)
      합산 최대 170점, 높은 가능성: ≥70, 검토 필요: 30~69
    """
    score, reasons = 0, []

    # 1) 제조번호 일치 (최우선 100점)
    a_serial = re.sub(r"\s+", "", str(asset.get("제조번호", ""))).lower()
    r_serial  = re.sub(r"\s+", "", str(recall.get("MAKE_NO", ""))).lower()
    serial_match = False
    if a_serial:
        if (r_serial and a_serial == r_serial):
            serial_match = True
        elif serial_hit_set and a_serial in serial_hit_set:
            serial_match = True
    if serial_match:
        score += 100
        reasons.append(f"제조번호 일치 ({asset.get('제조번호','')})")

    # 2) 품목명 유사도 (0-50점) — ITEM_NAME vs 한글명칭
    nr = _sim(asset.get("한글명칭", ""), recall.get("ITEM_NAME", ""))
    ns = int(nr * 50)
    if ns >= 5:
        score += ns
        reasons.append(f"품목명 유사도 {int(nr*100)}%")

    # 3) 분류명 유사도 (0-20점) — MEA_CLASS_NAME vs 한글명칭(보조)
    cr = _sim(asset.get("한글명칭", ""), recall.get("MEA_CLASS_NAME", ""))
    cs = int(cr * 20)
    if cs >= 5:
        score += cs
        reasons.append(f"분류명 유사도 {int(cr*100)}%")

    return score, reasons

def _parse_excel_assets(xlsx_bytes: bytes) -> list[dict]:
    if not _XLSX_OK:
        raise RuntimeError("openpyxl 패키지가 필요합니다: pip install openpyxl")
    wb = _openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb.active
    headers = [str(cell.value or "").strip() for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    assets = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        assets.append({headers[i]: (str(v).strip() if v is not None else "")
                       for i, v in enumerate(row) if i < len(headers)})
    return assets


# ── 임베딩 유틸 ──────────────────────────────────────────────────────
async def embed(text: str) -> np.ndarray:
    """단일 텍스트를 bge-m3:latest로 임베딩해 1-D numpy 배열로 반환."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text})
        res.raise_for_status()
        vec = res.json()["embedding"]   # 단수 키
    return np.array(vec, dtype=np.float32)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


async def ensure_doc_embeddings() -> np.ndarray:
    """DOCS + _user_docs 임베딩을 캐시. 문서 수가 달라지면 재계산."""
    global _doc_embeddings
    all_docs = _get_all_docs()
    if _doc_embeddings is None or _doc_embeddings.shape[0] != len(all_docs):
        vecs = [await embed(doc) for doc in all_docs]
        _doc_embeddings = np.stack(vecs)   # (N, D)
    return _doc_embeddings


def _parse_source(first_line: str) -> dict:
    """조각 첫 줄에서 title·page 분리. 기본 형식 + 업로드 형식 모두 지원."""
    # 기본 형식: "N) 조각명 — p.OO"
    m = re.match(r'^\d+\)\s*(.+?)\s*[—\-]+\s*(p\.\d+)', first_line)
    if m:
        return {"title": m.group(1).strip(), "page": m.group(2).strip()}
    # 업로드 형식: "[업로드 자료 p.OO]"
    m2 = re.match(r'^\[(.+?)\]', first_line)
    if m2:
        inner = m2.group(1).strip()
        pg = re.search(r'p\.(\d+)', inner)
        return {"title": inner, "page": f"p.{pg.group(1)}" if pg else ""}
    return {"title": first_line.strip(), "page": ""}


async def retrieve(
    question: str,
    device_name: str = "",
    model_name: str = "",
    top_k: int = TOP_K,
) -> tuple[list[str], list[dict]]:
    """장비/모델 필터 + 코사인 유사도 상위 top_k 조각 반환.
    SIM_THRESHOLD 미만 조각은 제외하되 최상위 1개는 항상 포함.
    장비명/모델명을 쿼리에 포함해 RAG 정확도 향상.
    """
    all_docs    = _get_all_docs()
    doc_vecs    = await ensure_doc_embeddings()
    candidates  = _get_candidate_indices(device_name, model_name)

    # 장비명·모델명을 쿼리에 합쳐 임베딩 → 관련 조각이 상위에 올라옴
    search_q = " ".join(filter(None, [device_name, model_name, question]))
    q_vec    = await embed(search_q)

    sims   = {i: cosine_sim(q_vec, doc_vecs[i]) for i in candidates}
    ranked = sorted(candidates, key=lambda i: sims[i], reverse=True)

    selected = []
    for rank, idx in enumerate(ranked):
        if rank == 0:
            selected.append(idx)
        elif sims[idx] >= SIM_THRESHOLD:
            selected.append(idx)
        if len(selected) >= top_k:
            break

    chunks  = [all_docs[i] for i in selected]
    sources = [_parse_source(all_docs[i].splitlines()[0]) for i in selected]
    return chunks, sources


# ── Pydantic 모델 ─────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    context: str = ""       # 프론트 전달값 — RAG가 대체하므로 백엔드에서 무시
    bare: bool = False      # True = 바이브, False = 하네스
    device_name: str = ""   # 장비명 (예: 주입펌프)
    model_name:  str = ""   # 모델명 (예: DI-2200P)


class DeviceItem(BaseModel):
    model: str
    lot: str


class RecallItem(BaseModel):
    model: str
    lot: str
    reason: str


class RecallCheckRequest(BaseModel):
    inventory: list[DeviceItem]
    recall_notice: list[RecallItem]


# ── 스트리밍 헬퍼 ─────────────────────────────────────────────────────
async def stream_ollama(question: str, context: str, system_prompt: str):
    """토큰만 흘림 — [DONE] 은 호출부에서 발행 (sources 이벤트 순서 제어 목적)."""
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"참고 자료:\n{context}\n\n질문: {question}"},
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data  = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    # done 플래그는 여기서 처리하지 않음 — 호출부에서 [DONE] 발행
                except json.JSONDecodeError:
                    continue


async def stream_recall(context: str):
    """토큰만 흘림 — [DONE] 은 호출부에서 발행."""
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": RECALL_SYSTEM_PROMPT},
            {"role": "user",   "content": context},
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data  = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                except json.JSONDecodeError:
                    continue


# ── 엔드포인트 ────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def sse_harness():
        # 토큰 → sources 이벤트 → [DONE]  (이 순서를 지켜야 프론트가 sources를 읽음)
        chunks, sources = await retrieve(
            req.question, req.device_name, req.model_name, top_k=TOP_K
        )
        context     = "\n\n".join(chunks)
        sys_prompt  = build_system_prompt(req.device_name, req.model_name)
        async for chunk in stream_ollama(req.question, context, sys_prompt):
            yield chunk
        yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    async def sse_bare():
        # 바이브: 토큰 → [DONE] (sources 없음)
        payload = {
            "model": MODEL,
            "stream": True,
            "messages": [
                {"role": "system", "content": VIBE_SYSTEM_PROMPT},
                {"role": "user",   "content": req.question},
            ],
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data  = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield f"data: {json.dumps({'token': token})}\n\n"
                    except json.JSONDecodeError:
                        continue
        yield "data: [DONE]\n\n"

    sse_gen = sse_bare() if req.bare else sse_harness()
    return StreamingResponse(
        sse_gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/recall-check")
async def recall_check(req: RecallCheckRequest):
    # ── 파이썬이 직접 매칭 (모델에게 시키지 않음) ───────────────────────
    recall_keys: dict[tuple[str, str], str] = {
        (r.model.strip(), r.lot.strip()): r.reason.strip()
        for r in req.recall_notice
    }
    matched, unmatched = [], []
    for item in req.inventory:
        key = (item.model.strip(), item.lot.strip())
        if key in recall_keys:
            matched.append({"model": item.model, "lot": item.lot,
                            "reason": recall_keys[key]})
        else:
            unmatched.append({"model": item.model, "lot": item.lot})

    total, n_match, n_safe = len(req.inventory), len(matched), len(unmatched)
    matched_lots   = ", ".join(f"{m['lot']}({m['reason']})" for m in matched) or "없음"
    unmatched_lots = ", ".join(u["lot"] for u in unmatched) or "없음"
    context = (
        f"[리콜대조결과]\n"
        f"전체 보유 기기: {total}건\n"
        f"리콜 대상: {n_match}건 — 로트 {matched_lots}\n"
        f"대상 아님: {n_safe}건 — 로트 {unmatched_lots}\n"
        f"(이 숫자와 로트는 파이썬 코드가 계산한 확정값임)"
    )

    async def stream_with_meta():
        meta = {
            "type": "meta",
            "total": total, "matched": n_match, "safe": n_safe,
            "matched_lots": [m["lot"] for m in matched],
            "safe_lots":    [u["lot"] for u in unmatched],
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
        async for chunk in stream_recall(context):
            yield chunk
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_with_meta(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 식약처 회수 목록 조회 ─────────────────────────────────────────────
@app.get("/api/mfds/recall-list")
async def mfds_recall_list(
    date_from: str = "",   # YYYYMMDD — 보고일자 시작
    date_to:   str = "",   # YYYYMMDD — 보고일자 종료
    status:    str = "전체"  # 전체 / 진행중 / 종료
):
    """식약처 getItemNameList 전체 조회 + 필터 + 캐시 저장."""
    global _recall_cache, _recall_cache_meta
    logger.info("==== /api/mfds/recall-list | date_from=%s date_to=%s status=%s ====",
                date_from, date_to, status)
    try:
        # 회수 품목 목록 + 업체 목록 병렬 조회
        (items, total_count), (co_items, _) = await asyncio.gather(
            _mfds_all_pages("getItemNameList01"),
            _mfds_all_pages("getCompanyNameList01"),
        )

        # 업체명 룩업: (MEDDEV_ENTP_SEQ, REPORT_SUBMIT_DATE) → ENTP_NAME
        co_lookup: dict[tuple, str] = {
            (c.get("MEDDEV_ENTP_SEQ", ""), c.get("REPORT_SUBMIT_DATE", "")): c.get("ENTP_NAME", "")
            for c in co_items
        }
        for item in items:
            key = (item.get("MEDDEV_ENTP_SEQ", ""), item.get("REPORT_SUBMIT_DATE", ""))
            item["ENTP_NAME"] = co_lookup.get(key, "")

        # 클라이언트 사이드 필터
        if date_from:
            items = [i for i in items if i.get("REPORT_SUBMIT_DATE", "")[:8] >= date_from]
        if date_to:
            items = [i for i in items if i.get("REPORT_SUBMIT_DATE", "")[:8] <= date_to]
        if status == "진행중":
            items = [i for i in items if i.get("RECALL_REPORT_NAME", "") == "계획보고"]
        elif status == "종료":
            items = [i for i in items if i.get("RECALL_REPORT_NAME", "") == "종료보고"]

        items.sort(key=lambda x: x.get("REPORT_SUBMIT_DATE", ""), reverse=True)

        # 캐시 저장
        _recall_cache = items
        _recall_cache_meta = {
            "total_count": total_count,
            "fetched": len(items),
            "filters": {"date_from": date_from, "date_to": date_to, "status": status},
        }
        logger.info("캐시 저장 완료: %d건", len(items))
        return {"ok": True, "total_count": total_count, "fetched": len(items), "items": items}
    except Exception as e:
        logger.error("recall-list 오류: %s", e)
        return {"ok": False, "error": str(e)}


# ── 회수 항목 상세조회 (캐시에서 즉시 반환) ──────────────────────────
@app.get("/api/mfds/recall-detail")
async def mfds_recall_detail(dept_no: str = ""):
    """캐시된 회수 목록에서 DEPT_RECEIPT_NO로 상세 정보 반환."""
    if not dept_no:
        return {"ok": False, "error": "dept_no 파라미터 필요"}
    item = next((i for i in _recall_cache if i.get("DEPT_RECEIPT_NO") == dept_no), None)
    if not item:
        return {"ok": False, "error": "해당 항목을 캐시에서 찾을 수 없습니다."}
    return {"ok": True, "item": item}


# ── 병원 자산 엑셀 업로드 + 회수 매칭 분석 ──────────────────────────
@app.post("/api/mfds/match")
async def mfds_match(file: UploadFile = File(...)):
    """엑셀 자산 파일 업로드 → 식약처 API 매칭 분석."""
    logger.info("==== /api/mfds/match 호출 | 파일명: %s ====", file.filename)

    # 1. 엑셀 파싱
    try:
        xlsx_bytes = await file.read()
        assets = _parse_excel_assets(xlsx_bytes)
    except RuntimeError as e:
        logger.error("엑셀 파싱 실패: %s", e)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.error("엑셀 파싱 예외: %s", e)
        return {"ok": False, "error": f"엑셀 파싱 오류: {e}"}

    logger.info("엑셀 파싱 완료: 자산 %d건", len(assets))
    if not assets:
        return {"ok": False, "error": "엑셀에서 자산 데이터를 읽을 수 없습니다."}

    # 2a. 캐시 우선 사용 — 없으면 API 신규 조회
    if _recall_cache:
        recall_items = _recall_cache
        total_recall = _recall_cache_meta.get("total_count", len(recall_items))
        filters = _recall_cache_meta.get("filters", {})
        logger.info("캐시 사용: %d건 (필터: %s)", len(recall_items), filters)
    else:
        logger.info("캐시 없음 — 식약처 getItemNameList01 신규 조회")
        try:
            recall_items, total_recall = await _mfds_all_pages("getItemNameList01")
            logger.info("getItemNameList01 완료: 전체 %d건 중 %d건 수신", total_recall, len(recall_items))
        except Exception as e:
            logger.error("getItemNameList01 오류: %s", e)
            return {"ok": False, "error": f"식약처 API 오류: {e}"}

    # 2b. 제조번호 히트셋 구성
    serial_hit: set[str] = set()
    unique_serials = {
        re.sub(r"\s+", "", str(a.get("제조번호", ""))).lower()
        for a in assets
        if str(a.get("제조번호", "")).strip()
    }
    logger.info("제조번호 조회 대상: %d건 %s", len(unique_serials), list(unique_serials)[:5])
    if unique_serials:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for serial in unique_serials:
                    logger.debug("getSerialNumList01 조회: make_no=%s", serial)
                    body = await _mfds_call(client, "getSerialNumList01",
                                            extra={"make_no": serial}, rows=10)
                    its = body.get("items", [])
                    if isinstance(its, dict):
                        its = [its]
                    if its:
                        serial_hit.add(serial)
                        logger.info("제조번호 히트: %s", serial)
        except Exception as e:
            logger.warning("getSerialNumList01 오류 (무시): %s", e)
    logger.info("제조번호 히트셋: %s", serial_hit)

    # REPORT_SUBMIT_DATE 내림차순 정렬
    recall_items.sort(key=lambda x: x.get("REPORT_SUBMIT_DATE", ""), reverse=True)

    # 3. 자산 × 회수 항목 매칭 (자산당 최고 점수 회수 항목 1건)
    HIGH_THRESHOLD   = 70
    REVIEW_THRESHOLD = 30

    high_list, review_list = [], []

    for asset in assets:
        best_score, best_recall, best_reasons = 0, None, []
        for recall in recall_items:
            sc, rsn = _score_asset_vs_recall(asset, recall, serial_hit)
            if sc > best_score:
                best_score, best_recall, best_reasons = sc, recall, rsn

        if best_score < REVIEW_THRESHOLD or best_recall is None:
            continue

        row = {
            "자산번호":     asset.get("자산번호", ""),
            "관리부서명":   asset.get("관리부서명", ""),
            "사용자":       asset.get("사용자", ""),
            "한글명칭":     asset.get("한글명칭", ""),
            "모델명":       asset.get("모델명", ""),
            "제조번호":     asset.get("제조번호", ""),
            "취득일자":     asset.get("취득일자", ""),
            "제조사":       asset.get("제조사", ""),
            "공급사":       asset.get("공급사", ""),
            "회수품목명":   best_recall.get("ITEM_NAME", ""),
            "회수분류명":   best_recall.get("MEA_CLASS_NAME", ""),
            "부서접수번호": best_recall.get("DEPT_RECEIPT_NO", ""),
            "회수보고구분": best_recall.get("REPORT_KIND_NAME", ""),
            "보고상태":     best_recall.get("REPORT_STATE_NAME", ""),
            "보고일자":     best_recall.get("REPORT_SUBMIT_DATE", ""),
            "점수":         best_score,
            "근거":         " / ".join(best_reasons),
        }
        if best_score >= HIGH_THRESHOLD:
            high_list.append(row)
        else:
            review_list.append(row)

    # 보고일자 내림차순 정렬
    high_list.sort(key=lambda x: x.get("보고일자", ""), reverse=True)
    review_list.sort(key=lambda x: x.get("보고일자", ""), reverse=True)

    return {
        "ok": True,
        "asset_count":  len(assets),
        "recall_count": total_recall,
        "fetched_recall": len(recall_items),
        "high_count":   len(high_list),
        "review_count": len(review_list),
        "high":   high_list,
        "review": review_list,
    }


# ── PDF 파싱 헬퍼 ─────────────────────────────────────────────────────
def _page_to_text_ocr(page) -> str:
    """pymupdf 페이지 → 텍스트. 스캔본이면 OCR 시도."""
    text = page.get_text().strip()
    if len(text) >= 100:
        return text
    # 텍스트가 희박 → 스캔 이미지로 판단, OCR 시도
    if _OCR_OK:
        try:
            mat = fitz.Matrix(2.0, 2.0)   # 2× 확대로 OCR 정확도 향상
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang="kor+eng").strip()
        except Exception:
            pass
    return text


def _extract_pdf_pages(pdf_bytes: bytes) -> tuple[list[tuple[int, str]], bool]:
    """PDF 전 페이지에서 (page_num, text) 목록 반환. OCR 사용 여부도 반환."""
    if not _FITZ_OK:
        raise RuntimeError("pymupdf(fitz) 패키지가 설치되지 않았습니다: pip install pymupdf")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages, ocr_used = [], False
    for i, page in enumerate(doc, 1):
        raw = page.get_text().strip()
        if len(raw) < 100 and _OCR_OK:
            ocr_used = True
        text = _page_to_text_ocr(page)
        if text:
            pages.append((i, text))
    doc.close()
    return pages, ocr_used


def _filter_troubleshoot_chunks(
    pages: list[tuple[int, str]],
    device_name: str = "",
    model_name: str = "",
) -> list[str]:
    """문제 해결·오류 코드 관련 페이지만 조각으로 추출. 장비/모델 태그 포함."""
    tag_parts = []
    if device_name: tag_parts.append(f"장비: {device_name}")
    if model_name:  tag_parts.append(f"모델: {model_name}")
    tag = " / ".join(tag_parts)

    chunks = []
    for page_num, text in pages:
        if not TROUBLESHOOT_RE.search(text):
            continue
        prefix = f"[{tag} — p.{page_num}]" if tag else f"[업로드 자료 p.{page_num}]"
        if len(text) <= 800:
            chunks.append(f"{prefix}\n{text}")
            continue
        paragraphs = re.split(r"\n{2,}", text)
        bucket: list[str] = []
        for para in paragraphs:
            bucket.append(para)
            if len("\n\n".join(bucket)) > 600:
                chunk_text = "\n\n".join(bucket).strip()
                if chunk_text:
                    chunks.append(f"{prefix}\n{chunk_text}")
                bucket = []
        if bucket:
            chunk_text = "\n\n".join(bucket).strip()
            if chunk_text:
                chunks.append(f"{prefix}\n{chunk_text}")
    return chunks


@app.post("/api/upload-pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    device_name: str = Form(""),
    model_name:  str = Form(""),
):
    global _user_docs, _doc_embeddings
    if not file.filename.lower().endswith(".pdf"):
        return {"ok": False, "error": "PDF 파일(.pdf)만 업로드 가능합니다."}
    pdf_bytes = await file.read()
    try:
        pages, ocr_used = _extract_pdf_pages(pdf_bytes)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"PDF 파싱 오류: {e}"}

    chunks = _filter_troubleshoot_chunks(pages, device_name, model_name)
    if not chunks:
        return {
            "ok": False,
            "error": (
                "문제 해결·오류 코드 관련 내용을 찾을 수 없습니다. "
                "파일에 Troubleshooting / Error Code / 문제 해결 섹션이 포함되어 있는지 확인하세요."
            ),
            "pages_processed": len(pages),
            "ocr_used": ocr_used,
        }

    key = _device_key(device_name, model_name) or "default"
    _user_docs[key] = chunks    # 해당 장비/모델 업로드 대체 (다른 장비는 보존)
    _doc_embeddings = None      # 캐시 무효화 → 다음 질문 시 재임베딩
    _save_user_docs()           # 디스크에 영구 저장

    return {
        "ok": True,
        "filename": file.filename,
        "device_name": device_name,
        "model_name": model_name,
        "pages_processed": len(pages),
        "chunks_extracted": len(chunks),
        "ocr_used": ocr_used,
        "preview": chunks[:3],
    }


@app.post("/api/reset-docs")
async def reset_docs(
    device_name: str = Form(""),
    model_name:  str = Form(""),
):
    """업로드 자료 초기화. 장비/모델 지정 시 해당 것만, 미지정 시 전체."""
    global _user_docs, _doc_embeddings
    key = _device_key(device_name, model_name)
    if key.strip("|"):
        _user_docs.pop(key, None)
        msg = f"'{device_name} {model_name}' 업로드 자료가 초기화되었습니다."
    else:
        _user_docs.clear()
        msg = "모든 업로드 자료가 초기화되었습니다. 내장 매뉴얼만 사용합니다."
    _doc_embeddings = None
    _save_user_docs()           # 디스크에 반영
    return {"ok": True, "message": msg}


@app.get("/health")
async def health():
    # 캐시 상태도 함께 반환
    return {"status": "ok", "docs_cached": _doc_embeddings is not None}


@app.get("/api/debug-docs")
async def debug_docs():
    """업로드된 문서 키·조각 수·첫 200자 미리보기 반환 (디버그용)."""
    return {
        "user_doc_keys": list(_user_docs.keys()),
        "user_doc_chunk_counts": {k: len(v) for k, v in _user_docs.items()},
        "previews": {
            k: [c[:200] for c in v[:3]]
            for k, v in _user_docs.items()
        },
    }


@app.post("/api/debug-retrieve")
async def debug_retrieve(req: ChatRequest):
    """질문에 대해 실제로 선택된 청크와 유사도를 반환 (디버그용)."""
    all_docs   = _get_all_docs()
    doc_vecs   = await ensure_doc_embeddings()
    candidates = _get_candidate_indices(req.device_name, req.model_name)
    search_q   = " ".join(filter(None, [req.device_name, req.model_name, req.question]))
    q_vec      = await embed(search_q)
    sims       = {i: float(cosine_sim(q_vec, doc_vecs[i])) for i in candidates}
    ranked     = sorted(candidates, key=lambda i: sims[i], reverse=True)[:TOP_K * 2]
    return {
        "search_query": search_q,
        "candidates_total": len(candidates),
        "top_results": [
            {"index": i, "sim": round(sims[i], 4), "preview": all_docs[i][:200]}
            for i in ranked
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)




