from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import re
import httpx
import json
import numpy as np

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_URL  = "http://121.138.151.6:11500/api/chat"
EMBED_URL   = "http://121.138.151.6:11500/api/embeddings"
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


SYSTEM_PROMPT = (
    "너는 의료기기 수리 매뉴얼 안내 도우미다. "
    "반드시 사용자가 제공한 '참고 자료(매뉴얼 발췌)' 텍스트에 명시된 내용만을 근거로 답한다. "
    "참고 자료에 해당 내용이 없으면 절대 추측·유추·일반 지식으로 답하지 말고, "
    "반드시 정확히 다음 문장으로만 답한다: "
    "'매뉴얼에서 확인 불가 — 해당 내용은 제공된 발췌에 없습니다. 정식 매뉴얼 / 제조사 서비스를 확인하세요.' "
    "모델명(예: DI-9999 등 자료에 없는 기기), 증상, 알람 코드, 오류 코드, 절차 등 "
    "참고 자료에 한 글자도 언급되지 않은 내용은 예외 없이 위 문장으로만 답한다. "
    "알람 코드(AL-OCC, AL-PRS, AL-AIR 등) 또는 오류 코드(E12, E15, E21, E33 등)를 질문받았을 때, "
    "해당 코드가 참고 자료 목록에 없으면 '미수록 코드 — 해당 코드는 제공된 발췌에 수록되어 있지 않습니다.'라고 명시한다. "
    "자료에 있는 내용을 답할 때: 증상을 물으면 '증상 → 점검 → 조치' 순서로 정리한다. "
    "답변 끝에 '근거: <항목명> (p.OO)' 형식으로 출처를 표기하고, 근거가 여러 개면 쉼표로 나열한다. "
    "임의 분해, 직접 수리, 내부 회로 수리 방법을 묻는 경우 절차를 알려주지 않고 "
    "'해당 작업은 자격 기술자 또는 제조사 서비스 대상입니다. 임의 분해·수리는 금지되어 있습니다.'라고만 답한다. "
    "모든 답변의 맨 마지막에 반드시 다음 안전 고지를 한 번 덧붙인다: "
    "'⚠ 본 안내는 자격 기술자 전용입니다. 임의 분해·내부 수리는 하지 마시고, "
    "환자 연결 상태에서는 점검하지 마세요. 의심 시 제조사 서비스에 요청하세요.'"
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
    """DOCS 임베딩을 최초 1회만 계산해 메모리에 캐시."""
    global _doc_embeddings
    if _doc_embeddings is None:
        vecs = []
        for doc in DOCS:
            vecs.append(await embed(doc))
        _doc_embeddings = np.stack(vecs)   # (N, D)
    return _doc_embeddings


def _parse_source(first_line: str) -> dict:
    """'N) 조각명 — p.OO' 형태의 첫 줄에서 title과 page를 분리."""
    m = re.match(r'^\d+\)\s*(.+?)\s*[—-]+\s*(p\.\d+)', first_line)
    if m:
        return {"title": m.group(1).strip(), "page": m.group(2).strip()}
    # 패턴 불일치 시 원본 전체를 title로
    return {"title": first_line.strip(), "page": ""}


async def retrieve(question: str, top_k: int = TOP_K) -> tuple[list[str], list[dict]]:
    """코사인 유사도 상위 top_k 조각을 반환.
    SIM_THRESHOLD 미만 조각은 제외하되, 최상위 1개는 임계값 무관하게 항상 포함.
    반환: (chunks, sources) — sources = [{"title": ..., "page": ...}, ...]
    """
    doc_vecs = await ensure_doc_embeddings()
    q_vec    = await embed(question)
    sims     = [cosine_sim(q_vec, dv) for dv in doc_vecs]

    ranked = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)

    selected = []
    for rank, idx in enumerate(ranked):
        if rank == 0:
            selected.append(idx)
        elif sims[idx] >= SIM_THRESHOLD:
            selected.append(idx)
        if len(selected) >= top_k:
            break

    chunks  = [DOCS[i] for i in selected]
    sources = [_parse_source(DOCS[i].splitlines()[0]) for i in selected]
    return chunks, sources


# ── Pydantic 모델 ─────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    context: str = ""  # 하네스 모드에서는 RAG가 대체; 바이브 모드에서도 무시
    bare: bool = False  # True = 바이브(모델 직접), False = 하네스(RAG + 출처)


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
async def stream_ollama(question: str, context: str):
    """토큰만 흘림 — [DONE] 은 호출부에서 발행 (sources 이벤트 순서 제어 목적)."""
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
        chunks, sources = await retrieve(req.question, top_k=TOP_K)
        context = "\n\n".join(chunks)
        async for chunk in stream_ollama(req.question, context):
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


@app.get("/health")
async def health():
    # 캐시 상태도 함께 반환
    return {"status": "ok", "docs_cached": _doc_embeddings is not None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
