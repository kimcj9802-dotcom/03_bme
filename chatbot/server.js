import 'dotenv/config';
import express from 'express';
import multer from 'multer';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  addDocument,
  removeDocument,
  retrieveRelevant,
  listDocuments,
} from './rag.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 50 * 1024 * 1024 } });

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || 'qwen2.5:1.5b';

app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

// ── Ollama 상태 확인 ───────────────────────────────────────────────────

async function checkOllama() {
  try {
    const res = await fetch(`${OLLAMA_URL}/api/tags`);
    return res.ok;
  } catch {
    return false;
  }
}

// ── Ollama 채팅 호출 ───────────────────────────────────────────────────

async function ollamaChat(systemPrompt, messages) {
  const payload = {
    model: OLLAMA_MODEL,
    stream: false,
    options: { num_ctx: 4096, temperature: 0.1 },
    think: false, // Qwen3 thinking 모드 비활성화 (빠른 응답)
    messages: [
      { role: 'system', content: systemPrompt },
      ...messages,
    ],
  };

  const res = await fetch(`${OLLAMA_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) throw new Error(`Ollama 오류: ${res.status}`);
  const data = await res.json();
  return data.message?.content || '응답을 받지 못했습니다.';
}

// ── 관리자 인증 미들웨어 ───────────────────────────────────────────────

function adminAuth(req, res, next) {
  if (req.headers['x-admin-token'] !== process.env.ADMIN_PASSWORD) {
    return res.status(401).json({ error: '인증 실패' });
  }
  next();
}

// ── 사용자 챗 API ──────────────────────────────────────────────────────

app.post('/api/chat', async (req, res) => {
  try {
    const { messages, userMessage } = req.body;
    if (!userMessage?.trim()) return res.status(400).json({ error: '메시지를 입력하세요.' });

    const relevant = await retrieveRelevant(userMessage);

    let contextText = relevant.length === 0
      ? '현재 등록된 메뉴얼이 없거나 관련 내용을 찾지 못했습니다.'
      : relevant.map((c, i) => `[참고 ${i + 1} - ${c.docName}]\n${c.text}`).join('\n\n---\n\n');

    const systemPrompt = `당신은 의료기기 트러블슈팅 도우미입니다.
사용자는 간호사 또는 임상병리사로 기계적 전문 지식은 없지만 장비를 직접 조작합니다.

[응답 규칙]
1. 반드시 아래 메뉴얼 참고 내용만을 기반으로 답변하세요.
2. 반드시 한국어로, 번호 목록 형식으로 쉽고 명확하게 설명하세요.
3. 공구 사용, 케이스 분해, PCB·보드·센서 교체, 배선 점검, 캘리브레이션 등 기술적 작업이 필요하면:
   "⚠️ 의공파트에 수리를 의뢰하세요." 라고 명확히 안내하세요.
4. 사용자가 직접 할 수 있는 조치:
   - IV 세트(수액 라인) 다시 장착
   - 도어(문) 열었다 닫기
   - STOP 키를 눌러 알람 해제 후 재시작
   - 배터리 충전 또는 교체 (Ni-MH 16.8V 2100mAh)
   - 퓨즈 교체 (250V T3.15A, 후면 퓨즈 홀더)
   - 외부 케이스 청소 (따뜻한 물에 적신 천으로 닦기)
5. 메뉴얼에 없는 내용은 "메뉴얼에서 확인되지 않습니다. 의공파트에 문의하세요."라고 하세요.
6. 답변은 간결하고 명확하게 작성하세요.

[메뉴얼 참고 내용]
${contextText}`;

    const history = (messages || []).slice(-8).map(m => ({
      role: m.role === 'bot' ? 'assistant' : m.role,
      content: m.content,
    }));

    const reply = await ollamaChat(systemPrompt, [
      ...history,
      { role: 'user', content: userMessage },
    ]);

    res.json({ reply });
  } catch (err) {
    console.error('Chat error:', err.message);
    if (err.message.includes('Ollama') || err.message.includes('fetch')) {
      res.status(503).json({ error: 'AI 서버(Ollama)에 연결할 수 없습니다. Ollama가 실행 중인지 확인하세요.' });
    } else {
      res.status(500).json({ error: '서버 오류가 발생했습니다.' });
    }
  }
});

// ── Ollama 상태 API ────────────────────────────────────────────────────

app.get('/api/status', async (req, res) => {
  const ollamaOk = await checkOllama();
  const docsCount = listDocuments().length;
  res.json({ ollama: ollamaOk, model: OLLAMA_MODEL, docsCount });
});

// ── 관리자 인증 ────────────────────────────────────────────────────────

app.post('/admin/auth', (req, res) => {
  if (req.body.password === process.env.ADMIN_PASSWORD) {
    res.json({ success: true });
  } else {
    res.status(401).json({ error: '비밀번호가 틀렸습니다.' });
  }
});

// ── 관리자 문서 API ────────────────────────────────────────────────────

app.get('/admin/documents', adminAuth, (req, res) => {
  res.json(listDocuments());
});

app.post('/admin/upload', adminAuth, upload.single('pdf'), async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  const send = (data) => res.write(`data: ${JSON.stringify(data)}\n\n`);

  try {
    if (!req.file) { send({ error: 'PDF 파일을 선택하세요.' }); return res.end(); }
    if (!req.file.originalname.toLowerCase().endsWith('.pdf')) {
      send({ error: 'PDF 파일만 업로드 가능합니다.' }); return res.end();
    }

    const docId = `doc_${Date.now()}`;
    const docName = (req.body.docName || req.file.originalname).replace(/\.pdf$/i, '');

    const result = await addDocument(docId, docName, req.file.buffer, (msg) => send({ status: msg }));
    send({ done: true, docId, docName, chunkCount: result.chunkCount });
  } catch (err) {
    console.error('Upload error:', err);
    send({ error: err.message });
  }
  res.end();
});

app.delete('/admin/documents/:id', adminAuth, (req, res) => {
  removeDocument(req.params.id);
  res.json({ success: true });
});

// ── 관리자 페이지 ──────────────────────────────────────────────────────

app.get('/admin', (req, res) => {
  res.sendFile(join(__dirname, 'public', 'admin.html'));
});

// ── 서버 시작 ──────────────────────────────────────────────────────────

const PORT = process.env.PORT || 3000;
app.listen(PORT, async () => {
  console.log(`\n🏥 의료기기 RAG 챗봇 (폐쇄망 모드)`);
  console.log(`   사용자: http://localhost:${PORT}`);
  console.log(`   관리자: http://localhost:${PORT}/admin`);
  const ok = await checkOllama();
  const embedModel = process.env.OLLAMA_EMBED_MODEL || 'bge-m3:latest';
  console.log(`   LLM:    ${ok ? `✅ ${OLLAMA_MODEL}` : '❌ 연결 안 됨'}`);
  console.log(`   임베딩: ${ok ? `✅ ${embedModel}` : '❌ 연결 안 됨'}\n`);
});
