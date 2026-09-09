from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import re
import io
import httpx
import json
import numpy as np
import os
import uuid as _uuid
import logging
import pathlib as _pathlib
from datetime import datetime
from urllib.parse import quote as _urllib_quote

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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_LLM_BASE   = os.getenv("LLM_BASE",    "http://121.138.151.6:11500")
OLLAMA_URL  = f"{_LLM_BASE}/api/chat"
EMBED_URL   = f"{_LLM_BASE}/api/embeddings"
MODEL       = os.getenv("LLM_MODEL",   "qwen3.6:35b-a3b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
TOP_K          = 3     # 검색해서 모델에 넘길 조각 수 (상위 2~3개)
SIM_THRESHOLD  = 0.3  # 잡음 제거용 낮은 임계값 — 최상위 조각은 미달해도 항상 포함

# ── 임베딩 캐시 (앱 수명 동안 유지) ──────────────────────────────────
_doc_embeddings: np.ndarray | None = None   # shape (N, D)

# ── 업로드된 구조화 자료 (엑셀 표준 양식 → 행 단위 저장, 디스크 영구 저장) ──
# key = "장비명|모델명"  (빈 값이면 "default")
# 각 행: {"symptom": str, "cause": str, "solution": str, "note": str}
_DATA_DIR  = _pathlib.Path(__file__).parent / "data"
_DOCS_FILE = _DATA_DIR / "user_docs.json"
_DATA_DIR.mkdir(exist_ok=True)

DOC_COLUMNS = ["증상", "원인", "해결방법", "비고"]
DOC_SHEET_NAME = "메뉴얼"

def _load_user_docs() -> dict[str, list[dict]]:
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

_user_docs: dict[str, list[dict]] = _load_user_docs()

# ── 계정 관리 (사용자 / 관리자 / 마스터 공용) ────────────────────────
# TODO: 운영 전환 시 비밀번호를 해시+외부 설정으로 이전할 것
_ADMIN_FILE = _DATA_DIR / "admins.json"

def _load_admins() -> dict[str, dict]:
    if _ADMIN_FILE.exists():
        try:
            return json.loads(_ADMIN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"2022137": {"password": "admin1234", "role": "master", "name": "김창진",
                         "dept": "", "must_change_password": False}}

def _save_admins() -> None:
    _ADMIN_FILE.write_text(
        json.dumps(_admin_store, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_admin_store: dict[str, dict] = _load_admins()
_admin_sessions: dict[str, dict] = {}  # {token: {admin_id, role}}

# ── 사용 권한 요청 (계정이 없는 사용자가 로그인 화면에서 제출, 디스크 영속) ──
_ACCESS_REQ_FILE = _DATA_DIR / "access_requests.json"

def _load_access_requests() -> list[dict]:
    if _ACCESS_REQ_FILE.exists():
        try:
            return json.loads(_ACCESS_REQ_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_access_requests() -> None:
    _ACCESS_REQ_FILE.write_text(
        json.dumps(_access_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_access_requests: list[dict] = _load_access_requests()

# ── 비밀번호 초기화 요청 (파일 영구 저장) ────────────────────────────
_RESET_FILE = _DATA_DIR / "reset_requests.json"

def _load_reset_requests() -> list[dict]:
    if _RESET_FILE.exists():
        try:
            return json.loads(_RESET_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_reset_requests() -> None:
    _RESET_FILE.write_text(
        json.dumps(_reset_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_reset_requests: list[dict] = _load_reset_requests()

# ── 매뉴얼 삭제 요청 (디스크 영속) ──────────────────────────────────
_DOC_DELETE_FILE = _DATA_DIR / "doc_delete_requests.json"

def _load_doc_delete_requests() -> list:
    try:
        return json.loads(_DOC_DELETE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def _save_doc_delete_requests() -> None:
    _DOC_DELETE_FILE.write_text(
        json.dumps(_doc_delete_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_doc_delete_requests: list[dict] = _load_doc_delete_requests()

# ── 미해결 질문 로그 (자료에서 답을 찾지 못한 질문 → 마스터 검토용, 디스크 영속) ──
_UNANSWERED_FILE = _DATA_DIR / "unanswered_questions.json"
_MAX_UNANSWERED  = 500

def _load_unanswered_questions() -> list[dict]:
    if _UNANSWERED_FILE.exists():
        try:
            return json.loads(_UNANSWERED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_unanswered_questions() -> None:
    _UNANSWERED_FILE.write_text(
        json.dumps(_unanswered_questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

_unanswered_questions: list[dict] = _load_unanswered_questions()

def _log_unanswered_question(device_name: str, model_name: str, question: str, answer: str) -> None:
    """RAG가 자료에서 답을 찾지 못한 대화를 전체 저장 — 추후 매뉴얼 보강 검토용."""
    entry = {
        "id":          str(_uuid.uuid4()),
        "ts":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "device_name": device_name,
        "model_name":  model_name,
        "question":    question,
        "answer":      answer,
    }
    _unanswered_questions.append(entry)
    if len(_unanswered_questions) > _MAX_UNANSWERED:
        _unanswered_questions.pop(0)
    _save_unanswered_questions()
    logger.info("[미해결 질문] %s/%s — %s", device_name, model_name, question)

# ── 활동 로그 (인메모리, 서버 재시작 시 초기화) ──────────────────────
_activity_log: list[dict] = []
_MAX_LOG = 500

def _log_activity(session: dict, tab: str, action: str, detail: str = "") -> None:
    aid  = session.get("admin_id", "?")
    name = _admin_store.get(aid, {}).get("name", "")
    entry = {
        "ts":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "admin_id": aid,
        "name":     name,
        "tab":      tab,
        "action":   action,
        "detail":   detail,
    }
    _activity_log.append(entry)
    if len(_activity_log) > _MAX_LOG:
        _activity_log.pop(0)
    logger.info("[활동] %s(%s) — %s / %s %s", aid, name, tab, action, detail)

# ── 세션 (앱 자체 로그인만 사용 — 사번 + 비밀번호) ───────────────────
def _get_session(request: Request) -> dict | None:
    token = request.headers.get("X-Admin-Token", "")
    return _admin_sessions.get(token) if token else None

def _require_login(request: Request) -> dict:
    """로그인만 되어 있으면 통과 (user/admin/master 공용)."""
    s = _get_session(request)
    if not s:
        raise HTTPException(401, "로그인이 필요합니다.")
    return s

def _require_admin(request: Request) -> dict:
    """관리자 이상(admin/master)만 통과."""
    s = _get_session(request)
    if not s or s.get("role") not in ("admin", "master"):
        raise HTTPException(401, "로그인이 필요합니다.")
    return s

def _require_master(request: Request) -> dict:
    s = _get_session(request)
    if not s:
        raise HTTPException(401, "로그인이 필요합니다.")
    if s.get("role") != "master":
        raise HTTPException(403, "마스터 관리자만 접근할 수 있습니다.")
    return s

def _get_all_docs() -> list[dict]:
    """업로드된 모든 조각(행)을 펼쳐 반환."""
    result: list[dict] = []
    for rows in _user_docs.values():
        result.extend(rows)
    return result

def _doc_to_text(row: dict) -> str:
    """구조화된 행(증상/원인/해결방법/비고) → 임베딩·LLM 컨텍스트용 텍스트."""
    symptom  = row.get("symptom", "")
    cause    = row.get("cause", "")
    solution = row.get("solution", "")
    body_parts = []
    if cause:    body_parts.append(f"원인: {cause}")
    if solution: body_parts.append(f"해결방법: {solution}")
    body = "\n".join(body_parts)
    return f"{symptom}\n{body}" if symptom else body

def _device_key(device_name: str, model_name: str) -> str:
    return f"{device_name.strip()}|{model_name.strip()}"

def _get_candidate_indices(device_name: str, model_name: str) -> list[int]:
    """지정 장비/모델의 업로드 조각 인덱스 반환. 미지정이면 전체."""
    all_docs = _get_all_docs()
    key = _device_key(device_name, model_name)
    if not key.strip("|") or not _user_docs:
        return list(range(len(all_docs)))
    indices, offset = [], 0
    for k, rows in _user_docs.items():
        if k == key:
            indices.extend(range(offset, offset + len(rows)))
        offset += len(rows)
    return indices if indices else list(range(len(all_docs)))


# ── 답을 찾지 못했을 때 고정 응답 (거짓 답변 방지 + 미해결 질문 로그 감지용) ──
NO_ANSWER_MSG = "해당 내용에 대한 답을 찾을 수 없습니다."


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
            f"자료에 해당 장비·모델 정보가 없으면 '{NO_ANSWER_MSG}'라고만 답하라.\n"
        )
    return (
        "아래 근거 자료에 질문의 답이 실제로 있으면 출처와 함께 답하고, "
        f"자료에 답이 없으면 절대로 추측하거나 지어내지 말고 '{NO_ANSWER_MSG}'라고만 답하라.\n"
        + device_clause +
        "너는 의료기기 수리 매뉴얼 안내 도우미다. "
        "반드시 아래 '참고 자료(매뉴얼 발췌)'에 명시된 내용만 근거로 답한다. "
        f"자료에 없는 모델명·증상·알람 코드·오류 코드·절차는 예외 없이 '{NO_ANSWER_MSG}'라고만 답한다. "
        f"알람 코드 또는 오류 코드가 자료 목록에 없을 때도 동일하게 '{NO_ANSWER_MSG}'라고만 답한다.\n"
        "자료에 있는 내용을 답할 때: 증상을 물으면 '증상 → 점검 → 조치' 순서로 정리한다. "
        "답변 끝에 '근거: <항목명> (p.OO)' 형식으로 출처를 표기하고, 근거가 여러 개면 쉼표로 나열한다.\n"
        "임의 분해·직접 수리·내부 회로 수리를 묻는 경우 절차를 알려주지 않고 "
        "'해당 작업은 자격 기술자 또는 제조사 서비스 대상입니다. 임의 분해·수리는 금지되어 있습니다.'라고만 답한다.\n"
        "이 답변은 자료 안내용이며 실제 시술·수리 지시가 아니다. "
        "실제 분해·내부 수리는 자격 기술자 또는 제조사 서비스 대상임을 답변 끝에 고지한다.\n"
        "환자정보·실제 시리얼 번호·로트 번호는 취급하지 않는다. 해당 내용을 묻더라도 구체적 수치를 다루지 않는다.\n"
        "모든 답변의 맨 마지막에 반드시 다음 안전 고지를 한 번 덧붙인다: "
        "'⚠️자격 기술자 전용 — 임의 분해·내부 수리 금지, 환자 연결 상태 점검 금지, 의심 시 제조사 서비스 요청.'"
    )

VIBE_SYSTEM_PROMPT = (
    "너는 의료기기 수리 전문 도우미다. "
    "제공된 참고 자료 없이 모델 자체 지식으로 한국어로 자유롭게 답한다. "
    "답변 끝에 '⚡ 바이브 모드: 검색 없이 모델 지식으로 답변'이라고 명시한다."
)

# ── 임베딩 유틸 ──────────────────────────────────────────────────────
class EmbedUnavailable(RuntimeError):
    """임베딩 게이트웨이 장애 시 raise — 호출 체인 어디서든 잡을 수 있도록 분리."""

async def embed(text: str) -> np.ndarray:
    """단일 텍스트를 bge-m3:latest로 임베딩해 1-D numpy 배열로 반환."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text})
            res.raise_for_status()
            vec = res.json()["embedding"]   # 단수 키
        return np.array(vec, dtype=np.float32)
    except (httpx.ConnectError, httpx.TimeoutException):
        raise EmbedUnavailable("지금은 검색 기능을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    except httpx.HTTPStatusError as e:
        raise EmbedUnavailable(f"임베딩 서버 오류(HTTP {e.response.status_code}). 잠시 후 다시 시도해 주세요.")
    except (KeyError, ValueError):
        raise EmbedUnavailable("임베딩 서버 응답이 올바르지 않습니다. 잠시 후 다시 시도해 주세요.")


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


async def ensure_doc_embeddings() -> np.ndarray:
    """업로드된 모든 행의 임베딩을 캐시. 행 수가 달라지면 재계산."""
    global _doc_embeddings
    all_docs = _get_all_docs()
    if _doc_embeddings is None or _doc_embeddings.shape[0] != len(all_docs):
        vecs = [await embed(_doc_to_text(row)) for row in all_docs]
        _doc_embeddings = np.stack(vecs)   # (N, D)
    return _doc_embeddings


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

    chunks  = [_doc_to_text(all_docs[i]) for i in selected]
    sources = [{"title": all_docs[i].get("symptom", ""), "page": all_docs[i].get("note", "")} for i in selected]
    return chunks, sources


# ── Pydantic 모델 ─────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    context: str = ""       # 프론트 전달값 — RAG가 대체하므로 백엔드에서 무시
    bare: bool = False      # True = 바이브, False = 하네스
    device_name: str = ""   # 장비명 (예: 주입펌프)
    model_name:  str = ""   # 모델명 (예: DI-2200P)


class LoginRequest(BaseModel):
    admin_id: str
    password: str

class AddAdminRequest(BaseModel):
    admin_id: str
    name: str = ""

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class LogActivityRequest(BaseModel):
    tab:    str
    action: str
    detail: str = ""

class ResetRequestBody(BaseModel):
    admin_id: str

class AccessRequestBody(BaseModel):
    emp_id: str
    name: str
    dept: str = ""

class RoleChangeBody(BaseModel):
    role: str   # "user" | "admin" | "master"


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


# ── 엔드포인트 ────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    _require_login(request)   # 로그인한 사용자만 챗봇 사용 가능

    async def sse_harness():
        # 토큰 → sources 이벤트 → [DONE]  (이 순서를 지켜야 프론트가 sources를 읽음)
        try:
            chunks, sources = await retrieve(
                req.question, req.device_name, req.model_name, top_k=TOP_K
            )
        except EmbedUnavailable as e:
            yield f"data: {json.dumps({'token': f'⚠️ {e}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        context     = "\n\n".join(chunks)
        sys_prompt  = build_system_prompt(req.device_name, req.model_name)
        answer_parts: list[str] = []
        async for chunk in stream_ollama(req.question, context, sys_prompt):
            yield chunk
            try:
                token = json.loads(chunk[len("data: "):].strip()).get("token", "")
                answer_parts.append(token)
            except (json.JSONDecodeError, ValueError):
                pass
        yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

        full_answer = "".join(answer_parts)
        if NO_ANSWER_MSG in full_answer:
            _log_unanswered_question(req.device_name, req.model_name, req.question, full_answer)

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


# ── 로그인 / 로그아웃 / 비밀번호 (전체 역할 공용) ─────────────────────
@app.post("/api/login")
async def login(req: LoginRequest):
    acc = _admin_store.get(req.admin_id.strip())
    if not acc or acc["password"] != req.password:
        return {"ok": False, "error": "사번 또는 비밀번호가 올바르지 않습니다."}
    token = str(_uuid.uuid4())
    _admin_sessions[token] = {"admin_id": req.admin_id.strip(), "role": acc["role"]}
    logger.info("로그인: %s (role=%s)", req.admin_id, acc["role"])
    return {
        "ok": True,
        "token": token,
        "role": acc["role"],
        "admin_id": req.admin_id.strip(),
        "name": acc.get("name", ""),
        "dept": acc.get("dept", ""),
        "must_change_password": bool(acc.get("must_change_password", False)),
    }


@app.post("/api/logout")
async def logout(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    s = _admin_sessions.pop(token, None)
    if s:
        logger.info("로그아웃: %s", s.get("admin_id"))
    return {"ok": True}


@app.post("/api/change-password")
async def change_password_api(req: ChangePasswordRequest, request: Request):
    s = _require_login(request)
    acc = _admin_store.get(s["admin_id"])
    if not acc:
        return {"ok": False, "error": "계정 정보를 찾을 수 없습니다."}
    if acc["password"] != req.current_password:
        return {"ok": False, "error": "현재 비밀번호가 올바르지 않습니다."}
    if len(req.new_password) < 6:
        return {"ok": False, "error": "새 비밀번호는 6자 이상이어야 합니다."}
    acc["password"] = req.new_password
    acc["must_change_password"] = False
    _save_admins()
    _log_activity(s, "설정", "비밀번호 변경", "")
    return {"ok": True}


# ── 사용 권한 요청 API ────────────────────────────────────────────────
@app.post("/api/access-request")
async def submit_access_request(req: AccessRequestBody):
    """계정이 없는 사용자가 로그인 화면에서 제출 — 로그인 불필요."""
    emp_id = req.emp_id.strip()
    name   = req.name.strip()
    dept   = req.dept.strip()
    if not emp_id or not name:
        return {"ok": False, "error": "사번과 이름을 입력해 주세요."}
    if emp_id in _admin_store:
        return {"ok": False, "error": "이미 등록된 사번입니다. 비밀번호를 잊으셨다면 초기화를 요청하세요."}
    if any(r["emp_id"] == emp_id for r in _access_requests):
        return {"ok": True, "message": "이미 접수된 요청입니다. 마스터 관리자의 승인을 기다려 주세요."}
    _access_requests.append({
        "id":           str(_uuid.uuid4()),
        "emp_id":       emp_id,
        "name":         name,
        "dept":         dept,
        "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_access_requests()
    logger.info("사용 권한 요청: %s (%s / %s)", emp_id, name, dept)
    return {"ok": True, "message": "요청이 접수되었습니다. 마스터 관리자의 승인 후 안내받은 계정으로 로그인해 주세요."}


@app.get("/api/admin/access-requests")
async def get_access_requests(request: Request):
    _require_master(request)
    return {"ok": True, "requests": _access_requests}


@app.post("/api/admin/access-requests/{req_id}/approve")
async def approve_access_request(req_id: str, request: Request):
    s = _require_master(request)
    global _access_requests
    r = next((x for x in _access_requests if x["id"] == req_id), None)
    if not r:
        raise HTTPException(404, "요청을 찾을 수 없습니다.")
    emp_id = r["emp_id"]
    if emp_id in _admin_store:
        return {"ok": False, "error": "이미 등록된 사번입니다."}
    default_pw = "user1234"
    _admin_store[emp_id] = {
        "password": default_pw, "role": "user",
        "name": r["name"], "dept": r.get("dept", ""),
        "must_change_password": True,
    }
    _save_admins()
    _access_requests = [x for x in _access_requests if x["id"] != req_id]
    _save_access_requests()
    _log_activity(s, "설정", "사용 권한 승인", f"{emp_id} ({r['name']}) 등록")
    return {"ok": True, "message": f"{r['name']} ({emp_id}) 계정이 생성되었습니다. 초기 비밀번호: {default_pw}"}


@app.delete("/api/admin/access-requests/{req_id}")
async def dismiss_access_request(req_id: str, request: Request):
    _require_master(request)
    global _access_requests
    _access_requests = [x for x in _access_requests if x["id"] != req_id]
    _save_access_requests()
    return {"ok": True}


@app.get("/api/admin/users")
async def list_admin_users(request: Request):
    _require_master(request)
    users = [
        {"admin_id": aid, "role": d["role"], "name": d.get("name", ""), "dept": d.get("dept", "")}
        for aid, d in sorted(_admin_store.items())
    ]
    return {"ok": True, "users": users}


@app.post("/api/admin/users/add")
async def add_admin_user(req: AddAdminRequest, request: Request):
    s = _require_master(request)
    aid  = req.admin_id.strip()
    name = req.name.strip()
    if not aid:
        return {"ok": False, "error": "사번을 입력해 주세요."}
    if aid in _admin_store:
        return {"ok": False, "error": f"이미 등록된 사번입니다: {aid}"}
    _admin_store[aid] = {"password": "admin1234", "role": "admin", "name": name,
                          "dept": "", "must_change_password": True}
    _save_admins()
    _log_activity(s, "설정", "관리자 등록", f"{aid} ({name}) 등록")
    return {"ok": True}


@app.post("/api/admin/users/{admin_id}/role")
async def change_user_role(admin_id: str, req: RoleChangeBody, request: Request):
    s = _require_master(request)
    if req.role not in ("user", "admin", "master"):
        return {"ok": False, "error": "올바르지 않은 권한입니다."}
    if admin_id == s["admin_id"]:
        return {"ok": False, "error": "자기 자신의 권한은 변경할 수 없습니다."}
    acc = _admin_store.get(admin_id)
    if not acc:
        return {"ok": False, "error": "존재하지 않는 사번입니다."}
    if acc.get("role") == "master":
        master_count = sum(1 for a in _admin_store.values() if a.get("role") == "master")
        if master_count <= 1 and req.role != "master":
            return {"ok": False, "error": "마지막 마스터 계정의 권한은 변경할 수 없습니다."}
    old_role = acc.get("role")
    acc["role"] = req.role
    _save_admins()
    for t, sv in list(_admin_sessions.items()):
        if sv["admin_id"] == admin_id:
            sv["role"] = req.role
    _log_activity(s, "설정", "권한 변경", f"{admin_id} ({acc.get('name','')}) {old_role} → {req.role}")
    return {"ok": True}


@app.delete("/api/admin/users/{admin_id}")
async def delete_admin_user(admin_id: str, request: Request):
    s = _require_master(request)
    if admin_id == s["admin_id"]:
        return {"ok": False, "error": "자기 자신은 삭제할 수 없습니다."}
    acc = _admin_store.get(admin_id)
    if not acc:
        return {"ok": False, "error": "존재하지 않는 사번입니다."}
    if acc.get("role") == "master":
        return {"ok": False, "error": "마스터 계정은 삭제할 수 없습니다."}
    del_name = acc.get("name", "")
    del _admin_store[admin_id]
    for t in [t for t, sv in list(_admin_sessions.items()) if sv["admin_id"] == admin_id]:
        del _admin_sessions[t]
    _save_admins()
    _log_activity(s, "설정", "계정 삭제", f"{admin_id} ({del_name}) 삭제")
    return {"ok": True}


@app.get("/api/admin/activity-log")
async def get_activity_log(request: Request):
    _require_master(request)
    return {"ok": True, "log": list(reversed(_activity_log))}


@app.post("/api/admin/log-activity")
async def log_activity_api(req: LogActivityRequest, request: Request):
    s = _get_session(request)
    if not s:
        return {"ok": False}
    _log_activity(s, req.tab, req.action, req.detail)
    return {"ok": True}


# ── 비밀번호 초기화 요청 API ──────────────────────────────────────────
@app.post("/api/request-reset")
async def request_password_reset(req: ResetRequestBody):
    """로그인 없이 누구나 호출 가능 — 사번 존재 여부만 확인."""
    aid = req.admin_id.strip()
    if aid not in _admin_store:
        return {"ok": False, "error": "등록되지 않은 사번입니다."}
    if _admin_store[aid].get("role") == "master":
        return {"ok": False, "error": "마스터 계정은 초기화 요청을 사용할 수 없습니다."}
    if any(r["admin_id"] == aid for r in _reset_requests):
        return {"ok": True, "message": "이미 초기화 요청이 접수되어 있습니다. 마스터 관리자에게 문의하세요."}
    name = _admin_store[aid].get("name", "")
    _reset_requests.append({
        "admin_id": aid,
        "name":     name,
        "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_reset_requests()
    logger.info("비밀번호 초기화 요청: %s (%s)", aid, name)
    return {"ok": True, "message": "초기화 요청이 접수되었습니다. 마스터 관리자에게 문의하세요."}


@app.get("/api/admin/reset-requests")
async def get_reset_requests(request: Request):
    _require_master(request)
    return {"ok": True, "requests": _reset_requests}


@app.post("/api/admin/reset-password/{admin_id}")
async def reset_admin_password(admin_id: str, request: Request):
    s = _require_master(request)
    acc = _admin_store.get(admin_id)
    if not acc:
        return {"ok": False, "error": "존재하지 않는 사번입니다."}
    if acc.get("role") == "master":
        return {"ok": False, "error": "마스터 계정은 초기화할 수 없습니다."}
    default_pw = "admin1234" if acc.get("role") == "admin" else "user1234"
    acc["password"] = default_pw
    acc["must_change_password"] = True
    _save_admins()
    global _reset_requests
    _reset_requests = [r for r in _reset_requests if r["admin_id"] != admin_id]
    _save_reset_requests()
    name = acc.get("name", "")
    _log_activity(s, "설정", "비밀번호 초기화", f"{admin_id} ({name}) → {default_pw}")
    return {"ok": True, "message": f"초기화되었습니다. 초기 비밀번호: {default_pw}"}


@app.delete("/api/admin/reset-requests/{admin_id}")
async def dismiss_reset_request(admin_id: str, request: Request):
    _require_master(request)
    global _reset_requests
    _reset_requests = [r for r in _reset_requests if r["admin_id"] != admin_id]
    _save_reset_requests()
    return {"ok": True}


# ── 매뉴얼 삭제 요청 API ─────────────────────────────────────────────

@app.post("/api/admin/request-delete-doc")
async def request_delete_doc(request: Request):
    s = _require_admin(request)
    body = await request.json()
    device_name = body.get("device_name", "").strip()
    model_name  = body.get("model_name",  "").strip()
    if not device_name:
        raise HTTPException(400, "장비명이 필요합니다.")
    admin_id = s["admin_id"]
    name = _admin_store.get(admin_id, {}).get("name", "")
    global _doc_delete_requests
    for r in _doc_delete_requests:
        if r["device_name"] == device_name and r["model_name"] == model_name:
            return {"ok": False, "error": "이미 동일한 삭제 요청이 접수되어 있습니다."}
    _doc_delete_requests.append({
        "id":           str(_uuid.uuid4()),
        "device_name":  device_name,
        "model_name":   model_name,
        "admin_id":     admin_id,
        "admin_name":   name,
        "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_doc_delete_requests()
    _log_activity(s, "자료 업로드", "매뉴얼 삭제 요청", f"{device_name} {model_name}".strip())
    return {"ok": True, "message": "삭제 요청이 접수되었습니다. 마스터 관리자의 검토 후 삭제됩니다."}


@app.get("/api/admin/doc-delete-requests")
async def get_doc_delete_requests(request: Request):
    _require_master(request)
    return {"ok": True, "requests": _doc_delete_requests}


@app.post("/api/admin/approve-delete-doc/{request_id}")
async def approve_delete_doc(request_id: str, request: Request):
    s = _require_master(request)
    global _doc_delete_requests, _user_docs, _doc_embeddings
    req = next((r for r in _doc_delete_requests if r["id"] == request_id), None)
    if not req:
        raise HTTPException(404, "요청을 찾을 수 없습니다.")
    device_name = req["device_name"]
    model_name  = req["model_name"]
    key = _device_key(device_name, model_name)
    _user_docs.pop(key, None)
    _doc_embeddings = None
    _save_user_docs()
    _doc_delete_requests = [r for r in _doc_delete_requests if r["id"] != request_id]
    _save_doc_delete_requests()
    dev_label = " ".join(filter(None, [device_name, model_name]))
    _log_activity(s, "설정", "매뉴얼 삭제 승인", dev_label)
    return {"ok": True, "message": f"'{dev_label}' 자료가 삭제되었습니다."}


@app.delete("/api/admin/doc-delete-requests/{request_id}")
async def dismiss_doc_delete_request(request_id: str, request: Request):
    _require_master(request)
    global _doc_delete_requests
    _doc_delete_requests = [r for r in _doc_delete_requests if r["id"] != request_id]
    _save_doc_delete_requests()
    return {"ok": True}


# ── 미해결 질문 로그 (마스터 전용) ────────────────────────────────────
@app.get("/api/admin/unanswered-questions")
async def get_unanswered_questions(request: Request):
    _require_master(request)
    return {"ok": True, "questions": list(reversed(_unanswered_questions))}


@app.delete("/api/admin/unanswered-questions/{qid}")
async def dismiss_unanswered_question(qid: str, request: Request):
    _require_master(request)
    global _unanswered_questions
    _unanswered_questions = [q for q in _unanswered_questions if q["id"] != qid]
    _save_unanswered_questions()
    return {"ok": True}


# ── 엑셀 표준 양식 업로드/다운로드 ────────────────────────────────────
def _parse_excel_docs(xlsx_bytes: bytes) -> list[dict]:
    """표준 양식(증상/원인/해결방법/비고) 엑셀 → 행 리스트."""
    if not _XLSX_OK:
        raise RuntimeError("openpyxl 패키지가 설치되지 않았습니다: pip install openpyxl")
    wb = _openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers = [str(h).strip() if h is not None else "" for h in header_row]

    def _col(*names: str) -> int | None:
        for name in names:
            if name in headers:
                return headers.index(name)
        return None

    idx_symptom = _col("증상")
    idx_cause   = _col("원인")
    idx_solution = _col("해결방법", "해결 방법")
    idx_note    = _col("비고")

    if idx_symptom is None or idx_solution is None:
        raise RuntimeError("표준 양식이 아닙니다. '증상', '해결방법' 열이 반드시 있어야 합니다. (표준 양식 다운로드 버튼을 이용해 주세요)")

    rows: list[dict] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        def _cell(i: int | None) -> str:
            if i is None or i >= len(raw) or raw[i] is None:
                return ""
            return str(raw[i]).strip()
        symptom  = _cell(idx_symptom)
        solution = _cell(idx_solution)
        if not symptom and not solution:
            continue
        rows.append({
            "symptom":  symptom,
            "cause":    _cell(idx_cause),
            "solution": solution,
            "note":     _cell(idx_note),
        })
    wb.close()
    return rows


def _build_docs_workbook(rows: list[dict]):
    """행 리스트 → openpyxl Workbook (표준 양식 컬럼)."""
    wb = _openpyxl.Workbook()
    ws = wb.active
    ws.title = DOC_SHEET_NAME
    ws.append(DOC_COLUMNS)
    for row in rows:
        ws.append([row.get("symptom", ""), row.get("cause", ""), row.get("solution", ""), row.get("note", "")])
    widths = [28, 28, 50, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    return wb


@app.get("/api/download-template")
async def download_template():
    """빈 표준 양식(헤더만) 다운로드 — 로그인 불필요."""
    if not _XLSX_OK:
        raise HTTPException(500, "서버에 openpyxl 패키지가 없습니다.")
    wb = _build_docs_workbook([])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=upload_template.xlsx"},
    )


@app.get("/api/download-docs")
async def download_docs(device_name: str = "", model_name: str = "", request: Request = None):
    _require_admin(request)
    key = _device_key(device_name, model_name)
    rows = _user_docs.get(key)
    if rows is None:
        raise HTTPException(404, "해당 장비/모델의 업로드 자료가 없습니다.")
    wb = _build_docs_workbook(rows)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    dev_label = "_".join(filter(None, [device_name, model_name])) or "manual"
    encoded_name = _urllib_quote(f"{dev_label}.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=manual.xlsx; filename*=UTF-8''{encoded_name}"},
    )


@app.post("/api/upload-docs")
async def upload_docs(
    request: Request,
    file: UploadFile = File(...),
    device_name: str = Form(""),
    model_name:  str = Form(""),
):
    global _user_docs, _doc_embeddings
    _require_admin(request)

    fname = file.filename.lower()
    if not fname.endswith(".xlsx"):
        return {"ok": False, "error": "엑셀(.xlsx) 파일만 업로드할 수 있습니다. '표준 양식 다운로드'로 받은 양식에 맞춰 작성해 주세요."}

    raw_bytes = await file.read()
    try:
        rows = _parse_excel_docs(raw_bytes)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"파일 파싱 오류: {e}"}

    if not rows:
        return {"ok": False, "error": "엑셀에서 유효한 행을 찾을 수 없습니다. '증상' 또는 '해결방법'이 채워진 행이 필요합니다."}

    key = _device_key(device_name, model_name) or "default"
    _user_docs[key] = rows
    _doc_embeddings = None
    _save_user_docs()

    s = _get_session(request)
    if s:
        dev_label = " ".join(filter(None, [device_name, model_name]))
        _log_activity(s, "자료 업로드", "매뉴얼 업로드",
                      f"{dev_label} / {file.filename} ({len(rows)}행)")

    return {
        "ok": True,
        "filename": file.filename,
        "device_name": device_name,
        "model_name": model_name,
        "rows_extracted": len(rows),
        "preview": [f"{r['symptom']} → {r['solution']}" for r in rows[:3]],
    }


@app.post("/api/reset-docs")
async def reset_docs(
    request: Request,
    device_name: str = Form(""),
    model_name:  str = Form(""),
):
    """업로드 자료 초기화. 마스터 전용. 장비/모델 지정 시 해당 것만, 미지정 시 전체."""
    s = _require_master(request)
    global _user_docs, _doc_embeddings
    key = _device_key(device_name, model_name)
    if key.strip("|"):
        _user_docs.pop(key, None)
        msg = f"'{device_name} {model_name}' 업로드 자료가 초기화되었습니다."
    else:
        _user_docs.clear()
        msg = "모든 업로드 자료가 초기화되었습니다."
    _doc_embeddings = None
    _save_user_docs()
    dev_label = " ".join(filter(None, [device_name, model_name])) or "전체"
    _log_activity(s, "자료 업로드", "자료 삭제", dev_label)
    return {"ok": True, "message": msg}


@app.get("/api/devices")
async def get_devices():
    """업로드된 장비/모델 목록 + 조각 수 반환 — 드롭다운·현황 카드 공용."""
    devices = []
    seen: set[tuple] = set()
    for key, rows in _user_docs.items():
        parts = key.split("|", 1)
        dn = parts[0].strip()
        mn = parts[1].strip() if len(parts) > 1 else ""
        if dn and (dn, mn) not in seen:
            seen.add((dn, mn))
            devices.append({"device_name": dn, "model_name": mn, "chunk_count": len(rows)})
    devices.sort(key=lambda x: (x["device_name"], x["model_name"]))
    return {"ok": True, "devices": devices}


@app.get("/health")
async def health():
    # 캐시 상태도 함께 반환
    return {"status": "ok", "docs_cached": _doc_embeddings is not None}


@app.get("/api/debug-docs")
async def debug_docs():
    """업로드된 문서 키·행 수·미리보기 반환 (디버그용)."""
    return {
        "user_doc_keys": list(_user_docs.keys()),
        "user_doc_row_counts": {k: len(v) for k, v in _user_docs.items()},
        "previews": {
            k: [_doc_to_text(r)[:200] for r in v[:3]]
            for k, v in _user_docs.items()
        },
    }


@app.post("/api/debug-retrieve")
async def debug_retrieve(req: ChatRequest):
    """질문에 대해 실제로 선택된 청크와 유사도를 반환 (디버그용)."""
    try:
        all_docs   = _get_all_docs()
        doc_vecs   = await ensure_doc_embeddings()
        candidates = _get_candidate_indices(req.device_name, req.model_name)
        search_q   = " ".join(filter(None, [req.device_name, req.model_name, req.question]))
        q_vec      = await embed(search_q)
    except EmbedUnavailable as e:
        return {"ok": False, "error": str(e)}
    sims   = {i: float(cosine_sim(q_vec, doc_vecs[i])) for i in candidates}
    ranked = sorted(candidates, key=lambda i: sims[i], reverse=True)[:TOP_K * 2]
    return {
        "search_query": search_q,
        "candidates_total": len(candidates),
        "top_results": [
            {"index": i, "sim": round(sims[i], 4), "preview": _doc_to_text(all_docs[i])[:200]}
            for i in ranked
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
