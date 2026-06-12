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


class ChatRequest(BaseModel):
    question: str
    context: str


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
