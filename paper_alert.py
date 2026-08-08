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
"""

import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests

# ============================================================
# ▼▼▼ 사용자 설정 (여기만 수정하면 됩니다) ▼▼▼
# ============================================================

# 1) 검색할 학술지 목록 (Crossref / Semantic Scholar에 등록된 정식 명칭 권장)
JOURNALS = [
    "Nature Communications",
    "Advanced Materials",
]

# 2) 검색 키워드 목록 (제목 또는 초록에 하나라도 포함되면 매칭)
#    대소문자 구분 없이 검색합니다.
KEYWORDS = [
    "Transistor",
    "Synaptic",
    "Neuromorphic",
    "Reservoir Computing",
]

# 3) 새 논문이 없는 날에도 "오늘은 새 논문 없음" 요약 메일을 보낼지 여부
SEND_EMPTY_SUMMARY = False

# 4) API 조회 시 최근 며칠치를 살펴볼지 (당일 실행이 실패해도 놓치지 않도록
#    여유를 두되, 실제 중복 발송은 발송 이력 파일이 막아줍니다)
SEARCH_LOOKBACK_DAYS = 4

# 5) 발송 이력 저장 파일 경로 (GitHub Actions가 이 파일을 커밋/푸시합니다)
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_history.json")

# ============================================================
# ▲▲▲ 사용자 설정 끝 ▲▲▲
# ============================================================

CROSSREF_API = "https://api.crossref.org/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

# Crossref API 예절: User-Agent에 연락처를 남기면 더 안정적으로 응답받을 수 있음
CROSSREF_HEADERS = {
    "User-Agent": "paper-alert-script/1.0 (mailto:{})".format(
        os.environ.get("MAIL_SENDER", "example@example.com")
    )
}


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


def fetch_from_crossref(journal, since_date):
    """Crossref API에서 특정 저널의 최근 논문을 가져온다."""
    results = []
    params = {
        # Crossref의 container-title 필터는 값에 따옴표를 넣으면 안 되고,
        # 있는 그대로의 문자열과 정확히(대소문자 무시) 일치해야 매칭됩니다.
        "filter": f"container-title:{journal},from-pub-date:{since_date},type:journal-article",
        "rows": 100,
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,abstract,container-title,published,URL,author",
    }
    url = f"{CROSSREF_API}?{urllib.parse.urlencode(params)}"
    try:
        resp = requests.get(url, headers=CROSSREF_HEADERS, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except requests.RequestException as e:
        log(f"Crossref 조회 실패 ({journal}): {e}")
        return results

    for item in items:
        doi = normalize_doi(item.get("DOI"))
        if not doi:
            continue
        title = strip_tags(" ".join(item.get("title", []) or []))
        abstract = strip_tags(item.get("abstract", "") or "")

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

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                log(f"Semantic Scholar rate limit, {wait}s 대기 후 재시도...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.RequestException as e:
            log(f"Semantic Scholar 조회 실패 ({journal}): {e}")
            return results
    else:
        return results

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
    return results


def collect_new_papers():
    since_date = (
        datetime.now(timezone.utc).date() - timedelta(days=SEARCH_LOOKBACK_DAYS)
    ).isoformat()
    history = load_history()

    merged = {}  # doi -> record
    for journal in JOURNALS:
        log(f"[{journal}] Crossref 검색 중...")
        for rec in fetch_from_crossref(journal, since_date):
            merged.setdefault(rec["doi"], rec)
        time.sleep(1)  # Crossref API 예절상 약간의 간격

        log(f"[{journal}] Semantic Scholar 검색 중...")
        for rec in fetch_from_semantic_scholar(journal, since_date):
            # 이미 Crossref에서 찾은 논문이면 초록이 비어있을 때만 보강
            if rec["doi"] in merged:
                if not merged[rec["doi"]].get("abstract") and rec.get("abstract"):
                    merged[rec["doi"]]["abstract"] = rec["abstract"]
            else:
                merged[rec["doi"]] = rec
        time.sleep(1)  # Semantic Scholar 무료 사용량 보호

    new_papers = []
    for doi, rec in merged.items():
        if doi in history:
            continue
        matched_kw = matches_keywords(rec["title"], rec["abstract"])
        if not matched_kw:
            continue
        rec["matched_keywords"] = matched_kw
        new_papers.append(rec)

    new_papers.sort(key=lambda r: r["journal"])
    return new_papers, history


def build_email_body(new_papers):
    if not new_papers:
        return (
            "오늘은 조건에 맞는 새 논문이 없습니다.\n\n"
            f"검색 저널: {', '.join(JOURNALS)}\n"
            f"검색 키워드: {', '.join(KEYWORDS)}"
        )

    lines = [f"오늘의 신규 논문 알림 ({len(new_papers)}건)\n"]
    for i, p in enumerate(new_papers, 1):
        lines.append(f"{i}. [{p['journal']}] {p['title']}")
        lines.append(f"   매칭 키워드: {', '.join(p['matched_keywords'])}")
        lines.append(f"   링크: {p['url']}")
        lines.append(f"   (출처: {p['source']})")
        lines.append("")
    return "\n".join(lines)


def send_email(subject, body):
    sender = os.environ.get("MAIL_SENDER")
    app_password = os.environ.get("MAIL_APP_PASSWORD")
    receiver = os.environ.get("MAIL_RECEIVER")

    if not sender or not app_password or not receiver:
        log("오류: MAIL_SENDER / MAIL_APP_PASSWORD / MAIL_RECEIVER 환경변수가 필요합니다.")
        sys.exit(1)

    receivers = [r.strip() for r in receiver.split(",") if r.strip()]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, receivers, msg.as_string())

    log(f"이메일 발송 완료 -> {receivers}")


def main():
    log("논문 검색 시작")
    log(f"저널: {JOURNALS}")
    log(f"키워드: {KEYWORDS}")

    new_papers, history = collect_new_papers()
    log(f"신규 매칭 논문 수: {len(new_papers)}")

    if not new_papers and not SEND_EMPTY_SUMMARY:
        log("새 논문이 없고 SEND_EMPTY_SUMMARY=False이므로 이메일을 보내지 않습니다.")
        return

    today_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )
    if new_papers:
        subject = f"[논문 알림] {today_str} 신규 논문 {len(new_papers)}건"
    else:
        subject = f"[논문 알림] {today_str} 신규 논문 없음"

    body = build_email_body(new_papers)
    send_email(subject, body)

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
