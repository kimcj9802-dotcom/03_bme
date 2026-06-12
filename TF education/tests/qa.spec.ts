import { test, expect } from '@playwright/test';

const BACKEND  = 'http://127.0.0.1:8000';
const FRONTEND = 'http://127.0.0.1:5500';         // python -m http.server 5500
const QUESTION = '폐색 알람(AL-OCC)이 발생했을 때 조치 방법은?';

// ── 1. 백엔드 /health 연결 확인 ─────────────────────────────────────
test('1) 백엔드 /health 연결 확인', async ({ request }) => {
  const res = await request.get(`${BACKEND}/health`);
  expect(
    res.status(),
    '백엔드가 응답하지 않습니다 — python -m uvicorn app:app --port 8000 실행 여부 확인'
  ).toBe(200);
  const body = await res.json();
  expect(body).toMatchObject({ status: 'ok' });
  console.log('  백엔드 응답:', JSON.stringify(body));
});

// ── 2~5. 브라우저 E2E ───────────────────────────────────────────────
test('2~5) 하네스 모드 — 질문 전송·답변·출처 검증', async ({ page }) => {

  // 2) 정적 서버 주소로 index.html 열기
  await page.goto(FRONTEND);
  await expect(page.locator('header h1')).toBeVisible();
  console.log('\n  ✅ 2) index.html 로드 완료');

  // 백엔드 연결 표시등 초록 확인
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8_000 });
  console.log('  ✅ 백엔드 연결 표시등 초록');

  // 하네스 모드(기본값) 확인 — 버튼에 t-active 클래스
  await expect(page.locator('#btn-harn')).toHaveClass(/t-active/);
  console.log('  ✅ 하네스 모드 활성 확인');

  // 3) 질문 입력 후 전송
  await page.locator('#question').fill(QUESTION);
  await page.locator('#send-btn').click();
  console.log(`\n  📨 3) 질문 전송: "${QUESTION}"`);

  // 4) 답변 스트리밍 완료 대기 (최대 120 s — DOCS 첫 임베딩 포함)
  await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 120_000 });

  const answer = (await page.locator('#answer-box').textContent()) ?? '';
  console.log('\n─── 모델 답변 ───\n' + answer + '\n─────────────────');

  // 4-a) 답변 내용 — 핵심 키워드 포함 여부
  const hasKeyword = /클램프|라인|재개|AL-OCC|폐색/.test(answer);
  // 4-b) 안전 고지 포함 여부
  const hasSafety  = /자격 기술자/.test(answer);

  // 5) 출처(sources-box) 표시 여부 확인
  const sourcesBox = page.locator('#sources-box');
  await expect(sourcesBox).toBeVisible({ timeout: 5_000 });
  const sourcesText = (await sourcesBox.textContent()) ?? '';
  const hasSources  = sourcesText.trim().length > 0;

  // ── 최종 리포트 ─────────────────────────────────────────────────
  const results = [
    { label: '핵심 키워드(클램프/라인/재개/폐색) 포함', pass: hasKeyword },
    { label: '안전 고지(자격 기술자) 포함',               pass: hasSafety  },
    { label: '출처(sources-box) 표시됨',                  pass: hasSources  },
  ];

  console.log('\n═══ 검증 결과 ═══');
  for (const r of results) {
    console.log(`  ${r.pass ? '✅ 통과' : '❌ 실패'} — ${r.label}`);
  }
  const allPass = results.every(r => r.pass);
  console.log(`\n  → 최종: ${allPass ? '✅ 통과' : '❌ 실패'}`);
  console.log('  출처 내용:', sourcesText.trim().replace(/\n/g, ' | '));
  console.log('════════════════\n');

  expect(hasKeyword, '답변에 핵심 키워드가 없습니다').toBe(true);
  expect(hasSafety,  '안전 고지가 없습니다').toBe(true);
  expect(hasSources, '출처(sources-box)가 표시되지 않았습니다').toBe(true);
});

// ── 바이브 모드 전환 확인 ───────────────────────────────────────────
test('바이브 모드 — 출처 없이 답변 확인', async ({ page }) => {
  await page.goto(FRONTEND);
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8_000 });

  // 바이브 모드로 전환
  await page.locator('#btn-vibe').click();
  await expect(page.locator('#btn-vibe')).toHaveClass(/t-active/);
  await expect(page.locator('#mode-desc')).toHaveText('모델 직접 호출 (출처 없음)');
  console.log('\n  ✅ 바이브 모드 전환 확인');

  await page.locator('#question').fill(QUESTION);
  await page.locator('#send-btn').click();

  await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 120_000 });

  const answer = (await page.locator('#answer-box').textContent()) ?? '';
  console.log('\n─── 바이브 답변 ───\n' + answer.slice(0, 200) + '...\n──────────────────');

  // 바이브 모드에서는 sources-box 가 숨겨져 있어야 함
  const sourcesBox = page.locator('#sources-box');
  const sourcesVisible = await sourcesBox.isVisible();

  const results = [
    { label: '답변이 비어있지 않음',       pass: answer.trim().length > 0 },
    { label: '출처 박스가 숨겨짐(바이브)', pass: !sourcesVisible           },
  ];

  console.log('\n═══ 바이브 검증 ═══');
  for (const r of results) {
    console.log(`  ${r.pass ? '✅ 통과' : '❌ 실패'} — ${r.label}`);
  }
  console.log(`  → 최종: ${results.every(r => r.pass) ? '✅ 통과' : '❌ 실패'}`);
  console.log('══════════════════\n');

  expect(answer.trim().length, '바이브 답변이 비어 있습니다').toBeGreaterThan(0);
  expect(sourcesVisible, '바이브 모드에서 출처 박스가 보이면 안 됩니다').toBe(false);
});
