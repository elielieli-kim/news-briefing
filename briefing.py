#!/usr/bin/env python3
"""
briefing.py
-----------
settings.json 의 RSS 피드에서 기사를 가져와
키워드 매칭 기사를 우선으로, 모자라면 최신 기사로 채워 최대 5개를 선택합니다.
Gemini API 로 각 기사의 세 줄 요약을 생성하고
결과를 news.json 과 index.html 로 저장합니다.

사용법:
    python3 briefing.py        (Mac / Linux)
    python  briefing.py        (Windows)

필요한 외부 패키지 없음 — Python 기본 모듈만 사용합니다.
Gemini API 키는 .env 파일의 GEMINI_API_KEY 에 넣어 주세요.
"""

import json
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# ── 경로 설정 ────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent   # 이 파일이 있는 폴더
SETTINGS_PATH = BASE_DIR / "settings.json"
ENV_PATH      = BASE_DIR / ".env"
OUTPUT_JSON   = BASE_DIR / "news.json"
OUTPUT_HTML   = BASE_DIR / "index.html"

MAX_ARTICLES = 5   # 최종 결과 최대 개수

# Gemini REST API 엔드포인트 (외부 패키지 없이 urllib 로 직접 호출)
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-3.5-flash-lite:generateContent?key={api_key}"
)

# 요약 프롬프트 템플릿
SUMMARY_PROMPT = """아래 기사 정보만 보고 한국어로 세 줄 요약을 써 줘.
- 각 줄은 한 문장, 줄 앞에 "- " 를 붙인다
- 기사 정보에 없는 내용, 숫자, 이름은 절대 쓰지 않는다
- 정보가 부족하면 마지막 줄에 "- 자세한 내용은 원문 확인 필요" 라고 쓴다

제목: {title}
소개문: {description}"""

# Mac 에서 Python 자체 SSL 인증서가 없을 때 발생하는 오류를 우회합니다.
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode   = ssl.CERT_NONE


# ── 1. .env 읽기 ─────────────────────────────────────────────────
def load_env() -> dict:
    """
    .env 파일을 읽어 KEY=VALUE 형식을 딕셔너리로 반환합니다.
    파일이 없거나 키가 없으면 빈 딕셔너리를 반환합니다.
    """
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' 으로 시작하는 주석과 빈 줄은 건너뜁니다
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


# ── 2. settings.json 읽기 ────────────────────────────────────────
def load_settings() -> dict:
    """settings.json 을 읽어 파이썬 딕셔너리로 반환합니다."""
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── 3. RSS URL 에서 XML 가져오기 ─────────────────────────────────
def fetch_xml(url: str) -> ET.Element:
    """URL 로 HTTP GET 요청을 보내 응답 XML 을 파싱해 반환합니다."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (news-briefing/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        return ET.fromstring(resp.read())


# ── 4. HTML 태그 제거 ────────────────────────────────────────────
def strip_html(text: str) -> str:
    """RSS description 안에 섞인 HTML 태그를 없앱니다."""
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


# ── 5. RSS <item> 파싱 ───────────────────────────────────────────
def parse_items(root: ET.Element, feed_name: str) -> list[dict]:
    """
    RSS XML 에서 <item> 태그를 모두 찾아 딕셔너리 리스트로 반환합니다.
    기사 본문 URL 은 별도 요청하지 않고,
    RSS 가 제공하는 범위(제목·링크·날짜·소개문)만 사용합니다.
    """
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    items = []
    for item in root.iter("item"):
        title = strip_html(item.findtext("title", ""))
        link  = (item.findtext("link") or "").strip()
        date  = (
            item.findtext("pubDate", "")
            or item.findtext("dc:date", "", ns)
        ).strip()
        desc  = strip_html(item.findtext("description", ""))
        items.append({
            "feed"       : feed_name,
            "title"      : title,
            "link"       : link,
            "date"       : date,
            "description": desc,
        })
    return items


# ── 6. 키워드 매칭 ───────────────────────────────────────────────
def find_keywords(item: dict, keywords: list[str]) -> list[str]:
    """제목 또는 소개문에 포함된 키워드를 찾아 반환합니다."""
    text = (item["title"] + " " + item["description"]).lower()
    return [kw for kw in keywords if kw.lower() in text]


# ── 7. Gemini API 로 세 줄 요약 생성 ────────────────────────────
def summarize(title: str, description: str, api_key: str) -> str:
    """
    Gemini REST API 를 직접 호출해 세 줄 요약을 반환합니다.
    API 키가 없거나 호출에 실패하면 빈 문자열을 반환합니다.
    (실패해도 프로그램 전체가 멈추지 않도록 try/except 로 감쌉니다.)
    """
    if not api_key:
        return ""

    # 보낼 JSON 데이터를 만듭니다
    prompt = SUMMARY_PROMPT.format(title=title, description=description)
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}]
    }).encode("utf-8")

    url = GEMINI_URL.format(api_key=api_key)
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # 타임아웃을 30초로 설정하고 최대 2회 시도
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as exc:
            if attempt == 0:
                continue   # 한 번 더 재시도
            print(f"    ⚠ 요약 실패: {exc}")
            return ""
    return ""


# ── 8. index.html 생성 ───────────────────────────────────────────
def build_html(articles: list[dict], collected_at: str) -> str:
    """
    기사 목록을 받아 HTML 문자열을 만들어 반환합니다.
    summary 필드가 비어 있으면 "요약을 가져오지 못했습니다." 를 표시합니다.
    """

    def article_html(art: dict) -> str:
        """기사 하나를 <article> 블록으로 변환합니다."""
        keywords_str = " ".join(f'<span class="kw">{k}</span>' for k in art["keywords"])

        # 세 줄 요약: "- 문장" 형태를 <li> 로 변환
        summary_text = art.get("summary", "")
        if summary_text:
            lines = [ln.lstrip("- ").strip() for ln in summary_text.splitlines() if ln.strip()]
            summary_html = "<ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>"
        else:
            summary_html = '<p class="no-summary">요약을 가져오지 못했습니다.</p>'

        return f"""
  <article>
    <h2><a href="{art['link']}" target="_blank">{art['title']}</a></h2>
    <div class="meta">
      <span class="source">📰 {art['feed']}</span>
      {f'<span class="date">🗓 {art["date"]}</span>' if art['date'] else ''}
      {keywords_str}
    </div>
    <div class="summary">{summary_html}</div>
    <a class="read-more" href="{art['link']}" target="_blank">원문 보기 →</a>
  </article>"""

    articles_html = "\n".join(article_html(a) for a in articles)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>독서·출판 아침 브리핑</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
      background: #f5f5f0;
      color: #222;
      padding: 2rem 1rem;
      max-width: 760px;
      margin: 0 auto;
    }}
    header {{ margin-bottom: 2rem; }}
    header h1 {{ font-size: 1.8rem; font-weight: 800; }}
    header .collected {{ color: #888; font-size: 0.9rem; margin-top: 0.4rem; }}
    article {{
      background: #fff;
      border-radius: 12px;
      padding: 1.4rem 1.6rem;
      margin-bottom: 1.2rem;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }}
    article h2 {{ font-size: 1.1rem; line-height: 1.5; margin-bottom: .6rem; }}
    article h2 a {{ color: #111; text-decoration: none; }}
    article h2 a:hover {{ text-decoration: underline; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: .4rem; font-size: .8rem;
             color: #666; margin-bottom: .8rem; align-items: center; }}
    .kw {{
      background: #e8f0fe; color: #3367d6;
      border-radius: 4px; padding: 1px 7px; font-size: .75rem;
    }}
    .summary ul {{ padding-left: 1.2rem; }}
    .summary li {{ margin-bottom: .35rem; line-height: 1.6; font-size: .95rem; }}
    .no-summary {{ color: #aaa; font-size: .9rem; font-style: italic; }}
    .read-more {{
      display: inline-block; margin-top: .8rem;
      font-size: .85rem; color: #3367d6; text-decoration: none;
    }}
    .read-more:hover {{ text-decoration: underline; }}
    footer {{
      text-align: center; font-size: .8rem; color: #aaa; margin-top: 2rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1>📚 독서·출판 아침 브리핑</h1>
    <p class="collected">수집 시각: {collected_at}</p>
  </header>

{articles_html}

  <footer>
    ⚠ AI 요약은 틀릴 수 있음 — 중요한 내용은 원문을 확인하세요.
  </footer>
</body>
</html>"""


# ── 9. 메인 로직 ─────────────────────────────────────────────────
def main() -> None:
    env      = load_env()
    api_key  = env.get("GEMINI_API_KEY", "")
    settings = load_settings()
    keywords: list[str]  = settings.get("keywords", [])
    feeds:    list[dict] = settings.get("feeds",    [])

    if api_key:
        print("🔑 Gemini API 키 확인됨")
    else:
        print("⚠ GEMINI_API_KEY 없음 — 요약 없이 HTML 만 생성합니다")

    # ① 모든 피드에서 기사 수집
    all_items: list[dict] = []
    for feed in feeds:
        name = feed["name"]
        url  = feed["url"]
        print(f"[{name}] 가져오는 중…", flush=True)
        try:
            root  = fetch_xml(url)
            items = parse_items(root, name)
            print(f"  → {len(items)}건 수신")
            all_items.extend(items)
        except Exception as exc:
            print(f"  ✗ 오류: {exc}")

    # ② 키워드 매칭 / 미매칭 분리
    keyword_hits: list[tuple[dict, list[str]]] = []
    no_hits:      list[dict]                   = []
    for item in all_items:
        matched = find_keywords(item, keywords)
        if matched:
            keyword_hits.append((item, matched))
        else:
            no_hits.append(item)

    # ③ 키워드 기사 우선 → 최신 기사로 보충해 최대 5건
    # format_article 대신 description 도 함께 보관 (요약에 필요)
    result: list[dict] = []
    for item, matched in keyword_hits:
        if len(result) >= MAX_ARTICLES:
            break
        result.append({**item, "keywords": matched})
    for item in no_hits:
        if len(result) >= MAX_ARTICLES:
            break
        result.append({**item, "keywords": []})

    # ④ Gemini 로 각 기사 세 줄 요약
    print("\n✨ 요약 생성 중…")
    for art in result:
        print(f"  → {art['title'][:35]}…")
        art["summary"] = summarize(art["title"], art["description"], api_key)

    # ⑤ news.json 저장 (description 제외)
    json_data = [
        {
            "feed"    : a["feed"],
            "title"   : a["title"],
            "link"    : a["link"],
            "date"    : a["date"],
            "keywords": a["keywords"],
            "summary" : a["summary"],
        }
        for a in result
    ]
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ news.json 저장 완료 — {len(json_data)}건")

    # ⑥ index.html 저장
    collected_at = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    html = build_html(json_data, collected_at)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ index.html 저장 완료 → {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
