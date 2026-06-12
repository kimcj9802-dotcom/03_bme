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
    "참고 자료(매뉴얼 발췌)에만 근거해 한국어로 답한다. "
    "증상을 물으면 '증상 → 점검 → 조치' 순서로 정리해 답한다. "
    "자료에 없는 내용은 추측하지 말고 '매뉴얼에서 확인 불가'라고만 답한다. "
    "답변 마지막 줄에는 반드시 '근거: <항목명> (p.OO)' 형식으로 출처를 표기한다. "
    "근거가 여러 개면 쉼표로 나열한다. 예) 근거: 폐색 알람(AL-OCC) (p.42), 작업 안전 원칙 (p.7). "
    "자료에서 찾지 못한 경우에는 출처 줄 없이 '매뉴얼에서 확인 불가'로만 끝낸다."
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
