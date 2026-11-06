"""
fetch_latest.py
---------------
settings.json 에 적힌 RSS 주소마다 최신 글 3개를 가져와
제목·링크·날짜를 JSON 으로 출력합니다.

사용법:
    python3 fetch_latest.py

필요한 외부 패키지 없음 — Python 기본 모듈만 사용합니다.
"""

import json                        # JSON 읽기/쓰기
import ssl                         # SSL 인증서 오류 우회 (Mac 한정)
import urllib.request              # URL 로 HTTP 요청 보내기
import xml.etree.ElementTree as ET # XML 파싱
from pathlib import Path           # 파일 경로 다루기

# Mac 에서 Python 자체 SSL 인증서가 없을 때 발생하는 오류를 우회합니다.
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode   = ssl.CERT_NONE

# ─────────────────────────────────────────────
# 1. settings.json 읽기
# ─────────────────────────────────────────────
# Path(__file__).parent → 이 스크립트가 있는 폴더
settings_path = Path(__file__).parent / "settings.json"

with open(settings_path, encoding="utf-8") as f:
    settings = json.load(f)        # JSON → 파이썬 딕셔너리

feeds = settings["feeds"]          # 피드 목록 (name + url)

# ─────────────────────────────────────────────
# 2. 각 피드에서 최신 글 3개 수집
# ─────────────────────────────────────────────
result = []  # 최종적으로 담을 리스트

for feed in feeds:
    name = feed["name"]
    url  = feed["url"]

    # HTTP 요청 — User-Agent 를 넣어야 일부 사이트가 거부하지 않음
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (news-briefing/1.0)"},
    )

    # urlopen 이 성공하면 XML 바이트를 받아 파싱
    with urllib.request.urlopen(request, timeout=15, context=SSL_CTX) as response:
        xml_bytes = response.read()

    # XML 문자열을 트리 구조로 변환
    root = ET.fromstring(xml_bytes)

    # RSS 의 각 기사는 <item> 태그 안에 들어 있음
    # root.iter("item") → 문서 전체에서 <item> 을 순서대로 찾아줌
    articles = []
    for item in root.iter("item"):

        # findtext() 는 태그가 없으면 None 대신 "" 반환
        title   = item.findtext("title",   "").strip()
        link    = item.findtext("link",    "").strip()
        pub_date = item.findtext("pubDate", "").strip()

        # RSS <title> 은 종종 CDATA 로 감싸져 파이썬이 텍스트만 꺼냄
        articles.append({
            "feed"    : name,
            "title"   : title,
            "link"    : link,
            "date"    : pub_date,
        })

        # 3개를 모으면 이 피드는 더 볼 필요 없음
        if len(articles) == 3:
            break

    result.extend(articles)

# ─────────────────────────────────────────────
# 3. JSON 으로 화면에 출력
# ─────────────────────────────────────────────
# ensure_ascii=False → 한글이 \uXXXX 로 깨지지 않게
# indent=2          → 보기 좋게 들여쓰기
print(json.dumps(result, ensure_ascii=False, indent=2))

