"""Поиск в БДУ ФСТЭК России (https://bdu.fstec.ru) без браузера.

Форма поиска: /search/index?q=<запрос>&size=<N>. Результаты бывают
двух типов: «Угроза» -> /threat/ubi.NNN и «Уязвимость» -> /vul/YYYY-NNNNN.
Сервер отдаёт 403 без браузерного User-Agent, поэтому заголовок обязателен.
"""
from __future__ import annotations

import html
import re
import ssl
import subprocess
import urllib.parse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
BASE = "https://bdu.fstec.ru"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def fetch(url: str) -> str:
    """curl -k (на сайте самоподписанный корневой сертификат)."""
    out = subprocess.run(
        ["curl", "-sk", "--max-time", "40", "-H", f"User-Agent: {UA}",
         "-H", "Accept-Language: ru-RU,ru;q=0.9", url],
        capture_output=True,
    )
    return out.stdout.decode("utf-8", "replace")


def _text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def search(query: str, size: int = 100) -> list[dict]:
    """Поиск; возвращает список {kind, id, url, title}."""
    url = f"{BASE}/search/index?q={urllib.parse.quote(query)}&size={size}"
    page = fetch(url)
    out, seen = [], set()
    for m in re.finditer(
        r'<h4>\s*<a href="(/(?:threat|vul)/[^"]+)"[^>]*>(.*?)</a>', page, re.S
    ):
        href, title = m.group(1), _text(m.group(2))
        if href in seen:
            continue
        seen.add(href)
        kind = "Угроза" if href.startswith("/threat/") else "Уязвимость"
        ident = href.rsplit("/", 1)[-1]
        out.append({"kind": kind, "id": ident, "url": BASE + href,
                    "title": title})
    total = re.search(r"Элементы с \d+ по \d+ из (\d+)", page)
    return out, (int(total.group(1)) if total else len(out))


def threat(uid: str) -> str:
    """Полный текст карточки УБИ (например, uid='ubi.001')."""
    return _text(fetch(f"{BASE}/threat/{uid}"))


if __name__ == "__main__":
    import sys
    q = sys.argv[1]
    rows, total = search(q, size=int(sys.argv[2]) if len(sys.argv) > 2 else 100)
    print(f"запрос={q!r} всего={total} получено={len(rows)}")
    for r in rows[:60]:
        mark = "*" if r["kind"] == "Угроза" else " "
        print(f"{mark} [{r['kind']}] {r['id']}: {r['title'][:150]}")
