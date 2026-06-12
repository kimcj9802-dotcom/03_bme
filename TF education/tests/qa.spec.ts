import { test, expect } from '@playwright/test';
import path from 'path';

const BASE = 'http://127.0.0.1:8000';
const HTML  = `file://${path.resolve(__dirname, '..', 'index.html').replace(/\\/g, '/')}`;

// ── 1. 백엔드 헬스 체크 ────────────────────────────────────────────
test('1) 백엔드 /health 연결 확인', async ({ request }) => {
  const res = await request.get(`${BASE}/health`);
  expect(res.status(), '백엔드가 응답하지 않습니다 — uvicorn이 실행 중인지 확인하세요').toBe(200);
  const body = await res.json();
  // docs_cached 필드가 추가될 수 있으므로 부분 일치로 검사
  expect(body).toMatchObject({ status: 'ok' });
});

// ── 2-6. 브라우저 E2E — 질문 전송 · 답변 내용 검증 ──────────────────
test('2~6) 폐색 알람 질문 → 답변 내용 및 안전 고지 검증', async ({ page }) => {

  // 2) index.html 열기
  await page.goto(HTML);
  await expect(page.locator('header h1')).toBeVisible();

  // 백엔드 연결 표시등이 초록으로 바뀔 때까지 대기
  await expect(page.locator('#dot')).toHaveClass(/\bok\b/, { timeout: 8000 });

  // 3) 질문 입력 후 전송
  await page.locator('#question').fill('폐색 알람 뜨면 어떻게 해?');
  await page.locator('#send-btn').click();

  // 4) 전송 버튼 재활성화 = 스트리밍 완전 종료
  //    첫 질문은 DOCS 8개 임베딩 캐시 생성 포함 → 넉넉히 120 s
  await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 120_000 });

  const answer = (await page.locator('#answer-box').textContent()) ?? '';
  console.log('\n─── 모델 답변 ───\n' + answer + '\n─────────────────');

  // 4) 답변에 핵심 키워드 포함 여부
  const hasKeyword = /클램프|라인/.test(answer);

  // 5) 안전 고지 포함 여부
  const hasSafety  = /자격 기술자/.test(answer);

  // 6) 최종 리포트
  const results = [
    { label: '핵심 키워드(클램프 또는 라인) 포함', pass: hasKeyword },
    { label: '안전 고지(자격 기술자) 포함',         pass: hasSafety  },
  ];

  console.log('\n═══ 검증 결과 ═══');
  for (const r of results) {
    console.log(`  ${r.pass ? '✅ 통과' : '❌ 실패'} — ${r.label}`);
  }
  const allPass = results.every(r => r.pass);
  console.log(`\n  → 최종: ${allPass ? '✅ 통과' : '❌ 실패'}`);
  console.log('════════════════\n');

  expect(hasKeyword, '답변에 "클램프" 또는 "라인"이 없습니다').toBe(true);
  expect(hasSafety,  '답변에 안전 고지("자격 기술자")가 없습니다').toBe(true);
});
