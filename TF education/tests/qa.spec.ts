import { test, expect } from '@playwright/test';

const BACKEND  = 'http://127.0.0.1:8000';
const FRONTEND = 'http://127.0.0.1:5500';         // python -m http.server 5500
const QUESTION = '폐색 알람(AL-OCC)이 발생했을 때 조치 방법은?';

// 관리자 로그인 헬퍼 (바이브 모드 토글은 admin-only)
async function loginAdmin(page: import('@playwright/test').Page) {
  await page.locator('#btn-admin-login').click();
  await page.locator('#modal-pw').fill('admin1234');
  await page.locator('#modal-confirm').click();
  await expect(page.locator('#admin-badge')).toBeVisible({ timeout: 3_000 });
}

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

// ── E21 오류 코드 빠른 검색 QA ──────────────────────────────────────
test('E21 오류 코드 빠른 검색 — 도어·출처·안전 고지 검증', async ({ page }) => {

  // index.html 열기
  await page.goto(FRONTEND);
  await expect(page.locator('header h1')).toBeVisible();
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8_000 });
  console.log('\n  ✅ index.html 로드 + 백엔드 연결 확인');

  // 하네스 모드 확인 (아니면 켜기)
  const harnBtn = page.locator('#btn-harn');
  if (!(await harnBtn.evaluate(el => el.classList.contains('t-active')))) {
    await harnBtn.click();
    console.log('  ℹ️  하네스 모드로 전환');
  }
  await expect(harnBtn).toHaveClass(/t-active/);
  console.log('  ✅ 하네스 모드 활성 확인');

  // 질문칸에 직접 입력 후 전송 (오류 코드 패널 질문 형식과 동일)
  const Q = 'E21 무슨 뜻이야?';
  await page.locator('#question').fill(Q);
  await page.locator('#send-btn').click();
  console.log(`\n  📨 질문 전송: "${Q}"`);

  // 스트리밍 완료 대기 (35b 모델 지연 감안 — 최대 120 s)
  await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 120_000 });

  const answer = (await page.locator('#answer-box').textContent()) ?? '';
  console.log('\n─── E21 답변 ───\n' + answer + '\n────────────────');

  // 출처 박스 내용
  const sourcesBox  = page.locator('#sources-box');
  await expect(sourcesBox).toBeVisible({ timeout: 5_000 });
  const sourcesText = (await sourcesBox.textContent()) ?? '';
  console.log('  출처:', sourcesText.trim().replace(/\n/g, ' | '));

  // ── 검증 항목 ────────────────────────────────────────────────────
  const results = [
    { label: '답변에 "도어" 포함',              pass: /도어/.test(answer) },
    { label: '출처에 "센서 오류 코드" 포함',     pass: /센서 오류 코드/.test(sourcesText) },
    { label: '출처에 "p.78" 포함',              pass: /p\.78/.test(sourcesText) },
    { label: '답변에 안전 고지("자격 기술자") 포함', pass: /자격 기술자/.test(answer) },
  ];

  console.log('\n═══ E21 검증 결과 ═══');
  for (const r of results) {
    console.log(`  ${r.pass ? '✅ 통과' : '❌ 실패'} — ${r.label}`);
  }
  const allPass = results.every(r => r.pass);
  console.log(`\n  → 최종: ${allPass ? '✅ 통과' : '❌ 실패'}`);
  console.log('══════════════════════\n');

  for (const r of results) {
    expect(r.pass, r.label).toBe(true);
  }
});

// ── 바이브 모드 전환 확인 ───────────────────────────────────────────
test('바이브 모드 — 출처 없이 답변 확인', async ({ page }) => {
  await page.goto(FRONTEND);
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8_000 });

  // 바이브 모드 토글은 admin-only → 먼저 관리자 로그인
  await loginAdmin(page);
  console.log('  ✅ 관리자 로그인 완료');

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

// ── 바이브↔하네스 SSE 직접 비교 ─────────────────────────────────────
// 브라우저 컨텍스트에서 fetch + ReadableStream으로 SSE를 직접 파싱해
// 두 모드의 응답 차이를 검증한다 (HTML→백엔드:8000 경유, 11434 직접 호출 없음).
async function callChat(
  page: import('@playwright/test').Page,
  question: string,
  bare: boolean
): Promise<{ tokens: string; sources: { title: string; page: string }[] | null }> {
  return page.evaluate(
    async ({ question, bare, backendUrl }) => {
      const res = await fetch(`${backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, context: '', bare }),
      });
      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let tokens = '';
      let sources: { title: string; page: string }[] | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (raw === '[DONE]') continue;
          try {
            const parsed = JSON.parse(raw);
            if (parsed.sources) sources = parsed.sources;
            else if (parsed.token) tokens += parsed.token;
          } catch { /* 무시 */ }
        }
      }
      return { tokens, sources };
    },
    { question, bare, backendUrl: BACKEND }
  );
}

test('바이브↔하네스 비교 — 하네스=근거+출처 / 바이브=출처 없음', async ({ page }) => {
  // 빈 페이지를 열어 CORS same-origin 없이 백엔드 직접 fetch 가능하게 함
  await page.goto(FRONTEND);
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8_000 });

  const Q = 'E21 무슨 뜻이야?';

  // ── 1) 바이브 모드 (bare: true) ────────────────────────────────────
  console.log(`\n  📨 바이브 모드로 "${Q}" 전송 중…`);
  const vibeRes = await callChat(page, Q, true);
  console.log('  바이브 답변(앞 150자):', vibeRes.tokens.slice(0, 150));
  console.log('  바이브 sources:', JSON.stringify(vibeRes.sources));

  // ── 2) 하네스 모드 (bare: false) ───────────────────────────────────
  console.log(`\n  📨 하네스 모드로 "${Q}" 전송 중…`);
  const harnRes = await callChat(page, Q, false);
  console.log('  하네스 답변(앞 150자):', harnRes.tokens.slice(0, 150));
  console.log('  하네스 sources:', JSON.stringify(harnRes.sources));

  // ── 3) 검증 ────────────────────────────────────────────────────────
  const harnSourcesText = harnRes.sources
    ? harnRes.sources.map(s => `${s.title} ${s.page}`).join(' ')
    : '';

  const results = [
    {
      label: '하네스 답변에 "도어" 포함',
      pass: /도어/.test(harnRes.tokens),
    },
    {
      label: '하네스 출처에 "센서 오류 코드" 포함',
      pass: /센서 오류 코드/.test(harnSourcesText),
    },
    {
      label: '하네스 출처에 "p.78" 포함',
      pass: /p\.78/.test(harnSourcesText),
    },
    {
      label: '하네스 답변에 안전 고지("자격 기술자") 포함',
      pass: /자격 기술자/.test(harnRes.tokens),
    },
    {
      label: '바이브 답변이 비어있지 않음',
      pass: vibeRes.tokens.trim().length > 0,
    },
    {
      label: '바이브에는 sources 이벤트 없음',
      pass: vibeRes.sources === null,
    },
  ];

  // ── 4) 한 줄 리포트 ────────────────────────────────────────────────
  const harnPass = results.slice(0, 4).every(r => r.pass);
  const vibePass = results.slice(4).every(r => r.pass);
  console.log('\n═══ 바이브↔하네스 비교 결과 ═══');
  for (const r of results) {
    console.log(`  ${r.pass ? '✅' : '❌'} ${r.label}`);
  }
  console.log(
    `\n  → 하네스=근거+출처 ${harnPass ? '✅' : '❌'} / 바이브=출처 없음 ${vibePass ? '✅' : '❌'}`
  );
  console.log('═══════════════════════════════\n');

  for (const r of results) {
    expect(r.pass, r.label).toBe(true);
  }
});
