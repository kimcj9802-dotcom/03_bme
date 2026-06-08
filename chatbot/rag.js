import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';

const STORE_PATH = './data/vector_store.json';
const CHUNK_SIZE = 600;
const CHUNK_OVERLAP = 120;
const TOP_K = 5;

const OLLAMA_URL = () => process.env.OLLAMA_URL || 'http://localhost:11434';
const EMBED_MODEL = () => process.env.OLLAMA_EMBED_MODEL || 'bge-m3:latest';

// ── 저장소 ────────────────────────────────────────────────────────────

function loadStore() {
  if (!existsSync(STORE_PATH)) return { documents: [], chunks: [] };
  return JSON.parse(readFileSync(STORE_PATH, 'utf8'));
}

function saveStore(store) {
  mkdirSync('./data', { recursive: true });
  writeFileSync(STORE_PATH, JSON.stringify(store), 'utf8');
}

// ── PDF 텍스트 추출 ───────────────────────────────────────────────────

export async function extractPdfText(pdfBuffer) {
  const uint8 = new Uint8Array(pdfBuffer);
  const doc = await pdfjsLib.getDocument({ data: uint8 }).promise;
  let fullText = '';
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const pageText = content.items.map(item => item.str).join(' ');
    fullText += `\n[${i}페이지]\n${pageText}`;
  }
  return fullText;
}

// ── 청킹 ──────────────────────────────────────────────────────────────

function chunkText(text, docId, docName) {
  const chunks = [];
  let start = 0, index = 0;
  while (start < text.length) {
    const end = Math.min(start + CHUNK_SIZE, text.length);
    const chunk = text.slice(start, end).trim();
    if (chunk.length > 30) {
      chunks.push({ text: chunk, docId, docName, chunkIndex: index++ });
    }
    if (end >= text.length) break;
    start = end - CHUNK_OVERLAP;
  }
  return chunks;
}

// ── Ollama 임베딩 (bge-m3) ─────────────────────────────────────────────

async function embedTexts(texts) {
  const BATCH = 32; // bge-m3 안정적 배치 크기
  const all = [];
  for (let i = 0; i < texts.length; i += BATCH) {
    const batch = texts.slice(i, i + BATCH);
    const res = await fetch(`${OLLAMA_URL()}/api/embed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: EMBED_MODEL(), input: batch }),
    });
    if (!res.ok) throw new Error(`임베딩 오류: ${await res.text()}`);
    const { embeddings } = await res.json();
    all.push(...embeddings);
  }
  return all;
}

// ── 코사인 유사도 ──────────────────────────────────────────────────────

function cosineSim(a, b) {
  let dot = 0, ma = 0, mb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    ma += a[i] * a[i];
    mb += b[i] * b[i];
  }
  return dot / (Math.sqrt(ma) * Math.sqrt(mb) || 1);
}

// ── 관련 청크 검색 ─────────────────────────────────────────────────────

export async function retrieveRelevant(query) {
  const store = loadStore();
  if (store.chunks.length === 0) return [];

  const [queryVec] = await embedTexts([query]);

  const scored = store.chunks.map(c => ({
    text: c.text,
    docName: c.docName,
    score: cosineSim(queryVec, c.embedding),
  }));

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, TOP_K);
}

// ── 문서 추가 ─────────────────────────────────────────────────────────

export async function addDocument(docId, docName, pdfBuffer, onProgress) {
  onProgress?.('PDF 텍스트 추출 중...');
  const text = await extractPdfText(pdfBuffer);

  onProgress?.('텍스트 청킹 중...');
  const chunks = chunkText(text, docId, docName);

  onProgress?.(`임베딩 생성 중... (${chunks.length}개 청크, bge-m3)`);
  const embeddings = await embedTexts(chunks.map(c => c.text));
  const enriched = chunks.map((c, i) => ({ ...c, embedding: embeddings[i] }));

  onProgress?.('저장 중...');
  const store = loadStore();
  store.documents.push({ id: docId, name: docName, uploadedAt: new Date().toISOString(), chunkCount: enriched.length });
  store.chunks.push(...enriched);
  saveStore(store);

  return { chunkCount: enriched.length };
}

// ── 문서 삭제 / 조회 ──────────────────────────────────────────────────

export function removeDocument(docId) {
  const store = loadStore();
  store.documents = store.documents.filter(d => d.id !== docId);
  store.chunks = store.chunks.filter(c => c.docId !== docId);
  saveStore(store);
}

export function listDocuments() {
  return loadStore().documents;
}
