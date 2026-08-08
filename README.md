# 논문 알림 자동 발송 시스템 (Paper Alert)

매일 아침 9시(한국 시간, KST)에 지정한 학술지에서 지정한 키워드가 제목 또는
초록에 포함된 신규 논문을 검색해서 이메일로 자동 발송하는 시스템입니다.

- 검색 대상 학술지, 키워드는 `paper_alert.py` 상단에서 쉽게 수정할 수 있습니다.
- 논문 검색: Crossref API + Semantic Scholar API (두 결과를 DOI 기준으로 합쳐서 중복 제거)
- 이메일 발송: Gmail SMTP (앱 비밀번호 방식)
- 스케줄링: GitHub Actions (매일 자동 실행, 수동 실행도 가능)
- 발송 이력(`sent_history.json`)을 저장해서 같은 논문이 중복으로 여러 번
  발송되지 않도록 합니다.

이 문서는 GitHub나 GitHub Actions를 처음 다뤄보는 분도 그대로 따라 할 수
있도록 단계별로 작성했습니다. 순서대로 진행하세요.

---

## 목차

1. [로컬에 git 설치 및 확인](#1-로컬에-git-설치-및-확인)
2. [GitHub 저장소 생성 및 로컬 클론](#2-github-저장소-생성-및-로컬-클론)
3. [Gmail 앱 비밀번호 발급](#3-gmail-앱-비밀번호-발급)
4. [GitHub Secrets 등록](#4-github-secrets-등록)
5. [파일 구조](#5-파일-구조)
6. [저널/키워드 수정 방법](#6-저널키워드-수정-방법)
7. [cron 스케줄 시간 변경 (UTC 변환)](#7-cron-스케줄-시간-변경-utc-변환)
8. [로컬 테스트 실행](#8-로컬-테스트-실행)
9. [Actions 탭에서 수동 실행 확인](#9-actions-탭에서-수동-실행-확인)
10. [자주 발생하는 오류와 해결 방법](#10-자주-발생하는-오류와-해결-방법)

---

## 1. 로컬에 git 설치 및 확인

### Windows

1. https://git-scm.com/download/win 에서 설치 파일을 내려받아 실행합니다.
   설치 옵션은 기본값 그대로 "Next"만 눌러도 됩니다.
2. 설치가 끝나면 **PowerShell** 또는 **Git Bash**를 새로 엽니다. (이미 열려
   있던 창은 새로고침이 안 되므로 반드시 새 창을 여세요.)
3. 설치 확인:

   ```powershell
   git --version
   ```

   `git version 2.xx.x.windows.x` 같은 버전이 출력되면 설치가 완료된 것입니다.

4. git을 처음 쓰는 경우 사용자 정보를 등록해야 커밋이 가능합니다.

   ```powershell
   git config --global user.name "본인 이름"
   git config --global user.email "본인 GitHub 가입 이메일"
   ```

### macOS

터미널에서 `git --version`을 입력하면, 설치되어 있지 않을 경우 Xcode Command
Line Tools 설치 안내가 자동으로 뜹니다. 안내에 따라 설치하면 됩니다.

---

## 2. GitHub 저장소 생성 및 로컬 클론

1. https://github.com 에 로그인한 뒤, 우측 상단 `+` 버튼 → **New repository**
   를 클릭합니다.
2. Repository name에 원하는 이름(예: `paper-alert`)을 입력하고,
   Public/Private 중 원하는 공개 범위를 선택한 뒤 **Create repository**를
   누릅니다. (README 등은 추가하지 않고 빈 저장소로 생성해도 됩니다.)
3. 생성된 저장소 페이지에서 초록색 **Code** 버튼을 눌러 저장소 주소(HTTPS)를
   복사합니다. 예시:

   ```
   https://github.com/내계정/paper-alert.git
   ```

4. 로컬 컴퓨터에서 저장소를 클론합니다. (원하는 작업 폴더로 이동한 뒤 실행)

   ```powershell
   git clone https://github.com/내계정/paper-alert.git
   cd paper-alert
   ```

5. 이 프로젝트에서 만든 아래 파일들을 클론한 폴더 안에 그대로 복사해
   넣습니다. (파일 구조는 [5. 파일 구조](#5-파일-구조) 참고)

   - `paper_alert.py`
   - `requirements.txt`
   - `sent_history.json`
   - `.github/workflows/paper-alert.yml`
   - `README.md`

6. 파일을 넣은 뒤 첫 커밋 및 푸시:

   ```powershell
   git add .
   git commit -m "chore: paper alert 시스템 초기 설정"
   git push origin main
   ```

   > 만약 `main` 대신 `master` 브랜치라는 오류가 나면 `git push origin master`
   > 로 시도하거나, `git branch` 명령으로 현재 브랜치 이름을 먼저 확인하세요.

---

## 3. Gmail 앱 비밀번호 발급

Gmail은 보안상 일반 로그인 비밀번호로는 외부 프로그램(SMTP)의 로그인을
허용하지 않습니다. **앱 비밀번호(App Password)**라는 별도의 16자리
비밀번호를 발급받아야 합니다.

### 사전 조건: 2단계 인증(2-Step Verification) 활성화 필수

앱 비밀번호는 Google 계정에 **2단계 인증이 켜져 있어야만** 발급할 수
있습니다.

1. https://myaccount.google.com/security 접속
2. "Google에 로그인하는 방법" 섹션에서 **2단계 인증**을 클릭하고, 켜져
   있지 않다면 안내에 따라 휴대폰 번호 등으로 활성화합니다.

### 앱 비밀번호 발급 절차

1. 2단계 인증을 켠 상태에서 https://myaccount.google.com/apppasswords 로
   이동합니다. (또는 Google 계정 → 보안 → 2단계 인증 페이지 하단의
   "앱 비밀번호" 메뉴로 진입)
2. 앱 이름(예: `paper-alert`)을 입력하고 **만들기(생성)**를 클릭합니다.
3. 화면에 `xxxx xxxx xxxx xxxx` 형태의 16자리 비밀번호가 표시됩니다. 이
   값을 복사해 안전한 곳에 보관하세요. (다시 볼 수 없으므로 꼭 저장)
4. 이 16자리 값이 이후 4단계에서 등록할 `MAIL_APP_PASSWORD` 값입니다.
   (공백은 있어도 없어도 상관없이 동작하지만, 그대로 복사해서 붙여넣는
   것을 권장합니다.)

> 앱 비밀번호 메뉴가 보이지 않는다면 2단계 인증이 아직 활성화되지 않았거나,
> 조직(회사/학교) 계정 정책으로 막혀 있을 수 있습니다. 이 경우 개인 Gmail
> 계정을 사용하는 것을 권장합니다.

### Anthropic API 키 발급 (선택 — AI 초록 요약 기능)

신규 논문의 초록을 한국어 1~2문장으로 요약해서 이메일에 함께 보여주는
기능은 Anthropic API를 사용합니다. 이 키를 등록하지 않아도 나머지 기능
(검색, 중복 제외, 이메일 발송)은 그대로 동작하며, 요약 줄만 빠집니다.

1. https://console.anthropic.com 에 접속해 로그인(또는 가입)합니다.
2. 왼쪽 메뉴에서 **API Keys**로 이동합니다.
3. **Create Key** 버튼을 눌러 이름(예: `paper-alert`)을 입력하고 키를
   생성합니다.
4. 생성된 키(`sk-ant-...`로 시작)를 복사해 안전한 곳에 보관하세요.
   (다시 전체 값을 볼 수 없으므로 꼭 저장해 두어야 합니다.)
5. 이 값이 이후 등록할 `ANTHROPIC_API_KEY` Secret 값입니다.

> Anthropic API는 사용량에 따라 과금됩니다. 이 스크립트는 매일 신규
> 매칭된 논문 개수만큼만 짧은 요약 요청을 보내므로(가볍고 저렴한
> `claude-haiku-4-5` 모델 사용) 비용이 크지 않지만, Anthropic Console의
> Billing 메뉴에서 사용량을 확인하는 것을 권장합니다.

---

## 4. GitHub Secrets 등록

GitHub Actions가 실행될 때 이메일 계정 정보와 API 키를 코드에 직접 적지
않고 안전하게 전달하기 위해 **Repository Secrets**를 사용합니다.

1. GitHub 저장소 페이지 상단의 **Settings** 탭으로 이동합니다.
2. 왼쪽 메뉴에서 **Secrets and variables** → **Actions**를 클릭합니다.
3. **New repository secret** 버튼을 눌러 아래 항목들을 각각 등록합니다.

   | Name | Value | 필수 여부 |
   |---|---|---|
   | `MAIL_SENDER` | 보내는 사람 Gmail 주소 (예: `myaccount@gmail.com`) | 필수 |
   | `MAIL_APP_PASSWORD` | 3단계에서 발급받은 16자리 앱 비밀번호 | 필수 |
   | `MAIL_RECEIVER` | 받는 사람 이메일 주소 (여러 명이면 쉼표로 구분, 예: `a@x.com,b@y.com`) | 필수 |
   | `ANTHROPIC_API_KEY` | 위에서 발급받은 Anthropic API 키 (`sk-ant-...`) | 선택 (AI 초록 요약용, 없으면 요약 없이 발송) |

4. 등록이 끝나면 **Actions** 탭 하위에 등록한 Secret 이름만 보이고 값은
   가려져서 표시되지 않습니다. 정상입니다.

### 워크플로우 쓰기 권한 설정 (필수)

이 워크플로우는 마지막 단계에서 발송 이력 파일(`sent_history.json`)을
저장소에 다시 커밋/푸시합니다. 이를 위해 GitHub Actions가 저장소에 **쓰기
권한**을 가지고 있어야 합니다.

1. 저장소 **Settings** → 왼쪽 메뉴 **Actions** → **General** 로 이동합니다.
2. 페이지 하단 **Workflow permissions** 섹션에서
   **Read and write permissions**를 선택합니다.
3. **Save**를 눌러 저장합니다.

   > 기본값인 "Read repository contents permission"으로 두면 마지막 커밋/
   > 푸시 단계에서 `Permission denied` 오류가 발생합니다.

---

## 5. 파일 구조

저장소 최상위(root)를 기준으로 아래와 같은 구조여야 합니다. 특히
`paper-alert.yml`은 **반드시** `.github/workflows/` 폴더 안에 있어야
GitHub Actions가 인식합니다. 경로가 조금이라도 다르면 워크플로우가 아예
실행되지 않습니다.

```
paper-alert/                          (저장소 최상위)
├── .github/
│   └── workflows/
│       └── paper-alert.yml           # GitHub Actions 워크플로우 (경로 고정)
├── paper_alert.py                    # 메인 스크립트
├── requirements.txt                  # 파이썬 의존성 목록
├── sent_history.json                 # 발송 이력 (자동 생성/갱신됨)
└── README.md                         # 이 문서
```

---

## 6. 저널/키워드 수정 방법

`paper_alert.py` 파일 상단의 "사용자 설정" 구역만 수정하면 됩니다.

```python
# 1) 검색할 학술지 목록
JOURNALS = [
    "Nature Communications",
    "Advanced Materials",
    "ACS Nano",              # 이렇게 줄을 추가해서 저널을 더 넣을 수 있습니다
]

# 2) 검색 키워드 목록 (제목 또는 초록에 하나라도 포함되면 매칭)
KEYWORDS = [
    "Transistor",
    "Synaptic",
    "Neuromorphic",
    "Reservoir Computing",
    "Memristor",              # 새 키워드 추가 예시
]
```

- 학술지 이름은 Crossref/Semantic Scholar에 등록된 정식 영문 명칭을
  사용해야 정확히 매칭됩니다. (저널 홈페이지나 논문의 "journal title"을
  그대로 사용하면 대체로 잘 맞습니다.)
- 키워드는 대소문자를 구분하지 않고, 제목/초록에 **부분 문자열**로
  포함되어 있으면 매칭됩니다. (예: `"Synaptic"` 키워드는 `"synaptic
  plasticity"`에도 매칭됩니다.)
- 수정 후에는 반드시 커밋 & 푸시해야 다음 자동 실행부터 반영됩니다.

  ```powershell
  git add paper_alert.py
  git commit -m "chore: 검색 저널/키워드 수정"
  git push
  ```

새 논문이 없는 날에도 "오늘은 없음" 요약 메일을 받고 싶다면 같은 파일에서
아래 값을 `True`로 바꾸면 됩니다.

```python
SEND_EMPTY_SUMMARY = True
```

---

## 7. cron 스케줄 시간 변경 (UTC 변환)

GitHub Actions의 `schedule: cron` 값은 **항상 UTC(협정 세계시) 기준**으로
해석됩니다. 한국 시간(KST)은 UTC보다 9시간 빠릅니다 (`KST = UTC + 9`).

즉, 원하는 KST 시각에서 9시간을 빼면 UTC 값이 됩니다.

| 원하는 KST 실행 시각 | UTC 계산 | cron 표현식 |
|---|---|---|
| 오전 9:00 | 9 - 9 = 0시 | `0 0 * * *` (현재 기본값) |
| 오전 7:30 | 7:30 - 9 = 전날 22:30 | `30 22 * * *` |
| 오후 6:00 (18:00) | 18 - 9 = 9시 | `0 9 * * *` |
| 자정 0:00 | 0 - 9 = 전날 15시 | `0 15 * * *` |

`.github/workflows/paper-alert.yml`에서 아래 줄을 원하는 값으로 수정하면
됩니다.

```yaml
on:
  schedule:
    - cron: "0 0 * * *"   # 여기를 수정
```

cron 표현식은 `분 시 일 월 요일` 순서입니다. `*`는 "매번"을 의미합니다.
값이 헷갈리면 https://crontab.guru 같은 사이트에 UTC 기준으로 넣어보고
결과를 확인한 뒤, KST로 다시 +9시간 환산해서 검산하는 것을 추천합니다.

> 참고: GitHub Actions의 cron 스케줄은 저장소 활동량에 따라 정각보다 몇 분
> ~ 몇십 분 정도 지연되어 실행될 수 있습니다. 이는 GitHub 인프라의 일반적인
> 특성이며, 정확히 정시에 실행되지 않아도 정상입니다.

---

## 8. 로컬에서 미리 테스트 실행하는 방법

GitHub에 올리기 전, 내 컴퓨터에서 먼저 정상 동작하는지 확인하는 것을
권장합니다.

1. Python이 설치되어 있는지 확인합니다. (3.9 이상 권장)

   ```powershell
   python --version
   ```

   설치되어 있지 않다면 https://www.python.org/downloads/ 에서 설치하세요.
   설치 시 "Add Python to PATH" 옵션을 꼭 체크하세요.

2. `paper-alert` 폴더로 이동한 뒤 의존성을 설치합니다.

   ```powershell
   cd paper-alert
   pip install -r requirements.txt
   ```

3. 환경변수를 설정합니다. (현재 PowerShell 세션에만 적용됩니다)

   ```powershell
   $env:MAIL_SENDER = "myaccount@gmail.com"
   $env:MAIL_APP_PASSWORD = "발급받은16자리앱비밀번호"
   $env:MAIL_RECEIVER = "받는사람@example.com"
   ```

   macOS/Linux(bash)라면:

   ```bash
   export MAIL_SENDER="myaccount@gmail.com"
   export MAIL_APP_PASSWORD="발급받은16자리앱비밀번호"
   export MAIL_RECEIVER="받는사람@example.com"
   ```

4. 스크립트를 실행합니다.

   ```powershell
   python paper_alert.py
   ```

5. 터미널에 진행 로그가 출력되고, 매칭된 신규 논문이 있으면 지정한
   이메일로 메일이 도착합니다. 정상적으로 실행되면 `sent_history.json`
   파일에 발송된 논문의 DOI가 기록됩니다. (같은 논문을 다시 실행해도
   중복 발송되지 않는지 한 번 더 실행해서 확인해 보세요.)

> 테스트 중 실제 메일을 계속 받고 싶지 않다면, `SEND_EMPTY_SUMMARY`를
> `False`로 두고 `KEYWORDS`를 아주 흔한 단어(예: `"a"`)로 잠깐 바꿔서 매칭이
> 잘 되는지만 확인한 뒤 원래 값으로 되돌리는 방법도 있습니다.

---

## 9. Actions 탭에서 수동 실행(workflow_dispatch)으로 확인하는 방법

로컬 테스트가 끝났고 GitHub에 파일을 푸시(2단계, 6단계 참고)했다면, 실제
자동화 환경에서도 정상 동작하는지 수동으로 한 번 실행해볼 수 있습니다.

1. GitHub 저장소 페이지 상단의 **Actions** 탭을 클릭합니다.
2. 왼쪽 워크플로우 목록에서 **Paper Alert**를 클릭합니다.
3. 오른쪽의 **Run workflow** 버튼(드롭다운)을 클릭하고, 다시 한번
   **Run workflow** 버튼을 눌러 실행합니다.

   > 이 버튼이 보이지 않는다면 `.github/workflows/paper-alert.yml` 파일이
   > 아직 `main`(또는 기본) 브랜치에 푸시되지 않은 것입니다. 먼저 커밋 &
   > 푸시를 완료하세요.

4. 실행이 시작되면 목록에 노란 점(진행 중) → 초록 체크(성공) 또는 빨간
   x(실패)로 상태가 표시됩니다. 실행 항목을 클릭하면 `run-paper-alert` 잡
   내부의 각 단계별 로그를 실시간으로 볼 수 있습니다.
5. `Run paper alert script` 단계 로그에서 검색된 논문 수, 이메일 발송
   여부 등을 확인할 수 있습니다.
6. `Commit and push updated send history` 단계에서 `sent_history.json`
   변경 사항이 있으면 자동으로 커밋되고, 저장소에도 반영된 것을
   Commits 히스토리에서 확인할 수 있습니다.

---

## 10. 자주 발생하는 오류와 해결 방법

### `remote: Permission to .../paper-alert.git denied` / `git push` 권한 오류 (로컬)

- 원인: GitHub 계정 인증이 안 되어 있거나, 클론한 저장소에 대한 쓰기 권한이
  없는 계정으로 로그인되어 있는 경우입니다.
- 해결: `git push` 시 뜨는 로그인 창에서 본인 GitHub 계정으로 로그인하세요.
  Personal Access Token(PAT) 인증을 요구하는 경우, GitHub → Settings →
  Developer settings → Personal access tokens에서 토큰을 생성해 비밀번호
  자리에 입력하면 됩니다.

### GitHub Actions에서 `Permission denied` / `remote: Permission to ... denied to github-actions[bot]` (마지막 커밋/푸시 단계)

- 원인: [4단계](#github-secrets-등록)의 "워크플로우 쓰기 권한 설정"을 하지
  않은 경우입니다. 기본값은 읽기 전용입니다.
- 해결: Settings → Actions → General → Workflow permissions에서
  **Read and write permissions**로 변경 후 저장하고 다시 실행하세요.

### `smtplib.SMTPAuthenticationError: (535, ...)` 이메일 인증 오류

- 원인 1: 일반 Gmail 로그인 비밀번호를 사용한 경우. 반드시
  [3단계](#3-gmail-앱-비밀번호-발급)에서 발급한 **앱 비밀번호**를
  사용해야 합니다.
- 원인 2: 2단계 인증이 꺼져 있어서 앱 비밀번호 자체가 유효하지 않게 된
  경우. 2단계 인증을 다시 켜고 앱 비밀번호를 새로 발급하세요.
- 원인 3: `MAIL_SENDER` Secret 값과 실제 앱 비밀번호를 발급받은 Gmail
  계정이 다른 경우. 두 값이 같은 계정인지 확인하세요.

### 워크플로우가 Actions 탭에 아예 보이지 않음

- 원인: `paper-alert.yml` 파일이 `.github/workflows/` 경로가 아닌 다른
  위치(예: 저장소 최상위, `github/workflows/` 등 오타)에 있는 경우입니다.
- 해결: [5. 파일 구조](#5-파일-구조)를 참고해 정확한 경로에 위치시키고
  다시 푸시하세요.

### 매일 정해진 시각에 실행되지 않음 / 몇 분~몇십 분 늦게 실행됨

- 원인: GitHub Actions의 스케줄 실행은 시스템 부하에 따라 지연될 수
  있는 것이 정상적인 동작입니다. (GitHub 공식 문서에서도 명시된 제약)
- 참고: 정시 실행이 매우 중요하다면 5~10분 정도 여유를 두고 판단하세요.

### `requests.exceptions.HTTPError: 429 Too Many Requests` (Semantic Scholar)

- 원인: Semantic Scholar 무료 API는 인증키 없이 사용할 경우 요청 빈도
  제한이 있습니다.
- 해결: 스크립트에 이미 간단한 재시도/대기 로직이 포함되어 있습니다. 계속
  발생한다면 `time.sleep()` 값을 늘리거나, Semantic Scholar에서 무료 API
  키를 발급받아 요청 헤더에 추가하는 것을 고려하세요.

### 새 논문이 있는데 메일이 안 옴

- `sent_history.json`에 해당 DOI가 이미 기록되어 있으면 중복 발송 방지
  로직에 의해 다시 보내지 않습니다. (정상 동작)
- 저널 이름 철자가 Crossref/Semantic Scholar의 정식 명칭과 다르면 검색이
  안 될 수 있습니다. [6단계](#6-저널키워드-수정-방법)를 참고해 정확한
  이름으로 수정하세요.
- `SEARCH_LOOKBACK_DAYS`(기본 4일) 범위 밖의 논문은 검색되지 않습니다.
  너무 오래된 논문을 테스트하려는 경우 이 값을 늘려보세요.
