// @ts-check
const { test, expect } = require('@playwright/test');
const path = require('path');

const BASE = 'http://127.0.0.1:8000';
const HTML  = `file://${path.resolve(__dirname, '..', 'index.html').replace(/\\/g, '/')}`;

// ── 1. 헬스 체크 API ────────────────────────────────────────────────
test('GET /health → { status: ok }', async ({ request }) => {
  const res = await request.get(`${BASE}/health`);
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body).toEqual({ status: 'ok' });
});

// ── 2. 페이지 기본 요소 렌더링 ──────────────────────────────────────
test('페이지 로드 — 필수 요소 존재', async ({ page }) => {
  await page.goto(HTML);
  await expect(page.locator('header h1')).toBeVisible();
  await expect(page.locator('#question')).toBeVisible();
  await expect(page.locator('#context')).toBeVisible();
  await expect(page.locator('#send-btn')).toBeVisible();
  await expect(page.locator('#answer-box')).toBeVisible();
});

// ── 3. 연결 상태 표시등 — 백엔드 실행 중이면 초록 ────────────────────
test('상태 표시등 — 백엔드 연결 시 초록(ok)', async ({ page }) => {
  await page.goto(HTML);
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 6000 });
  await expect(page.locator('#status-text')).toHaveText('백엔드 연결됨');
});

// ── 4. 질문 없이 전송 → alert ───────────────────────────────────────
test('질문 미입력 전송 → alert 경고', async ({ page }) => {
  await page.goto(HTML);
  let alertMsg = '';
  page.once('dialog', async dialog => {
    alertMsg = dialog.message();
    await dialog.accept();
  });
  await page.locator('#send-btn').click();
  await page.waitForTimeout(400);
  expect(alertMsg).toMatch(/질문/);
});

// ── 5. Ctrl+Enter 단축키 전송 ──────────────────────────────────────
test('Ctrl+Enter — 전송 로직 진입(alert 확인)', async ({ page }) => {
  await page.goto(HTML);
  let fired = false;
  page.once('dialog', async d => { fired = true; await d.accept(); });
  await page.locator('#question').focus();
  await page.keyboard.press('Control+Enter');
  await page.waitForTimeout(400);
  expect(fired).toBe(true);
});

// ── 6. 지우기 버튼 ─────────────────────────────────────────────────
test('지우기 버튼 — 답변 영역 초기화', async ({ page }) => {
  await page.goto(HTML);
  await page.evaluate(() => {
    document.getElementById('answer-box').textContent = '임시 답변';
  });
  await page.locator('#clear-btn').click();
  await expect(page.locator('#answer-box')).toHaveText('답변이 여기에 표시됩니다.');
  await expect(page.locator('#answer-box')).toHaveClass(/\bempty\b/);
});

// ── 7. 보안: HTML에 11434/ollama 직접 참조 없음 ──────────────────────
test('보안 — HTML 소스에 ollama 직접 호출 없음', async ({ page }) => {
  await page.goto(HTML);
  const src = await page.content();
  expect(src).not.toContain('11434');
  expect(src.toLowerCase()).not.toContain('ollama');
});

// ── 8. BE 변수 선언 확인 ───────────────────────────────────────────
test('BE 변수 — 백엔드 주소 단일 선언', async ({ page }) => {
  await page.goto(HTML);
  const src = await page.content();
  expect(src).toContain('const BE');
  expect(src).toContain('8000');
});

// ── 9. /api/chat SSE 스트리밍 실제 호출 ───────────────────────────
test('질문 전송 — /api/chat POST + 답변 스트리밍', async ({ page }) => {
  await page.goto(HTML);
  await page.locator('#context').fill('FastAPI는 Python 기반 웹 프레임워크입니다.');
  await page.locator('#question').fill('FastAPI가 무엇인가요?');

  // 네트워크 요청 캡처
  const [request] = await Promise.all([
    page.waitForRequest(r => r.url().includes('/api/chat') && r.method() === 'POST'),
    page.locator('#send-btn').click(),
  ]);

  const body = JSON.parse(request.postData() || '{}');
  expect(body.question).toBe('FastAPI가 무엇인가요?');
  expect(body).toHaveProperty('context');

  // 전송 버튼 재활성화 = 스트리밍 완전 종료
  await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 60000 });
  const answer = await page.locator('#answer-box').textContent();
  expect(answer?.trim().length).toBeGreaterThan(0);
});
