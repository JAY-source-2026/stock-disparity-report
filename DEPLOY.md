# 배포 & 진행상황 (인수인계 문서)

> 이 문서는 다른 PC/새 세션에서 작업을 **끊김 없이 이어가기 위한** 노트다.
> (원종명 부자되기 프로젝트 — 한국주식 퀀트 대시보드)

## 이 프로젝트가 뭔가
- Flask 대시보드(`dashboard/app.py`, 포트 8899). 프런트는 단일 파일 `dashboard/static/index.html`.
- 실행: `python -m dashboard.app`
- 데이터 소스: 토스 Open API(KRX 실시간), 네이버(목표주가·수급·기업카드), KRX/pykrx(투자자 수급), 야후(REST + **오버나잇 웹소켓** `quant/data/yahoo_stream.py`), FDR(지수·환율·미국), 나스닥 스크리너(미국 시총).
- 핵심 기능: 관심/보유 종목 드래그앤드롭, 캔들차트, 기업 해독 카드, 경제 캘린더, 매크로/명일지표 박스(실시간 오버나잇 🌙).

## 시크릿 (절대 커밋 금지 — .env 는 .gitignore 됨)
`.env.example` 참고. 배포 서버/새 PC에선 **.env 를 직접 새로 만들어야** 함:
- `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` (필수)
- `KRX_ID`, `KRX_PW` (선택 — 투자자 수급 확정치. 없으면 네이버 폴백)
- `FMP_API_KEY` (미사용)
> 실제 값은 원래 PC(C:\claude\dow\.env)에 있음. 채팅/로그/커밋에 절대 노출하지 말 것.

## 목표: PC 꺼져도 폰·PC 어디서든 접속 (무료·안정)
### 배포 로드맵
1. **[진행중] 24시간 서버 확보** — 오라클 클라우드 평생무료 VM
   - 서울 리전은 신규 무료가입 불가 → **일본(도쿄) 리전**으로 결정
   - ⚠️ **현재 막힘**: 오라클 가입이 자동 사기방지로 2회 거부(카드 승인은 됐으나 계정생성 실패). → 하루 뒤 재시도 예정. 계속 막히면 대안(카드 불필요 해외 무료 클라우드 Koyeb/Render/HuggingFace + 깃허브 백업)으로 선회.
2. **코드 올리기** — 서버에서 `git clone` → venv → `pip install -r requirements.txt` → `.env` 작성
3. **자동 실행** — systemd 서비스로 등록(재부팅·크래시 자동 재시작). 운영은 `gunicorn -w 1 --threads 4 -b 0.0.0.0:8899 dashboard.app:app` (웹소켓/캐시가 메모리라 **워커 1개** 필수)
4. **공개 주소** — 처음엔 `http://서버IP:8899`, 이후 DuckDNS(무료 고정주소)+Caddy(무료 HTTPS)
   - 보안 요구 낮음 → 공개 URL로 진행하기로 함(Tailscale 안 씀)

### 접속/방화벽 메모
- 오라클: VCN Security List 인그레스 80·443·8899 열기 + 인스턴스 내부 iptables/ufw도 열어야 함(우분투 이미지 기본 차단).

## 최근 완료된 코드 변경(이 저장소 상태)
- 매크로 박스: 원-달러 환율/원-엔 환율(100엔)/비트코인($) 추가.
- 명일지표: 야후 웹소켓 실시간 오버나잇(🌙)/프리마켓(☀️) 자동 전환, 7초 폴링(`/api/after`). 부분 틱 병합 버그 수정.
- 종목 검색 대소문자 무시(`dashboard/store.py filter_listing`).
- requirements.txt에 pykrx·websocket-client·gunicorn 추가.

## 개발 환경
- 원본 PC: Windows, `C:\claude\dow`, Python 3.11. 프리뷰 서버로 8899 구동.
- 깃허브: https://github.com/JAY-source-2026/stock-disparity-report (main)
- 다른 PC에서 이어받기: `git clone` → `.env` 새로 작성 → `python -m dashboard.app`
