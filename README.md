# 매일 오전 9시 논문 알림 시스템

지정한 저널(Nature Communications, Advanced Materials)에서 지정한 키워드
(Transistor, Synaptic, Neuromorphic, Reservoir Computing)를 포함한 신규 논문을
매일 오전 9시(KST)에 이메일로 발송합니다.

## 1. 설정 방법

### (1) GitHub 저장소 생성
이 폴더 전체(`paper_alert.py`, `requirements.txt`, `.github/workflows/paper-alert.yml`)를
새 GitHub 저장소에 push 하세요. Private repo로 만들어도 GitHub Actions는 정상 동작합니다.

### (2) Gmail 앱 비밀번호 발급
1. Google 계정 → 보안 → 2단계 인증 활성화
2. https://myaccount.google.com/apppasswords 접속
3. "앱 비밀번호" 생성 (이름은 아무거나, 예: "paper-alert")
4. 생성된 16자리 비밀번호를 복사해둠 (일반 로그인 비밀번호가 아님)

### (3) GitHub Secrets 등록
저장소 → Settings → Secrets and variables → Actions → New repository secret

| Secret 이름 | 값 |
|---|---|
| `MAIL_SENDER` | 보내는 Gmail 주소 |
| `MAIL_APP_PASSWORD` | 위에서 발급한 16자리 앱 비밀번호 |
| `MAIL_RECEIVER` | 받을 이메일 주소 (본인 메일이면 됨) |

### (4) 최초 빈 발송기록 파일 생성
```bash
echo "[]" > sent_dois.json
git add sent_dois.json && git commit -m "init" && git push
```

## 2. 저널/키워드 수정

`paper_alert.py` 상단의 `JOURNALS`, `KEYWORDS` 리스트를 수정하면 됩니다.

```python
JOURNALS = [
    "Nature Communications",
    "Advanced Materials",
    "Nature Electronics",       # 예: 저널 추가
]

KEYWORDS = [
    "transistor",
    "synaptic",
    "neuromorphic",
    "reservoir computing",
    "memristor",                # 예: 키워드 추가
]
```

## 3. 스케줄 시간 수정

`.github/workflows/paper-alert.yml`의 cron은 **UTC 기준**입니다.
GitHub Actions는 UTC만 지원하므로 KST(UTC+9)로 환산해서 넣어야 합니다.

- 오전 9시 KST → `0 0 * * *` (UTC 0시)
- 오전 8시 KST로 바꾸려면 → `0 23 * * *` (전날 UTC 23시)

> 참고: GitHub Actions의 스케줄 트리거는 부하 상황에 따라 정확히 그 분에
> 실행되지 않고 몇 분~몇십 분 늦어질 수 있습니다. 칼같은 정시 실행이 필요하면
> 서버(cron)나 클라우드 스케줄러(AWS EventBridge 등)를 쓰는 게 더 안정적입니다.

## 4. 동작 원리

1. Crossref API(무료, 키 불필요)로 저널명 기준 최근 2일치 논문을 조회
2. 제목/초록에 키워드가 포함된 것만 필터링
3. `sent_dois.json`에 이미 보낸 DOI를 기록해 중복 발송 방지
4. 신규 논문이 있을 때만 Gmail로 이메일 발송 (HTML 형식, 제목/저자/DOI 링크 포함)

## 5. 로컬 테스트

```bash
pip install -r requirements.txt
export MAIL_SENDER="you@gmail.com"
export MAIL_APP_PASSWORD="xxxxxxxxxxxxxxxx"
export MAIL_RECEIVER="you@gmail.com"
python paper_alert.py
```

## 6. 확장 아이디어

- Crossref만으로는 초록(abstract)이 안 나오는 경우가 많아 제목 위주 필터링이 됨
  → Semantic Scholar API를 추가로 붙이면 초록 기반 필터링 정확도가 올라감
  (기존 주간 알림 시스템에서 이미 쓰고 계신 걸로 알고 있어서, 그 로직을 이 스크립트에
  합치는 것도 가능)
- 저널별 ISSN을 직접 지정하면 container-title 매칭보다 훨씬 정확해짐
