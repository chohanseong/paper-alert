"""
매일 지정한 저널 + 키워드에 해당하는 신규 논문을 검색해 이메일로 발송하는 스크립트.

동작 방식:
1. Crossref API로 저널(container-title) 기준 최근 논문을 조회
2. 제목/초록에 키워드가 포함된 논문만 필터링
3. 이미 발송한 논문(DOI)은 sent_dois.json에 기록해 중복 발송 방지
4. Gmail SMTP로 이메일 발송

필요한 환경변수 (GitHub Actions Secrets 등록):
- MAIL_SENDER      : 보내는 Gmail 주소
- MAIL_APP_PASSWORD: Gmail 앱 비밀번호 (일반 로그인 비밀번호 아님)
- MAIL_RECEIVER    : 받는 이메일 주소 (본인 이메일)
"""

import os
import json
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---- 설정: 원하는 저널과 키워드를 여기서 수정 ----
JOURNALS = [
    "Nature Communications",
    "Advanced Materials",
]

KEYWORDS = [
    "transistor",
    "synaptic",
    "neuromorphic",
    "reservoir computing",
]

LOOKBACK_DAYS = 2  # 매일 실행되므로 이틀치 정도 여유를 두고 조회 (누락 방지)
SENT_DB_PATH = "sent_dois.json"
CROSSREF_API = "https://api.crossref.org/works"


def load_sent_dois():
    if os.path.exists(SENT_DB_PATH):
        with open(SENT_DB_PATH, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_dois(dois):
    with open(SENT_DB_PATH, "w") as f:
        json.dump(list(dois), f)


def search_journal(journal_name, since_date):
    """Crossref에서 특정 저널의 최근 논문을 가져온다."""
    params = {
        "query.container-title": journal_name,
        "filter": f"from-pub-date:{since_date},type:journal-article",
        "rows": 50,
        "sort": "published",
        "order": "desc",
    }
    resp = requests.get(CROSSREF_API, params=params, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("message", {}).get("items", [])

    # container-title이 정확히 일치하는 것만 남김 (Crossref 검색이 느슨해서 필터링 필요)
    filtered = []
    for item in items:
        titles = item.get("container-title", [])
        if any(journal_name.lower() in t.lower() for t in titles):
            filtered.append(item)
    return filtered


def matches_keywords(item):
    text = " ".join(item.get("title", []) + item.get("abstract", "").split())
    text = text.lower()
    # abstract 필드가 없는 경우가 많으므로 title 위주로 체크
    title_text = " ".join(item.get("title", [])).lower()
    combined = title_text + " " + item.get("abstract", "").lower()
    return any(kw.lower() in combined for kw in KEYWORDS)


def format_entry(item, journal_name):
    title = " ".join(item.get("title", ["(제목 없음)"]))
    doi = item.get("DOI", "")
    url = item.get("URL", f"https://doi.org/{doi}")
    authors = item.get("author", [])
    author_names = ", ".join(
        f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors[:5]
    )
    if len(authors) > 5:
        author_names += " 외"
    return f"""
    <div style="margin-bottom:20px;padding:12px;border-left:3px solid #4A90D9;">
        <div style="font-size:13px;color:#4A90D9;font-weight:bold;">{journal_name}</div>
        <div style="font-size:15px;font-weight:bold;margin:4px 0;">
            <a href="{url}" style="color:#111;text-decoration:none;">{title}</a>
        </div>
        <div style="font-size:12px;color:#666;">{author_names}</div>
        <div style="font-size:12px;color:#999;">DOI: {doi}</div>
    </div>
    """


def send_email(entries):
    sender = os.environ["MAIL_SENDER"]
    app_password = os.environ["MAIL_APP_PASSWORD"]
    receiver = os.environ["MAIL_RECEIVER"]

    today = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[논문 알림] {today} - 신규 논문 {len(entries)}건"
    msg["From"] = sender
    msg["To"] = receiver

    if entries:
        body_html = "<html><body>" + "".join(entries) + "</body></html>"
    else:
        body_html = "<html><body><p>오늘은 조건에 맞는 신규 논문이 없습니다.</p></body></html>"

    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, receiver, msg.as_string())


def main():
    since_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    sent_dois = load_sent_dois()
    new_entries_html = []
    newly_sent = set()

    for journal in JOURNALS:
        try:
            items = search_journal(journal, since_date)
        except requests.RequestException as e:
            print(f"[경고] {journal} 조회 실패: {e}")
            continue

        for item in items:
            doi = item.get("DOI")
            if not doi or doi in sent_dois:
                continue
            if not matches_keywords(item):
                continue
            new_entries_html.append(format_entry(item, journal))
            newly_sent.add(doi)

    # 이메일은 신규 논문이 없어도 항상 보낼지, 있을 때만 보낼지 선택 가능
    # 기본: 신규 논문이 있을 때만 발송
    if new_entries_html:
        send_email(new_entries_html)
        print(f"이메일 발송 완료: {len(new_entries_html)}건")
    else:
        print("신규 논문 없음 - 이메일 발송 생략")

    save_sent_dois(sent_dois | newly_sent)


if __name__ == "__main__":
    main()
