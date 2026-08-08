#!/usr/bin/env python3
"""
paper_alert.py

Nature Communications, Advanced Materials 등 지정한 학술지에서
지정한 키워드(제목/초록)가 포함된 신규 논문을 매일 검색해서
이메일로 발송하는 스크립트.

- 논문 검색: Crossref API + Semantic Scholar API (DOI 기준 중복 제거)
- 이메일 발송: Gmail SMTP (앱 비밀번호)
- 발송 이력: sent_history.json (같은 논문 중복 발송 방지)

실행 방법:
    python paper_alert.py

환경변수 (필수):
    MAIL_SENDER        보내는 Gmail 주소 (예: myaccount@gmail.com)
    MAIL_APP_PASSWORD  Gmail 앱 비밀번호 (일반 로그인 비밀번호 아님)
    MAIL_RECEIVER      받는 이메일 주소 (쉼표로 여러 명 지정 가능)

환경변수 (선택, AI 초록 요약용):
    ANTHROPIC_API_KEY  Anthropic API 키. 없으면 초록 요약 없이 기존처럼 동작합니다.
"""

import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape

import requests

try:
    import anthropic
except ImportError:
    anthropic = None

# ============================================================
# ▼▼▼ 사용자 설정 (여기만 수정하면 됩니다) ▼▼▼
# ============================================================

# 1) 검색할 학술지 → 티어 매핑 (단일 관리 지점).
#    저널을 추가/삭제/이동하려면 이 딕셔너리 한 곳만 수정하면 됩니다 —
#    검색 대상 목록(JOURNALS)과 이메일 뱃지·정렬 점수가 여기서 함께 결정됩니다.
#    티어는 아래 TIER_INFO에 정의된 "FLAGSHIP" / "TRENDING" / "FEATURED"
#    중 하나를 사용하세요.
JOURNAL_TIERS = {
    "Nature Materials": "FLAGSHIP",
    "Nature Electronics": "FLAGSHIP",
    "Nature Nanotechnology": "FLAGSHIP",
    "Advanced Materials": "FLAGSHIP",
    "Nature Communications": "TRENDING",
    "Advanced Functional Materials": "TRENDING",
    "ACS Nano": "TRENDING",
    "Advanced Science": "FEATURED",
    "Science Advances": "FEATURED",
}

# 실제 검색에 사용하는 저널 목록은 위 딕셔너리의 키에서 자동으로 만들어집니다.
JOURNALS = list(JOURNAL_TIERS.keys())

# 티어별 이모지/표시 이름/정렬 가중치/카드 강조색(HTML 미리보기용) 정의.
# color는 그라데이션 미지원 클라이언트(Outlook 등)를 위한 단색 폴백입니다.
TIER_INFO = {
    "FLAGSHIP": {
        "emoji": "🏆",
        "label": "Flagship",
        "weight": 100,
        "color": "#7c3aed",
        "gradient": "linear-gradient(135deg, #4f46e5, #9333ea, #db2777)",
    },
    "TRENDING": {
        "emoji": "🔥",
        "label": "Trending",
        "weight": 60,
        "color": "#f97316",
        "gradient": "linear-gradient(135deg, #f97316, #ef4444, #ec4899)",
    },
    "FEATURED": {
        "emoji": "✨",
        "label": "Featured",
        "weight": 30,
        "color": "#ec4899",
        "gradient": "linear-gradient(135deg, #ec4899, #f472b6, #fb923c)",
    },
}

# JOURNAL_TIERS에 등록되지 않은 저널(또는 TIER_INFO에 없는 티어 키)에
# 적용할 기본값 (FEATURED와 동일)
DEFAULT_TIER = {
    "emoji": "✨",
    "label": "Featured",
    "weight": 30,
    "color": "#ec4899",
    "gradient": "linear-gradient(135deg, #ec4899, #f472b6, #fb923c)",
}

# 매칭된 키워드 1개당 추가되는 점수 (점수 = 티어 가중치 + 매칭 키워드 수 × 이 값)
KEYWORD_MATCH_SCORE = 10

# 2) 검색 키워드 목록 (제목 또는 초록에 하나라도 포함되면 매칭)
#    대소문자 구분 없이 검색합니다.
KEYWORDS = [
    "Transistor",
    "Synaptic",
    "Neuromorphic",
    "Ferroelectric",
    "MoS2",
    "Reservoir Computing",
]

# 3) 새 논문이 없는 날에도 "오늘은 새 논문 없음" 요약 메일을 보낼지 여부
SEND_EMPTY_SUMMARY = False

# 4) API 조회 시 최근 며칠치를 살펴볼지 (당일 실행이 실패해도 놓치지 않도록
#    여유를 두되, 실제 중복 발송은 발송 이력 파일이 막아줍니다)
SEARCH_LOOKBACK_DAYS = 4

# 5) 발송 이력 저장 파일 경로 (GitHub Actions가 이 파일을 커밋/푸시합니다)
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_history.json")

# 6) AI 초록 요약에 사용할 모델 (가볍고 저렴한 모델 권장)
ANTHROPIC_SUMMARY_MODEL = "claude-haiku-4-5"

# ============================================================
# ▲▲▲ 사용자 설정 끝 ▲▲▲
# ============================================================

# 테스트 모드: GitHub Actions 수동 실행(workflow_dispatch)에서 skip_dedup
# 입력값을 체크했을 때만 SKIP_DEDUP=true로 전달됨. true면 발송 이력을 무시하고
# 조건에 맞는 논문을 전부 매칭하되, 발송 이력 파일 자체는 갱신하지 않는다
# (그래야 다음 정식 스케줄 실행 때 그 논문들이 여전히 "신규"로 잡힘).
SKIP_DEDUP = os.environ.get("SKIP_DEDUP", "").strip().lower() in ("true", "1", "yes")

CROSSREF_API = "https://api.crossref.org/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

# Crossref "polite pool" 적용을 위한 연락처 이메일.
# User-Agent 헤더와 mailto 쿼리 파라미터 둘 다에 넣어야 확실하게 적용됩니다.
# (polite pool은 더 높은 rate limit과 더 안정적인 응답을 받을 수 있게 해줍니다)
CONTACT_EMAIL = os.environ.get("MAIL_SENDER") or os.environ.get("MAIL_RECEIVER") or "example@example.com"
CROSSREF_HEADERS = {
    "User-Agent": f"paper-alert-script/1.0 (mailto:{CONTACT_EMAIL})"
}

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5  # 재시도 간격: 5s, 10s, 15s ...


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"경고: 발송 이력 파일을 읽는 데 실패했습니다 ({e}). 빈 이력으로 시작합니다.")
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)


def normalize_doi(doi):
    if not doi:
        return None
    return doi.strip().lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def strip_tags(text):
    """Crossref/Semantic Scholar 응답에 섞여 오는 JATS/HTML 태그 제거."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def matches_keywords(title, abstract):
    text = f"{title or ''} {abstract or ''}".lower()
    matched = [kw for kw in KEYWORDS if kw.lower() in text]
    return matched


def get_journal_tier(journal):
    """저널명에 해당하는 티어 정보(emoji/label/weight)를 반환한다.
    JOURNAL_TIERS에 등록되지 않은 저널, 또는 TIER_INFO에 없는 티어 키가
    매핑되어 있는 경우 모두 DEFAULT_TIER로 처리한다."""
    tier_key = JOURNAL_TIERS.get(journal)
    return TIER_INFO.get(tier_key, DEFAULT_TIER)


def score_paper(rec):
    """정렬용 점수 = 저널 티어 가중치 + 매칭된 키워드 수 × KEYWORD_MATCH_SCORE."""
    tier = get_journal_tier(rec["journal"])
    return tier["weight"] + len(rec.get("matched_keywords", [])) * KEYWORD_MATCH_SCORE


def strip_markdown(text):
    """AI 요약 응답에 마크다운 문법이 섞여 나오는 경우를 대비한 안전장치.
    프롬프트로 금지해도 100% 보장되지 않으므로 정규식으로 한 번 더 제거한다."""
    if not text:
        return text
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)  # # 헤더
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **볼드**
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)  # *이탤릭*
    text = re.sub(r"__(.+?)__", r"\1", text)  # __볼드__
    text = re.sub(r"(?m)^[\-\*]\s+", "", text)  # - 목록, * 목록
    text = re.sub(r"\s+", " ", text).strip()
    return text


_anthropic_client = None
_anthropic_unavailable_logged = False


def _get_anthropic_client():
    """Anthropic 클라이언트를 지연 생성한다. 패키지 미설치/키 누락 시 None을 반환."""
    global _anthropic_client, _anthropic_unavailable_logged

    if _anthropic_client is not None:
        return _anthropic_client

    if anthropic is None:
        if not _anthropic_unavailable_logged:
            log("anthropic 패키지가 설치되어 있지 않아 초록 요약을 건너뜁니다.")
            _anthropic_unavailable_logged = True
        return None

    if not os.environ.get("ANTHROPIC_API_KEY"):
        if not _anthropic_unavailable_logged:
            log("ANTHROPIC_API_KEY가 설정되어 있지 않아 초록 요약을 건너뜁니다.")
            _anthropic_unavailable_logged = True
        return None

    _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def summarize_abstract(abstract):
    """초록을 한국어 1~2문장으로 요약한다. 초록이 없거나 API 호출이 실패하면
    None을 반환하고(로그만 남기고) 호출부는 요약 없이 계속 진행한다."""
    if not abstract:
        return None

    client = _get_anthropic_client()
    if client is None:
        return None

    try:
        response = client.messages.create(
            model=ANTHROPIC_SUMMARY_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "다음은 논문 초록이다. 아래 규칙을 반드시 지켜서 핵심 내용만 "
                        "한국어 1~2문장으로 요약해줘. 마크다운 문법(#, *, - 등)을 쓰지 "
                        "말고, 여러 버전을 제시하지 말고, 요약 문장만 출력해줘. "
                        "'본 연구는', '이 논문은', '이 연구에서는' 같은 상투적 서두 "
                        "없이 바로 핵심 내용(무엇을 했고 무엇을 발견했는지)으로 "
                        "시작해줘. 전공 핵심 용어, 소자/재료명, 화학식, 데이터셋명은 "
                        "번역하지 말고 영어 원문 그대로 쓰고, 나머지 문장 구조와 "
                        "조사는 자연스러운 한국어로 써줘. 마치 한국인 연구자가 발표할 "
                        f"때 전문 용어는 영어로 섞어 말하는 것처럼.\n\n초록: {abstract}"
                    ),
                }
            ],
        )
        summary = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        summary = strip_markdown(summary)
        return summary or None
    except Exception as e:
        log(f"초록 요약 실패 (Anthropic API): {e}")
        return None


def request_with_retry(url, *, headers=None, label=""):
    """429(Too Many Requests)나 일시적 네트워크 오류 시 대기 후 재시도하는 공통 GET 요청.

    최대 MAX_RETRIES회 시도하며, 실패하면 None을 반환한다 (호출부에서 빈 결과로 처리).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                log(f"{label} 요청 실패 (재시도 {MAX_RETRIES}회 모두 실패): {e}")
                return None
            wait = RETRY_BACKOFF_SECONDS * attempt
            log(f"{label} 네트워크 오류 ({e}) - {wait}초 대기 후 재시도 ({attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            if attempt == MAX_RETRIES:
                log(f"{label} 429 Too Many Requests - 재시도 {MAX_RETRIES}회 모두 실패, 이번 조회는 건너뜁니다")
                return None
            wait = RETRY_BACKOFF_SECONDS * attempt
            log(f"{label} 429 Too Many Requests - {wait}초 대기 후 재시도 ({attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            log(f"{label} HTTP 오류: {e}")
            return None

        return resp

    return None


def fetch_from_crossref(journal, since_date):
    """Crossref API에서 특정 저널의 최근 논문을 가져온다."""
    results = []
    params = {
        # Crossref의 container-title 필터는 값에 따옴표를 넣으면 안 되고,
        # 있는 그대로의 문자열과 정확히(대소문자 무시) 일치해야 매칭됩니다.
        # (query.container-title 같은 관련도 기반 검색은 sort=published와 함께
        #  쓰면 관련도 순위가 무시되어 엉뚱한 저널이 섞여 들어오므로 사용하지 않음)
        "filter": f"container-title:{journal},from-pub-date:{since_date},type:journal-article",
        "rows": 100,
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,abstract,container-title,published,URL,author",
        "mailto": CONTACT_EMAIL,  # polite pool 적용 (User-Agent와 이중 적용)
    }
    url = f"{CROSSREF_API}?{urllib.parse.urlencode(params)}"
    resp = request_with_retry(url, headers=CROSSREF_HEADERS, label=f"[{journal}] Crossref")
    if resp is None:
        return results
    items = resp.json().get("message", {}).get("items", [])

    with_abstract = 0
    for item in items:
        doi = normalize_doi(item.get("DOI"))
        if not doi:
            continue
        title = strip_tags(" ".join(item.get("title", []) or []))
        abstract = strip_tags(item.get("abstract", "") or "")
        if abstract:
            with_abstract += 1

        results.append(
            {
                "doi": doi,
                "title": title,
                "abstract": abstract,
                # container-title이 저널명의 변형 표기를 담고 있을 수 있으므로
                # 이메일에는 사용자가 설정한 정식 저널명을 그대로 표시
                "journal": journal,
                "url": item.get("URL", f"https://doi.org/{doi}"),
                "source": "Crossref",
            }
        )
    log(
        f"[{journal}] Crossref: {len(results)}건 조회 "
        f"(초록 있음 {with_abstract}건 / 없음 {len(results) - with_abstract}건)"
    )
    return results


def fetch_from_semantic_scholar(journal, since_date):
    """Semantic Scholar API에서 특정 저널 + 키워드로 최근 논문을 가져온다."""
    results = []
    # OR로 연결된 키워드 쿼리 (구문 검색을 위해 따옴표로 묶음)
    query = " | ".join(f'"{kw}"' for kw in KEYWORDS)
    today = datetime.now(timezone.utc).date().isoformat()
    params = {
        "query": query,
        "venue": journal,
        "fields": "title,abstract,externalIds,publicationDate,url,venue",
        "publicationDateOrYear": f"{since_date}:{today}",
    }
    url = f"{SEMANTIC_SCHOLAR_API}?{urllib.parse.urlencode(params)}"
    resp = request_with_retry(url, label=f"[{journal}] Semantic Scholar")
    if resp is None:
        return results
    data = resp.json()

    for item in data.get("data", []) or []:
        doi = normalize_doi((item.get("externalIds") or {}).get("DOI"))
        if not doi:
            continue
        results.append(
            {
                "doi": doi,
                "title": strip_tags(item.get("title") or ""),
                "abstract": strip_tags(item.get("abstract") or ""),
                # Semantic Scholar가 반환하는 venue 표기가 축약형/변형일 수 있어
                # 이메일에는 사용자가 설정한 정식 저널명을 그대로 표시
                "journal": journal,
                "url": item.get("url") or f"https://doi.org/{doi}",
                "source": "Semantic Scholar",
            }
        )
    log(f"[{journal}] Semantic Scholar: {len(results)}건 조회")
    return results


def collect_new_papers():
    since_date = (
        datetime.now(timezone.utc).date() - timedelta(days=SEARCH_LOOKBACK_DAYS)
    ).isoformat()
    history = load_history()

    merged = {}  # doi -> record
    journal_dois = {j: set() for j in JOURNALS}  # 저널별 디버그 집계용

    for journal in JOURNALS:
        log(f"[{journal}] Crossref 검색 중...")
        for rec in fetch_from_crossref(journal, since_date):
            merged.setdefault(rec["doi"], rec)
            journal_dois[journal].add(rec["doi"])
        time.sleep(1)  # Crossref API 예절상 약간의 간격

        log(f"[{journal}] Semantic Scholar 검색 중...")
        for rec in fetch_from_semantic_scholar(journal, since_date):
            # 이미 Crossref에서 찾은 논문이면 초록이 비어있을 때만 보강
            if rec["doi"] in merged:
                if not merged[rec["doi"]].get("abstract") and rec.get("abstract"):
                    merged[rec["doi"]]["abstract"] = rec["abstract"]
            else:
                merged[rec["doi"]] = rec
            journal_dois[journal].add(rec["doi"])
        time.sleep(1)  # Semantic Scholar 무료 사용량 보호

    if SKIP_DEDUP:
        log("SKIP_DEDUP=true - 발송 이력 중복 제외를 건너뜁니다 (테스트 모드, 이력 파일은 갱신되지 않음)")

    new_papers = []
    for journal in JOURNALS:
        dois = journal_dois[journal]
        already_sent = 0
        no_keyword_match = 0
        matched = 0
        for doi in dois:
            rec = merged[doi]
            if not SKIP_DEDUP and doi in history:
                already_sent += 1
                continue
            matched_kw = matches_keywords(rec["title"], rec["abstract"])
            if not matched_kw:
                no_keyword_match += 1
                continue
            rec["matched_keywords"] = matched_kw
            rec["summary"] = summarize_abstract(rec["abstract"])
            new_papers.append(rec)
            matched += 1
        # 디버그 로그: 왜 신규 매칭이 0건인지(또는 몇 건인지) 저널별로 추적 가능하게 함
        log(
            f"[{journal}] 요약: Crossref+Semantic Scholar 고유 논문 {len(dois)}건 중 "
            f"발송 이력 있음(중복 제외) {already_sent}건, "
            f"키워드 불일치(제목/초록에 매칭 없음) {no_keyword_match}건, "
            f"신규 매칭 {matched}건"
        )

    # 점수(티어 가중치 + 매칭 키워드 수 × 10) 높은 순으로 정렬
    new_papers.sort(key=score_paper, reverse=True)
    return new_papers, history


def build_email_body(new_papers):
    test_notice = (
        "⚠️ 테스트 모드로 발송됨 (SKIP_DEDUP=true, 중복 필터 비활성화 / 발송 이력 미갱신)\n\n"
        if SKIP_DEDUP
        else ""
    )

    if not new_papers:
        return (
            test_notice
            + "오늘은 조건에 맞는 새 논문이 없습니다.\n\n"
            f"검색 저널: {', '.join(JOURNALS)}\n"
            f"검색 키워드: {', '.join(KEYWORDS)}"
        )

    lines = [test_notice + f"좋은 아침이에요!! 오늘의 신규 논문 알림 ({len(new_papers)}건)이 있습니다\n"]
    for i, p in enumerate(new_papers, 1):
        tier = get_journal_tier(p["journal"])
        lines.append(f"{i}. {tier['emoji']} {tier['label']} | [{p['journal']}] {p['title']}")
        if p.get("summary"):
            lines.append(f"   요약: {p['summary']}")
        lines.append(f"   매칭 키워드: {', '.join(p['matched_keywords'])}")
        lines.append(f"   링크: {p['url']}")
        lines.append("")
    return "\n".join(lines)


# Instagram 특유의 보라-빨강-오렌지 그라데이션 (헤더 배너, 카드 버튼 공통)
HEADER_GRADIENT = "linear-gradient(135deg, #833ab4 0%, #fd1d1d 50%, #fcb045 100%)"


def _render_paper_card_html(p):
    """논문 한 건을 Instagram 피드 느낌의 카드 HTML로 렌더링한다."""
    tier = get_journal_tier(p["journal"])
    title = html_escape(p["title"])
    journal = html_escape(p["journal"])
    url = html_escape(p["url"])

    keyword_tags = "".join(
        f'<span style="display:inline-block;background-color:#f3f4f6;color:#374151;'
        f'font-size:12px;font-weight:600;padding:5px 12px;border-radius:999px;'
        f'margin:0 6px 6px 0;">#{html_escape(kw)}</span>'
        for kw in p["matched_keywords"]
    )

    summary_html = ""
    if p.get("summary"):
        summary_html = (
            '<p style="margin:10px 0 0 0;font-size:14px;line-height:1.6;color:#4b5563;">'
            f'💡 {html_escape(p["summary"])}</p>'
        )

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;margin:0 auto 20px auto;background-color:#ffffff;
              border-radius:16px;border:1px solid #e5e7eb;overflow:hidden;">
  <tr>
    <td style="padding:20px 22px;">
      <span style="display:inline-block;background:{tier['gradient']};background-color:{tier['color']};
                   color:#ffffff;font-size:12px;font-weight:700;padding:6px 14px;border-radius:999px;">
        {tier['emoji']} {tier['label']}
      </span>
      <p style="margin:14px 0 4px 0;font-size:12px;font-weight:700;color:#9333ea;
                text-transform:uppercase;letter-spacing:0.4px;">{journal}</p>
      <p style="margin:0;font-size:17px;font-weight:700;color:#111827;line-height:1.45;">{title}</p>
      {summary_html}
      <div style="margin-top:14px;">{keyword_tags}</div>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
        <tr>
          <td style="border-radius:999px;background:{HEADER_GRADIENT};background-color:#c13584;">
            <a href="{url}" style="display:inline-block;padding:10px 22px;font-size:13px;
                                    font-weight:700;color:#ffffff;text-decoration:none;
                                    border-radius:999px;">
              논문 보러가기 →
            </a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def build_html_email_preview(new_papers):
    """Instagram 피드 스타일의 카드형 HTML 이메일 본문을 생성한다.

    디자인 비교용으로 코드에 남겨둔 함수이며, 실제 발송 경로에는 연결되어
    있지 않다 (실제 발송은 Notion 스타일인 build_html_notion_style 사용).
    """
    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )

    if new_papers:
        cards_section = "".join(_render_paper_card_html(p) for p in new_papers)
    else:
        cards_section = (
            '<p style="text-align:center;color:#6b7280;font-size:14px;padding:24px 0;">'
            "오늘은 조건에 맞는 새 논문이 없습니다.</p>"
        )

    test_banner = ""
    if SKIP_DEDUP:
        test_banner = (
            '<p style="text-align:center;background-color:#fef3c7;color:#92400e;'
            'font-size:13px;font-weight:600;padding:10px;border-radius:10px;'
            'max-width:600px;margin:0 auto 16px auto;">'
            "⚠️ 테스트 모드로 생성됨 (SKIP_DEDUP=true, 중복 필터 비활성화 / 발송 이력 미갱신)</p>"
        )

    journals_str = html_escape(", ".join(JOURNALS))
    keywords_str = html_escape(", ".join(KEYWORDS))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>신규 논문 알림</title>
</head>
<body style="margin:0;padding:0;background-color:#f9fafb;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f9fafb;padding:24px 12px;">
    <tr>
      <td align="center">
        {test_banner}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;">
          <tr>
            <td style="background:{HEADER_GRADIENT};background-color:#c13584;
                       border-radius:20px;padding:32px 24px;text-align:center;">
              <p style="margin:0;font-size:28px;">📚✨</p>
              <p style="margin:8px 0 0 0;font-size:21px;font-weight:800;color:#ffffff;">
                좋은 아침이에요!!
              </p>
              <p style="margin:6px 0 0 0;font-size:14px;color:#ffffff;opacity:0.9;">
                오늘의 신규 논문 알림 · {today_str}
              </p>
              <p style="margin:16px 0 0 0;display:inline-block;background-color:rgba(255,255,255,0.25);
                        color:#ffffff;font-size:14px;font-weight:700;padding:8px 20px;
                        border-radius:999px;">
                🎉 {len(new_papers)}건 매칭
              </p>
            </td>
          </tr>
        </table>
        <div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>
        {cards_section}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;margin-top:8px;">
          <tr>
            <td style="text-align:center;padding:16px 12px;">
              <p style="margin:0 0 6px 0;font-size:11px;color:#9ca3af;">검색 저널: {journals_str}</p>
              <p style="margin:0;font-size:11px;color:#9ca3af;">검색 키워드: {keywords_str}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ============================================================
# 디자인 비교용 대안 스타일 (Notion / Newsletter / Academic)
# 이 중 Notion 스타일(build_html_notion_style)이 최종 확정되어 실제
# 발송 경로(main() → send_email())에 연결되어 있다. Newsletter/Academic과
# 위쪽의 build_html_email_preview()(Instagram 스타일)는 비교용으로 코드에만
# 남겨둔 것으로, 실제 발송에는 사용되지 않는다.
# ============================================================

def _render_card_notion(p):
    """Notion 문서 스타일 카드: 카드 테두리 없이 얇은 구분선, 회색 톤 위주."""
    tier = get_journal_tier(p["journal"])
    title = html_escape(p["title"])
    journal = html_escape(p["journal"])
    url = html_escape(p["url"])

    keyword_tags = "".join(
        f'<span style="display:inline-block;background-color:#eef2ff;color:#4338ca;'
        f'font-size:12px;font-weight:500;padding:3px 10px;border-radius:4px;'
        f'margin:0 6px 6px 0;">{html_escape(kw)}</span>'
        for kw in p["matched_keywords"]
    )

    summary_html = ""
    if p.get("summary"):
        summary_html = (
            '<p style="margin:8px 0 0 0;font-size:14px;line-height:1.65;color:#6b6b6b;">'
            f'💡 {html_escape(p["summary"])}</p>'
        )

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;margin:0 auto;border-bottom:1px solid #e9e9e7;">
  <tr>
    <td style="padding:18px 4px;">
      <span style="display:inline-block;background-color:#f1f1ef;color:#6b6b6b;
                   font-size:11px;font-weight:600;padding:3px 10px;border-radius:4px;">
        {tier['emoji']} {tier['label']}
      </span>
      <p style="margin:10px 0 2px 0;font-size:12px;color:#9b9a97;">📄 {journal}</p>
      <p style="margin:0;font-size:16px;font-weight:600;color:#37352f;line-height:1.4;">{title}</p>
      {summary_html}
      <div style="margin-top:10px;">{keyword_tags}</div>
      <p style="margin:10px 0 0 0;">
        <a href="{url}" style="font-size:13px;color:#2383e2;text-decoration:none;">🔗 원문 보기 →</a>
      </p>
    </td>
  </tr>
</table>
"""


def build_html_notion_style(new_papers):
    """Notion 문서 스타일 HTML 이메일 본문. 화이트/오프화이트 배경,
    카드 대신 구분선, 정보 밀도 높은 '문서 읽는' 느낌.

    최종 확정된 디자인으로, main()의 실제 발송 경로에서 사용된다."""
    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )

    if new_papers:
        rows_section = "".join(_render_card_notion(p) for p in new_papers)
    else:
        rows_section = (
            '<p style="text-align:center;color:#9b9a97;font-size:14px;padding:24px 0;">'
            "오늘은 조건에 맞는 새 논문이 없습니다.</p>"
        )

    test_banner = ""
    if SKIP_DEDUP:
        test_banner = (
            '<p style="text-align:center;background-color:#f1f1ef;color:#6b6b6b;'
            'font-size:12px;padding:8px;border-radius:4px;'
            'max-width:600px;margin:0 auto 12px auto;">'
            "⚠️ 테스트 모드로 생성됨 (SKIP_DEDUP=true)</p>"
        )

    journals_str = html_escape(", ".join(JOURNALS))
    keywords_str = html_escape(", ".join(KEYWORDS))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>신규 논문 알림 (Notion 스타일)</title>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#ffffff;padding:24px 12px;">
    <tr>
      <td align="center">
        {test_banner}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;">
          <tr>
            <td style="padding:8px 4px 20px 4px;border-bottom:1px solid #e9e9e7;">
              <p style="margin:0;font-size:13px;color:#9b9a97;">{today_str}</p>
              <p style="margin:6px 0 0 0;font-size:24px;font-weight:700;color:#37352f;">
                📚 오늘의 논문 노트
              </p>
              <p style="margin:6px 0 0 0;font-size:13px;color:#9b9a97;">
                신규 매칭 {len(new_papers)}건
              </p>
            </td>
          </tr>
        </table>
        {rows_section}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;margin-top:8px;">
          <tr>
            <td style="text-align:center;padding:16px 4px;">
              <p style="margin:0 0 6px 0;font-size:11px;color:#b3b2af;">검색 저널: {journals_str}</p>
              <p style="margin:0;font-size:11px;color:#b3b2af;">검색 키워드: {keywords_str}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _render_card_newsletter(p):
    """Newsletter/Substack 스타일 행: 세리프 폰트, 구분선만 사용하는 리스트형."""
    tier = get_journal_tier(p["journal"])
    title = html_escape(p["title"])
    journal = html_escape(p["journal"])
    url = html_escape(p["url"])
    keywords_str = " · ".join(html_escape(kw) for kw in p["matched_keywords"])
    serif = "Georgia, 'Times New Roman', serif"

    summary_html = ""
    if p.get("summary"):
        summary_html = (
            f'<p style="margin:10px 0 0 0;font-size:15px;line-height:1.7;color:#333333;'
            f'font-family:{serif};">{html_escape(p["summary"])}</p>'
        )

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;margin:0 auto;">
  <tr>
    <td style="padding:22px 0;border-bottom:1px solid #dddddd;">
      <p style="margin:0;font-size:12px;font-weight:700;letter-spacing:0.5px;color:#c65d3b;
                text-transform:uppercase;font-family:{serif};">
        {tier['emoji']} {tier['label']} · {journal}
      </p>
      <p style="margin:8px 0 0 0;font-size:19px;font-weight:700;color:#1a1a1a;line-height:1.4;
                font-family:{serif};">{title}</p>
      {summary_html}
      <p style="margin:10px 0 0 0;font-size:13px;color:#777777;font-family:{serif};">
        키워드: {keywords_str}
      </p>
      <p style="margin:10px 0 0 0;">
        <a href="{url}" style="font-size:14px;color:#c65d3b;text-decoration:underline;
                                font-family:{serif};">원문 보기 →</a>
      </p>
    </td>
  </tr>
</table>
"""


def build_html_newsletter_style(new_papers):
    """Newsletter/Substack 스타일 HTML 이메일 미리보기. 세리프 폰트,
    신문 헤더 느낌의 마스트헤드, 카드 없는 리스트형 레이아웃."""
    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )
    serif = "Georgia, 'Times New Roman', serif"

    if new_papers:
        rows_section = "".join(_render_card_newsletter(p) for p in new_papers)
    else:
        rows_section = (
            f'<p style="text-align:center;color:#777777;font-size:14px;padding:24px 0;'
            f'font-family:{serif};">오늘은 조건에 맞는 새 논문이 없습니다.</p>'
        )

    test_banner = ""
    if SKIP_DEDUP:
        test_banner = (
            f'<p style="text-align:center;color:#c65d3b;font-size:12px;'
            f'font-style:italic;font-family:{serif};max-width:600px;margin:0 auto 8px auto;">'
            "⚠️ 테스트 모드로 생성됨 (SKIP_DEDUP=true)</p>"
        )

    journals_str = html_escape(", ".join(JOURNALS))
    keywords_str = html_escape(", ".join(KEYWORDS))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>신규 논문 알림 (Newsletter 스타일)</title>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;font-family:{serif};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#ffffff;padding:24px 12px;">
    <tr>
      <td align="center">
        {test_banner}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;">
          <tr>
            <td style="text-align:center;padding:12px 0 24px 0;border-bottom:3px solid #1a1a1a;">
              <p style="margin:0;font-size:12px;letter-spacing:2px;color:#777777;
                        text-transform:uppercase;font-family:{serif};">{today_str}</p>
              <p style="margin:10px 0 0 0;font-size:32px;font-weight:700;color:#1a1a1a;
                        font-family:{serif};">☕ 오늘의 논문 브리핑</p>
              <p style="margin:8px 0 0 0;font-size:14px;color:#555555;font-family:{serif};">
                오늘 신규 매칭된 논문 {len(new_papers)}건을 전해드립니다.
              </p>
            </td>
          </tr>
        </table>
        <div style="max-width:600px;margin:0 auto;height:8px;border-bottom:1px solid #1a1a1a;
                    font-size:0;line-height:8px;">&nbsp;</div>
        <div style="height:16px;line-height:16px;font-size:0;">&nbsp;</div>
        {rows_section}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;margin-top:8px;">
          <tr>
            <td style="text-align:center;padding:16px 4px;">
              <p style="margin:0 0 6px 0;font-size:11px;color:#999999;font-family:{serif};">
                검색 저널: {journals_str}
              </p>
              <p style="margin:0;font-size:11px;color:#999999;font-family:{serif};">
                검색 키워드: {keywords_str}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# 티어 라벨 → 별 개수 매핑 (Minimal Academic 스타일 전용)
ACADEMIC_STAR_MAP = {"Flagship": 3, "Trending": 2, "Featured": 1}


def _render_card_academic(p):
    """Minimal Academic 스타일 행: 인용 형식 느낌, 색상 대신 별점으로 티어 표시."""
    tier = get_journal_tier(p["journal"])
    stars = "★" * ACADEMIC_STAR_MAP.get(tier["label"], 1)
    title = html_escape(p["title"])
    journal = html_escape(p["journal"])
    url = html_escape(p["url"])
    keywords_str = ", ".join(html_escape(kw) for kw in p["matched_keywords"])

    summary_html = ""
    if p.get("summary"):
        summary_html = (
            '<p style="margin:10px 0 0 0;font-size:14px;line-height:1.7;color:#333333;">'
            f'{html_escape(p["summary"])}</p>'
        )

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;margin:0 auto;">
  <tr>
    <td style="padding:28px 0;border-bottom:1px solid #dddddd;">
      <p style="margin:0 0 8px 0;font-size:13px;color:#555555;letter-spacing:1px;">
        {stars} {tier['label']}
      </p>
      <p style="margin:0;font-size:16px;color:#000000;line-height:1.5;">
        <strong>{title}.</strong> <em>{journal}</em>.
      </p>
      {summary_html}
      <p style="margin:10px 0 0 0;font-size:13px;color:#555555;">키워드: {keywords_str}</p>
      <p style="margin:8px 0 0 0;">
        <a href="{url}" style="font-size:13px;color:#000000;text-decoration:underline;">
          → 논문 링크
        </a>
      </p>
    </td>
  </tr>
</table>
"""


def build_html_academic_style(new_papers):
    """Minimal Academic 스타일 HTML 이메일 미리보기. 흰 배경 + 검은 텍스트,
    저널명 이탤릭·제목 볼드의 인용 형식, 티어는 별점, 여백을 넉넉하게."""
    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )

    if new_papers:
        rows_section = "".join(_render_card_academic(p) for p in new_papers)
    else:
        rows_section = (
            '<p style="text-align:center;color:#555555;font-size:14px;padding:24px 0;">'
            "오늘은 조건에 맞는 새 논문이 없습니다.</p>"
        )

    test_banner = ""
    if SKIP_DEDUP:
        test_banner = (
            '<p style="text-align:center;color:#555555;font-size:12px;font-style:italic;'
            'max-width:600px;margin:0 auto 8px auto;">'
            "⚠️ 테스트 모드로 생성됨 (SKIP_DEDUP=true)</p>"
        )

    journals_str = html_escape(", ".join(JOURNALS))
    keywords_str = html_escape(", ".join(KEYWORDS))

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>신규 논문 알림 (Academic 스타일)</title>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;
             font-family:Georgia,'Times New Roman',serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#ffffff;padding:24px 12px;">
    <tr>
      <td align="center">
        {test_banner}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;">
          <tr>
            <td style="padding:24px 0 32px 0;text-align:center;">
              <p style="margin:0;font-size:12px;color:#777777;letter-spacing:2px;
                        text-transform:uppercase;">{today_str}</p>
              <p style="margin:12px 0 0 0;font-size:22px;font-weight:700;color:#000000;">
                Daily Literature Digest
              </p>
              <p style="margin:8px 0 0 0;font-size:13px;color:#555555;">
                {len(new_papers)}건의 신규 매칭 논문
              </p>
            </td>
          </tr>
        </table>
        {rows_section}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;margin-top:16px;">
          <tr>
            <td style="text-align:center;padding:20px 4px;">
              <p style="margin:0 0 6px 0;font-size:11px;color:#888888;">검색 저널: {journals_str}</p>
              <p style="margin:0;font-size:11px;color:#888888;">검색 키워드: {keywords_str}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(subject, plain_body, html_body):
    """이메일을 발송한다. HTML을 지원하지 않는 클라이언트를 위해
    plain_body를 fallback으로, html_body(Notion 스타일)를 기본 렌더링
    본문으로 함께 담은 multipart/alternative 메일을 보낸다."""
    sender = os.environ.get("MAIL_SENDER")
    app_password = os.environ.get("MAIL_APP_PASSWORD")
    receiver = os.environ.get("MAIL_RECEIVER")

    if not sender or not app_password or not receiver:
        log("오류: MAIL_SENDER / MAIL_APP_PASSWORD / MAIL_RECEIVER 환경변수가 필요합니다.")
        sys.exit(1)

    receivers = [r.strip() for r in receiver.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)
    # multipart/alternative 관례상 나중에 attach한 파트가 더 선호되는
    # 버전으로 취급되므로 plain을 먼저, html을 나중에 붙인다.
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, receivers, msg.as_string())

    log(f"이메일 발송 완료 -> {receivers}")


def main():
    log("논문 검색 시작")
    log(f"저널: {JOURNALS}")
    log(f"키워드: {KEYWORDS}")
    if SKIP_DEDUP:
        log("테스트 모드 (SKIP_DEDUP=true): 중복 제외 없이 전체 재검색, 발송 이력은 갱신하지 않습니다.")

    new_papers, history = collect_new_papers()
    log(f"신규 매칭 논문 수: {len(new_papers)}")

    if not new_papers and not SEND_EMPTY_SUMMARY:
        log("새 논문이 없고 SEND_EMPTY_SUMMARY=False이므로 이메일을 보내지 않습니다.")
        return

    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )
    test_prefix = "[테스트 모드] " if SKIP_DEDUP else ""
    if new_papers:
        subject = f"{test_prefix}[논문 알림] {today_str} 신규 논문 {len(new_papers)}건"
    else:
        subject = f"{test_prefix}[논문 알림] {today_str} 신규 논문 없음"

    # 실제 발송: Notion 스타일 HTML을 기본으로, plain text는 fallback으로 함께 담는다.
    # (build_html_email_preview/newsletter/academic, build_email_body는 비교용으로
    #  코드에 남겨두되 발송 경로에는 연결하지 않는다.)
    plain_body = build_email_body(new_papers)
    html_body = build_html_notion_style(new_papers)
    send_email(subject, plain_body, html_body)

    if SKIP_DEDUP:
        log("테스트 모드이므로 발송 이력 파일은 갱신하지 않습니다.")
        return

    # 발송한 논문은 이력에 기록하여 중복 발송 방지
    now_iso = datetime.now(timezone.utc).isoformat()
    for p in new_papers:
        history[p["doi"]] = {
            "title": p["title"],
            "journal": p["journal"],
            "sent_at": now_iso,
        }
    save_history(history)
    log("발송 이력 저장 완료")


if __name__ == "__main__":
    main()
