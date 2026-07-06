# 버전 변경 이력 (CHANGELOG)

---

## ver.2.1 (2026-07-06) — 현재 최신
파일: `app_v2.1.py` / `index_v2.1.html`

### 주요 변경 (ver.2.0 대비)
| 항목 | 내용 |
|------|------|
| **매칭 속도 개선** | 자산 3000대 기준 10분 이상 → 수십 초. 자산별 개별 API 호출 제거 → `_build_recall_serial_set()`으로 이진탐색+병렬 일괄 수집 전환 |
| **점수 체계 100점 만점화** | 기존 최대치 불명확 → 제조번호(40)+업체명(20)+품목명(30)+분류명(10) 합계 100점 체계 |
| **업체명 유사도 매칭 추가** | 제조사/공급사 vs 회수업체명(ENTP_NAME) 유사도를 매칭 요소로 추가. 니혼코덴↔필립스 오탐 방지 |
| **검토 필요 임계값 상향** | 30점 → **50점** (높은 가능성: 70점 이상 유지) |
| **탭 버튼 UX 개선** | 비선택 상태: 흰 배경+검은 글씨+이모지 원, 선택 상태: 색상 배경+흰 글씨 |
| **테이블 컬럼 정리** | 회수이유·보고일자 컬럼 제거 / 회수업체명 컬럼 추가 |
| **상세조회 모델목록** | 회수 상세 모달에 모델명·제조번호·회수대상량 테이블 표시 (`getSerialNumList01` 이진탐색) |
| **테이블 헤더 고정폭** | 헤더 줄바꿈 방지, 가로 스크롤 지원 |

---

## ver.2.0 (2026-07-01)
파일: `app_v2.0.py` / `index_v2.0.html`

### 주요 변경 (ver.1.2 대비)
| 항목 | 내용 |
|------|------|
| **로깅 강화** | 백엔드 API 오류 원문 반환 |
| **MFDS 필드명 수정** | SERIAL_NUM → MAKE_NO 로 수정 |
| **API 오류 처리 개선** | 상세 에러 메시지 반환 |

---

## ver.1.2 (2026-06-19)
파일: `app_v1.2.py` / `index_v1.2.html`

### 주요 변경 (ver.1.1 대비)
| 항목 | 내용 |
|------|------|
| **식약처 API 연동** | `MdlpRtrvlSleStpgeInfoService02` 연동. 품목명/업체명/기간 필터 회수 검색 |
| **회수 목록 캐시** | 조회 결과 메모리 캐시 → 재조회 없이 매칭 재사용 |
| **자산 매칭 기능** | 엑셀 업로드 → 회수 캐시와 품목명 유사도 매칭 → 높은 가능성/검토 필요 분류 |
| **관리자 모드** | 비밀번호(admin1234) 입력 시 `.admin-only` 메뉴 활성화 |
| **엑셀/PDF 업로드** | openpyxl·pymupdf·pytesseract 지원. 장비 문서 자동 파싱 |

---

## ver.1.1 (2026-06-12)
파일: `app_v1.1.py` / `index_v1.1.html`

### 주요 변경 (ver.1.0 대비)
| 항목 | 내용 |
|------|------|
| **RAG 임베딩 기능 추가** | bge-m3 모델로 문서 임베딩, 코사인 유사도 기반 관련 조각 검색 |
| **의료기기 매뉴얼 문서 내장** | 주요 알람·고장 사례 조각을 DOCS 배열로 내장, 질문에 맞는 조각 자동 선택 |
| **검색 기반 답변** | 단순 LLM 호출 → TOP_K 조각 검색 후 context로 전달하는 RAG 구조로 전환 |
| **회수 알림 기능** | `/api/recall-check` 엔드포인트 추가 (초기 버전) |

---

## ver.1.0 (2026-06-11)
파일: `app_v1.0.py` / `index_v1.0.html`  
(git 커밋: `9f603ed`)

### 최초 기능
| 항목 | 내용 |
|------|------|
| **AI 채팅 기본 구조** | FastAPI(port 8000) + Ollama qwen3.6:35b-a3b 연결, SSE 스트리밍 |
| **단일 HTML UI** | 참고자료 입력, 질문 입력, 실시간 답변 표시, 백엔드 상태 배지 |
| **CORS 지원** | 프론트엔드↔백엔드 분리 구조 |

---

## 버전 되돌리기 방법 (PowerShell)

```powershell
# 원하는 버전 번호로 교체 (예: 1.1, 1.2, 2.0, 2.1)
$ver = "2.0"
Copy-Item "D:\claude ai\TF education\versions\app_v$ver.py"    "D:\claude ai\TF education\app.py" -Force
Copy-Item "D:\claude ai\TF education\versions\index_v$ver.html" "D:\claude ai\TF education\index.html" -Force

# 서버 재시작
python -m uvicorn app:app --port 8000 --reload
```

---

## 버전 파일 목록

| 버전 | app | index |
|------|-----|-------|
| 1.0 | app_v1.0.py | index_v1.0.html |
| 1.1 | app_v1.1.py | index_v1.1.html |
| 1.2 | app_v1.2.py | index_v1.2.html |
| 2.0 | app_v2.0.py | index_v2.0.html |
| 2.1 | app_v2.1.py | index_v2.1.html |

_저장 기준: 각 버전 파일은 해당 시점의 app.py + index.html 스냅샷_
