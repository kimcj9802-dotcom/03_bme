"""
Playwright UI 테스트 — index.html + FastAPI 백엔드(:8000)
실행: pytest test_ui.py -v
"""
import re
import pytest
from playwright.sync_api import Page, expect


BASE = "http://127.0.0.1:8000"
HTML = "file:///D:/claude%20ai/TF%20education/index.html"


# ── 공통 픽스처 ────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def backend():
    """백엔드가 응답하는지 세션 전체에서 한 번 확인."""
    import httpx
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
        assert r.status_code == 200, f"/health 응답 코드: {r.status_code}"
    except Exception as e:
        pytest.skip(f"백엔드 미실행 — 건너뜀: {e}")


# ── 1. 헬스 체크 API ───────────────────────────────────────────────────
def test_health_endpoint(backend):
    import httpx
    r = httpx.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── 2. 페이지 로드 & 기본 요소 존재 ───────────────────────────────────
def test_page_loads(page: Page):
    page.goto(HTML)
    expect(page.locator("header h1")).to_be_visible()
    expect(page.locator("#question")).to_be_visible()
    expect(page.locator("#context")).to_be_visible()
    expect(page.locator("#send-btn")).to_be_visible()
    expect(page.locator("#answer-box")).to_be_visible()


# ── 3. 연결 상태 표시등 — 백엔드 실행 중이면 초록 ─────────────────────
def test_status_dot_green_when_backend_up(page: Page, backend):
    page.goto(HTML)
    dot = page.locator("#dot")
    # /health fetch 완료 대기 (최대 5 s)
    expect(dot).to_have_class(re.compile(r"\bok\b"), timeout=5000)
    expect(page.locator("#status-text")).to_have_text("백엔드 연결됨")


# ── 4. 질문 없이 전송 → alert 확인 ────────────────────────────────────
def test_send_without_question_shows_alert(page: Page):
    page.goto(HTML)
    # alert 대화상자 자동 수락하고 메시지 캡처
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    page.locator("#send-btn").click()
    page.wait_for_timeout(500)
    assert any("질문" in m for m in messages), f"alert 메시지 없음: {messages}"


# ── 5. Ctrl+Enter 단축키로 전송 트리거 ────────────────────────────────
def test_ctrl_enter_triggers_send(page: Page):
    page.goto(HTML)
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    page.locator("#question").focus()
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(500)
    # 질문이 비어 있으므로 alert 발생 = 전송 로직 진입 확인
    assert len(messages) > 0


# ── 6. 지우기 버튼 동작 ───────────────────────────────────────────────
def test_clear_button_resets_answer(page: Page):
    page.goto(HTML)
    # 답변 영역에 임의 텍스트 주입 후 지우기
    page.evaluate("document.getElementById('answer-box').textContent = '임시 답변'")
    page.locator("#clear-btn").click()
    expect(page.locator("#answer-box")).to_have_text("답변이 여기에 표시됩니다.")
    expect(page.locator("#answer-box")).to_have_class(re.compile(r"\bempty\b"))


# ── 7. HTML 소스에 11434/ollama 직접 호출 없음 (보안) ─────────────────
def test_html_has_no_direct_ollama_call(page: Page):
    page.goto(HTML)
    src = page.content()
    assert "11434" not in src, "HTML에 11434 포트 직접 참조 발견"
    assert "ollama" not in src.lower(), "HTML에 ollama 직접 참조 발견"


# ── 8. BE 변수 선언 확인 ──────────────────────────────────────────────
def test_html_uses_BE_variable(page: Page):
    page.goto(HTML)
    src = page.content()
    assert 'const BE' in src, "BE 변수 선언 없음"
    assert "8000" in src, "BE 주소에 포트 8000 없음"


# ── 9. /api/chat 실제 스트리밍 호출 (백엔드 필요) ─────────────────────
def test_chat_streaming_response(page: Page, backend):
    page.goto(HTML)
    # 짧은 컨텍스트와 질문 입력
    page.locator("#context").fill("FastAPI는 Python 웹 프레임워크입니다.")
    page.locator("#question").fill("FastAPI가 무엇인가요?")

    # 네트워크 요청 감시
    with page.expect_request(lambda r: "/api/chat" in r.url) as req_info:
        page.locator("#send-btn").click()

    request = req_info.value
    assert request.method == "POST"
    body = request.post_data_json
    assert body["question"] == "FastAPI가 무엇인가요?"
    assert "context" in body

    # 답변 영역에 텍스트가 채워질 때까지 대기 (최대 30 s)
    expect(page.locator("#answer-box")).not_to_have_class(
        re.compile(r"\bempty\b"), timeout=30000
    )
    answer = page.locator("#answer-box").text_content()
    assert answer and len(answer.strip()) > 0, "답변 내용이 비어 있음"
