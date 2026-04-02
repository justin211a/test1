# Marketing Data Pipeline — Claude Code 업무 매뉴얼

## 프로젝트 개요
- **목적**: 메타, 구글 AD, 네이버 SA, GFA 4개 마케팅 채널의 성과 데이터를 BigQuery에 자동 수집
- **기술 스택**: Python 3.12, FastAPI, BigQuery, Cloud Run
- **배포**: GitHub push → Cloud Build → Cloud Run 자동 배포
- **서비스 URL**: https://marketing-pipeline-api-221865276835.asia-northeast3.run.app
- **GCP 프로젝트**: novatra-test (asia-northeast3 서울)
- **회사**: 주식회사 테라바이오텍코리아

## 채널별 연동 현황

| 채널 | 방식 | 계정 |
|------|------|------|
| Meta | Marketing API (facebook-business SDK) | 3계정: act_1359875541588072, act_257163854107840, act_2394931866137036 |
| Google Ads | GAQL (google-ads SDK) | 1계정: 9496631898 |
| 네이버 SA | REST API + HMAC + 비동기 리포트 | 2그룹: novatra1(올뉴비타, CID:3294331), novatra2019(캘리초이스, CID:4048996) |
| GFA | 미연동 (API 파트너 신청 중) | CSV/크롬 익스텐션 폴백 |

## BigQuery 스키마

- **데이터셋**: `novatra-test.marketing_data`
- **테이블**: `ad_performance` (파티셔닝: date, 클러스터링: channel/account_id/campaign_id)
- **뷰**: `ad_performance_view` (CTR/CPC/ROAS 자동 계산)
- **모니터링**: `pipeline_runs` (실행 이력)
- **타임존**: 항상 `Asia/Seoul`
- **통화**: KRW 통일. 환율은 한국수출입은행 API 실시간 조회. exchange_rate 컬럼에 기록.

## 프로젝트 구조

```
src/
  config.py          — 환경변수 로더 (Pydantic BaseSettings, .env 파일)
  schemas.py         — 통합 AdPerformance 모델
  loader.py          — BigQuery 적재 (delete-then-insert 멱등성)
  pipeline.py        — 오케스트레이션, 에러 격리, 알림
  main.py            — FastAPI 앱 (Cloud Run 서비스) + Click CLI
  channels/
    base.py          — BaseChannel ABC (rate limiter 포함)
    meta.py          — Meta Marketing API
    google_ads.py    — Google Ads GAQL
    naver_sa.py      — 네이버 SA 비동기 리포트 (생성→폴링→다운로드)
    gfa.py           — GFA (크롬 익스텐션 수신 + CSV 폴백)
  utils/
    auth.py          — 네이버 HMAC 서명, Meta 토큰 갱신
    exchange_rate.py — 한국수출입은행 환율 API
    notify.py        — Slack 웹훅 + 이메일 알림
scripts/
  run_pipeline.py    — CLI: 파이프라인 실행
  backfill.py        — CLI: 과거 데이터 청크 백필
  setup_bigquery.py  — BigQuery 테이블 생성 (1회)
extension/           — GFA 크롬 익스텐션
```

## 핵심 설계 원칙
- **멱등성**: delete-then-insert (같은 날짜/채널 재실행 안전)
- **에러 격리**: 한 채널 실패해도 나머지 계속 실행
- **Rolling 백필**: 매일 D-7~D-1 수집 (전환 지연 보정)
- **멀티 계정**: 채널별 계정 목록 배열로 관리
- **네이버 SA 특수**: 로그인별 API Key/Secret이 다름 → NaverSAAccountGroup으로 그룹 관리

## 코딩 규칙

### Python
- Python 3.10+ (type hints 사용)
- Pydantic v2 모델로 데이터 검증
- tenacity로 API 재시도 (3회, exponential backoff)
- structlog으로 구조화 로깅

### 데이터 처리
- 파생 지표(CTR, CPC, ROAS)는 저장하지 않음 → BigQuery 뷰에서 계산
- 환율 변환 시 반드시 exchange_rate 컬럼에 적용 환율 기록
- raw_payload JSON 컬럼에 원본 API 응답 보존
- KRW 외 통화는 cost_original, cost_currency 에 원본 보존

### API 연동
- 네이버 SA: 비동기 리포트 3단계 (POST 생성 → GET 폴링 → 다운로드 파싱)
- Meta: adset 레벨 = 광고그룹. actions에서 전환 추출.
- Google Ads: cost_micros를 1,000,000으로 나누기. 계정 통화 자동 감지.
- Rate limiter 필수 (BaseChannel에 내장)

## 환경변수 (.env)
- **절대 git에 커밋하지 말 것** (.gitignore에 등록됨)
- API 키, 토큰, Secret은 .env 파일 또는 Cloud Run 환경변수에만 저장
- Service Account JSON은 config/bigquery/service_account.json (gitignore됨)

## Git 워크플로우
- 브랜치: `claude/automate-marketing-data-import-m5lxH`
- push하면 Cloud Run 자동 배포됨 (Cloud Build 트리거 연결됨)
- 커밋 메시지: 한글 또는 영문, 기능 단위로
- 검증 후 push

## 알림
- **Slack**: `#웹-훅` 채널로 파이프라인 성공/실패/이상감지 알림
- **이메일**: 실패 + 토큰 만료 경고만 (중요 건만)

## CLI 사용법
```bash
# 전체 채널 실행 (rolling 7일)
python scripts/run_pipeline.py

# 특정 채널만
python scripts/run_pipeline.py --channel meta --date yesterday

# 과거 데이터 백필
python scripts/backfill.py --channel google_ads --start 2026-01-01 --end 2026-03-30

# CSV 업로드
python scripts/run_pipeline.py --channel gfa --csv uploads/gfa.csv --date 2026-03-30
```

## Cloud Run API 엔드포인트
- `GET /health` — 상태 확인
- `POST /api/ingest/gfa` — GFA 크롬 익스텐션 데이터 수신
- `POST /api/ingest/csv` — CSV 데이터 수신

## 절대 하지 말 것
- .env 파일이나 service_account.json을 git에 커밋
- 검증 없이 push (Cloud Run에 자동 배포됨)
- BigQuery 테이블 DROP/DELETE 쿼리 (반드시 백업 먼저)
- API 키/토큰을 코드에 하드코딩
- delete-then-insert 외의 방식으로 BigQuery 적재 (멱등성 깨짐)

## 의사결정이 필요한 경우 (대표님에게 물어볼 것)
- 새로운 마케팅 채널 추가
- BigQuery 스키마 변경 (컬럼 추가/삭제)
- 비용이 발생하는 인프라 변경
- API 키/토큰 갱신 (브라우저 로그인 필요)
