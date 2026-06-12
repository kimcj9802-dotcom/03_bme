from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_URL = "http://121.138.151.6:11434/api/chat"
MODEL = "qwen3.6:35b-a3b"
SYSTEM_PROMPT = (
    "너는 의료기기 수리 매뉴얼 안내 도우미다. "
    "반드시 사용자가 제공한 '참고 자료(매뉴얼 발췌)' 텍스트에 명시된 내용만을 근거로 답한다. "

    # ── 핵심 제약: 자료 밖 내용 차단 ──────────────────────────────────
    "참고 자료에 해당 내용이 없으면 절대 추측·유추·일반 지식으로 답하지 말고, "
    "반드시 정확히 다음 문장으로만 답한다: "
    "'매뉴얼에서 확인 불가 — 해당 내용은 제공된 발췌에 없습니다. 정식 매뉴얼 / 제조사 서비스를 확인하세요.' "
    "모델명(예: DI-9999 등 자료에 없는 기기), 증상, 알람 코드, 오류 코드, 절차 등 "
    "참고 자료에 한 글자도 언급되지 않은 내용은 예외 없이 위 문장으로만 답한다. "

    # ── 미수록 코드 명시 ───────────────────────────────────────────────
    "알람 코드(AL-OCC, AL-PRS, AL-AIR 등) 또는 오류 코드(E12, E15, E21, E33 등)를 질문받았을 때, "
    "해당 코드가 참고 자료 목록에 없으면 '미수록 코드 — 해당 코드는 제공된 발췌에 수록되어 있지 않습니다.'라고 명시한다. "

    # ── 정상 답변 형식 ─────────────────────────────────────────────────
    "자료에 있는 내용을 답할 때: 증상을 물으면 '증상 → 점검 → 조치' 순서로 정리한다. "
    "답변 끝에 '근거: <항목명> (p.OO)' 형식으로 출처를 표기하고, 근거가 여러 개면 쉼표로 나열한다. "

    # ── 분해·직접 수리 요청 차단 ──────────────────────────────────────
    "임의 분해, 직접 수리, 내부 회로 수리 방법을 묻는 경우 절차를 알려주지 않고 "
    "'해당 작업은 자격 기술자 또는 제조사 서비스 대상입니다. 임의 분해·수리는 금지되어 있습니다.'라고만 답한다. "

    # ── 안전 고지 ─────────────────────────────────────────────────────
    "모든 답변의 맨 마지막에 반드시 다음 안전 고지를 한 번 덧붙인다: "
    "'⚠ 본 안내는 자격 기술자 전용입니다. 임의 분해·내부 수리는 하지 마시고, "
    "환자 연결 상태에서는 점검하지 마세요. 의심 시 제조사 서비스에 요청하세요.'"
)


RECALL_SYSTEM_PROMPT = (
    "아래 [리콜대조결과]에 적힌 숫자와 로트만 인용해 한국어로 한두 줄로 답하라. "
    "결과에 없는 로트·건수를 지어내지 마라."
)


class ChatRequest(BaseModel):
    question: str
    context: str


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


async def stream_ollama(question: str, context: str):
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"참고 자료:\n{context}\n\n질문: {question}",
            },
        ],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if data.get("done"):
                        yield "data: [DONE]\n\n"
                except json.JSONDecodeError:
                    continue


async def stream_recall(context: str):
    """리콜 대조 결과(context)를 모델에 넘겨 자연어 요약을 SSE로 스트리밍."""
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": RECALL_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if data.get("done"):
                        yield "data: [DONE]\n\n"
                except json.JSONDecodeError:
                    continue


@app.post("/api/recall-check")
async def recall_check(req: RecallCheckRequest):
    # ── 1. 파이썬이 직접 매칭 계산 (모델에게 시키지 않음) ──────────────
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

    total   = len(req.inventory)
    n_match = len(matched)
    n_safe  = len(unmatched)

    # ── 2. 결정론적 결과를 컨텍스트로 구성 ────────────────────────────
    matched_lots  = ", ".join(f"{m['lot']}({m['reason']})" for m in matched)  \
                    or "없음"
    unmatched_lots = ", ".join(u["lot"] for u in unmatched) or "없음"

    context = (
        f"[리콜대조결과]\n"
        f"전체 보유 기기: {total}건\n"
        f"리콜 대상: {n_match}건 — 로트 {matched_lots}\n"
        f"대상 아님: {n_safe}건 — 로트 {unmatched_lots}\n"
        f"(이 숫자와 로트는 파이썬 코드가 계산한 확정값임)"
    )

    # ── 3. 계산 요약을 SSE meta 이벤트로 먼저 전송, 이후 모델 스트리밍 ─
    async def stream_with_meta():
        # 프론트가 계산값을 직접 읽을 수 있도록 첫 이벤트로 전달
        meta = {
            "type": "meta",
            "total": total,
            "matched": n_match,
            "safe": n_safe,
            "matched_lots": [m["lot"] for m in matched],
            "safe_lots":    [u["lot"] for u in unmatched],
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
        # 이후 모델 자연어 요약 스트리밍
        async for chunk in stream_recall(context):
            yield chunk

    return StreamingResponse(
        stream_with_meta(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        stream_ollama(req.question, req.context),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
