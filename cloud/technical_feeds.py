"""Pure offline normalization of untrusted feed bytes; no HTTP or storage.

``feed_projection_sha256`` covers normalized feed title, description and raw
publication-date fields. It is not a hash of a fetched article or its bytes.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urljoin
from xml.etree import ElementTree

from .records import content_hash
from .technical_records import canonical_technical_url

ATOM = "{http://www.w3.org/2005/Atom}"


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    try:
        return parsed.astimezone(timezone.utc).isoformat() if parsed and parsed.tzinfo else None
    except OverflowError:
        return None


def _text(node, path):
    found = node.find(path)
    return " ".join("".join(found.itertext()).split()) if found is not None else ""


def _item_url(feed_url, links):
    if not links:
        raise ValueError("invalid link")
    for link in links:
        if (not link or link.startswith("#") or len(link) > 2048
                or any(char.isspace() or ord(char) == 127 for char in link)):
            raise ValueError("invalid link")
    urls = {canonical_technical_url(urljoin(feed_url, link)) for link in links}
    if len(urls) != 1 or any(len(url) > 2048 for url in urls):
        raise ValueError("invalid link")
    return next(iter(urls))


def _author_metadata(authors):
    return {"author_claims": sorted({value[:200] for value in authors})[:10],
            "authorship_ambiguous": len(authors) > 1,
            "author_claims_truncated": len(authors) > 10 or any(len(value) > 200 for value in authors)}


def parse_feed(data, feed_url, *, max_bytes=1_000_000, max_items=100):
    if (not isinstance(feed_url, str) or not feed_url or len(feed_url) > 2048
            or any(char.isspace() or ord(char) == 127 for char in feed_url)):
        raise ValueError("invalid_feed_url")
    try:
        feed_url = canonical_technical_url(feed_url)
    except ValueError:
        raise ValueError("invalid_feed_url") from None
    if (type(max_bytes) is not int or not 1 <= max_bytes <= 2_000_000
            or type(max_items) is not int or not 1 <= max_items <= 500):
        raise ValueError("invalid_feed_limits")
    if not isinstance(data, bytes):
        raise ValueError("unsupported_feed_encoding")
    if len(data) > max_bytes:
        raise ValueError("feed_too_large")
    if b"\x00" in data:
        raise ValueError("unsupported_feed_encoding")
    upper_data = data.upper()
    if b"<!DOCTYPE" in upper_data or b"<!ENTITY" in upper_data:
        raise ValueError("unsafe_xml")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("unsupported_feed_encoding") from None
    declaration = re.match(r'\s*<\?xml\s+[^?]*encoding\s*=\s*(["\'])([^"\']+)\1', text, re.I)
    if declaration and declaration.group(2).lower().replace("-", "") != "utf8":
        raise ValueError("unsupported_feed_encoding")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise ValueError("invalid_feed_xml") from None
    if any("{http://www.w3.org/XML/1998/namespace}base" in node.attrib for node in root.iter()):
        raise ValueError("unsupported_xml_base")
    atom = root.tag == ATOM + "feed"
    if not atom and (root.tag != "rss" or len(root.findall("channel")) != 1):
        raise ValueError("unsupported_feed_format")
    nodes = root.findall(ATOM + "entry") if atom else root.findall("./channel/item")
    if len(nodes) > max_items:
        raise ValueError("feed_item_limit_exceeded")
    items, seen, author_sets, url_authors, duplicate_count, withheld_count = [], {}, {}, {}, 0, 0
    for node in nodes:
        if atom:
            links = [link.get("href") for link in node.findall(ATOM + "link")
                     if link.get("rel", "alternate") == "alternate" and link.get("href")]
            title = _text(node, ATOM + "title")
            excerpt = _text(node, ATOM + "summary") or _text(node, ATOM + "content")
            authors = {_text(author, ATOM + "name") for author in node.findall(ATOM + "author")}
            raw_date = _text(node, ATOM + "published")
        else:
            links = [" ".join("".join(link.itertext()).split()) for link in node.findall("link")]
            title = _text(node, "title")
            excerpt, raw_date = _text(node, "description"), _text(node, "pubDate")
            authors = {" ".join("".join(author.itertext()).split()) for author in node.findall("author")}
        authors.discard("")
        try:
            url = _item_url(feed_url, links)
        except ValueError:
            withheld_count += 1
            continue
        published = _date(raw_date)
        digest = content_hash({"title": title, "description": excerpt, "source_published_at_raw": raw_date})
        key = (url, digest)
        url_authors.setdefault(url, set()).update(authors)
        if key in seen:
            duplicate_count += 1
            if authors:
                author_sets[key].update(authors)
                seen[key].update(_author_metadata(author_sets[key]))
            continue
        item = {"url": url, "title": title[:300], "excerpt": excerpt[:400], "excerpt_truncated": len(excerpt) > 400,
                "published_at": published, "source_published_at_raw": raw_date[:200] if raw_date else None,
                "feed_projection_sha256": digest, **_author_metadata(authors),
                "verified_author": None, "author_verified": False}
        seen[key] = item
        author_sets[key] = authors
        items.append(item)
    for item in items:
        if len(url_authors[item["url"]]) > 1:
            item["authorship_ambiguous"] = True
    return {"feed_url": feed_url, "items": items, "feed_completeness": "not_established",
            "duplicate_count": duplicate_count, "withheld_count": withheld_count}
