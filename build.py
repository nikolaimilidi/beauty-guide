#!/usr/bin/env python3
"""Статический генератор сайта: Markdown + Jinja2 -> dist/.

Запуск:  python build.py
Статьи:  content/articles/*.md  (frontmatter + markdown)
Ссылки:  data/links.yml, в тексте  [текст]({{aff:ключ}})
"""
import html
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
OUT = ROOT / "dist"
AFF_REL = "sponsored nofollow noopener"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]

warnings = []


def warn(msg):
    warnings.append(msg)
    print("WARN:", msg, file=sys.stderr)


def load_yaml(path):
    return yaml.safe_load(path.read_text("utf-8")) or {}


def to_date(v):
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def ru_date(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def split_front(text, name):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise SystemExit(f"{name}: нет frontmatter (--- ... ---)")
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


site = load_yaml(ROOT / "data" / "site.yml")
cats = load_yaml(ROOT / "data" / "categories.yml")
links = load_yaml(ROOT / "data" / "links.yml")
BASE = site.get("base_path", "").rstrip("/")
SITE_URL = site["site_url"].rstrip("/")


def u(path):
    """Внутренняя ссылка с учётом base_path."""
    return BASE + path


def a(path):
    """Абсолютный URL (canonical, sitemap, OG)."""
    return SITE_URL + BASE + path


def render_body(body, slug, posts, used_aff):
    def aff(m):
        text, key = m.group(1), m.group(2)
        link = links.get(key) or {}
        if not link.get("url"):
            warn(f"{slug}: ссылка '{key}' не заполнена в data/links.yml, выведен обычный текст")
            return html.escape(text)
        used_aff.add(key)
        return (f'<a href="{html.escape(link["url"], quote=True)}" '
                f'rel="{AFF_REL}" target="_blank">{html.escape(text)}</a>')

    def post(m):
        text, target = m.group(1), m.group(2)
        if target not in posts:
            warn(f"{slug}: внутренняя ссылка на несуществующую статью '{target}'")
            return text
        return f"[{text}]({u('/' + target + '/')})"

    body = re.sub(r"\[([^\]]+)\]\(\{\{aff:([a-z0-9_-]+)\}\}\)", aff, body)
    body = re.sub(r"\[([^\]]+)\]\(\{\{post:([a-z0-9-]+)\}\}\)", post, body)
    # внутренние ссылки вида [текст](/about/) получают base_path
    body = re.sub(r"\]\(/(?!/)", lambda m: f"]({BASE}/", body)
    md = markdown.Markdown(
        extensions=["tables", "toc", "attr_list", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    out = md.convert(body)
    return out, md.toc_tokens


def collect_articles():
    raw = {}
    for p in sorted((ROOT / "content" / "articles").glob("*.md")):
        meta, body = split_front(p.read_text("utf-8"), p.name)
        if meta.get("draft") and not os.environ.get("DRAFTS"):
            continue  # черновики видны только при локальной сборке: DRAFTS=1 python build.py
        meta["slug"] = meta.get("slug") or p.stem
        for k in ("title", "description", "category", "date"):
            if k not in meta:
                raise SystemExit(f"{p.name}: нет поля '{k}'")
        if meta["category"] not in cats:
            raise SystemExit(f"{p.name}: неизвестная рубрика '{meta['category']}'")
        meta["date"] = to_date(meta["date"])
        meta["updated"] = to_date(meta.get("updated") or meta["date"])
        raw[meta["slug"]] = (meta, body)
    posts = {}
    for slug, (meta, body) in raw.items():
        used = set()
        html_body, toc = render_body(body, slug, raw, used)
        meta["html"] = html_body
        meta["toc"] = toc if len(toc) >= 3 else []
        meta["aff_used"] = sorted(used)
        posts[slug] = meta
    return posts


def make_env():
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.globals.update(site=site, cats=cats, u=u, a=a, ru_date=ru_date)
    return env


def write(path, content):
    path = OUT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")


def ld(data):
    return json.dumps(data, ensure_ascii=False)


def breadcrumbs_ld(items):
    return {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n, "item": a(p)}
            for i, (n, p) in enumerate(items)
        ],
    }


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "static", OUT / "static")

    env = make_env()
    posts = collect_articles()
    ordered = sorted(posts.values(), key=lambda p: (p["date"], p["slug"]), reverse=True)
    sitemap = []  # (path, lastmod)

    # главная
    write("index.html", env.get_template("index.html").render(
        path="/", title=site["name"], description=site["description"],
        posts=ordered[:12], jsonld=[ld({
            "@context": "https://schema.org", "@type": "WebSite",
            "name": site["name"], "url": a("/"), "inLanguage": "ru"})]))
    sitemap.append(("/", max((p["updated"] for p in ordered), default=date.today())))

    # рубрики
    for key, cat in cats.items():
        cp = [p for p in ordered if p["category"] == key]
        path = f"/{key}/"
        write(f"{key}/index.html", env.get_template("category.html").render(
            path=path, title=f"{cat['title']}: статьи и подборки",
            description=cat["description"], cat=cat, posts=cp,
            crumbs=[("Главная", "/"), (cat["title"], path)],
            jsonld=[ld(breadcrumbs_ld([("Главная", "/"), (cat["title"], path)]))]))
        sitemap.append((path, max((p["updated"] for p in cp), default=date.today())))

    # статьи
    for p in ordered:
        path = f"/{p['slug']}/"
        cat = cats[p["category"]]
        related = [r for r in ordered if r["category"] == p["category"] and r["slug"] != p["slug"]][:4]
        if len(related) < 4:
            related += [r for r in ordered if r["category"] != p["category"]][:4 - len(related)]
        advertisers = [links[k] for k in p["aff_used"]]
        crumbs = [("Главная", "/"), (cat["title"], f"/{p['category']}/"), (p["title"], path)]
        article_ld = {
            "@context": "https://schema.org", "@type": "Article",
            "headline": p["title"], "description": p["description"],
            "datePublished": p["date"].isoformat(), "dateModified": p["updated"].isoformat(),
            "inLanguage": "ru", "mainEntityOfPage": a(path),
            "author": {"@type": "Organization", "name": site["name"]},
            "publisher": {"@type": "Organization", "name": site["name"]},
        }
        write(f"{p['slug']}/index.html", env.get_template("article.html").render(
            path=path, title=p["title"], description=p["description"], p=p, cat=cat,
            related=related, advertisers=advertisers, crumbs=crumbs, og_type="article",
            jsonld=[ld(article_ld), ld(breadcrumbs_ld(crumbs))]))
        sitemap.append((path, p["updated"]))

    # служебные страницы
    for pg in sorted((ROOT / "content" / "pages").glob("*.md")):
        meta, body = split_front(pg.read_text("utf-8"), pg.name)
        slug = meta.get("slug") or pg.stem
        body_html, _ = render_body(body, slug, posts, set())
        path = f"/{slug}/"
        crumbs = [("Главная", "/"), (meta["title"], path)]
        write(f"{slug}/index.html", env.get_template("page.html").render(
            path=path, title=meta["title"], description=meta.get("description", meta["title"]),
            body=body_html, crumbs=crumbs,
            jsonld=[ld(breadcrumbs_ld(crumbs))]))
        sitemap.append((path, to_date(meta.get("updated") or date.today())))

    write("404.html", env.get_template("404.html").render(
        path="/404.html", title="Страница не найдена", description="Страница не найдена"))

    # sitemap.xml, robots.txt
    items = "\n".join(
        f"  <url><loc>{a(p)}</loc><lastmod>{d.isoformat()}</lastmod></url>" for p, d in sitemap)
    write("sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + items + "\n</urlset>\n")
    write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {a('/sitemap.xml')}\n")

    print(f"Готово: {len(ordered)} статей, {len(sitemap)} страниц в sitemap -> {OUT}")
    if warnings:
        print(f"Предупреждений: {len(warnings)}")


if __name__ == "__main__":
    main()
