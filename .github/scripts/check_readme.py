"""Check the profile's inline Markdown links, repository files and GitHub section links."""
from contextlib import closing
from html.parser import HTMLParser
from http.client import HTTPSConnection, HTTPException
from ipaddress import ip_address
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


PUBLIC_HOST = "github.com"
MAX_HTML = 4 * 1024 * 1024
INLINE = re.compile(r"`[^`]+`|\[([^\[\]`<>\\]+)\]\(([^\s()<>\\]+)\)")
UNCHECKED = re.compile(r"[\[\]<>`\\@]|://|\bwww\.|&(?:#\w+|\w+);", re.I)


class CheckError(ValueError):
    pass


def prose(text):
    """Yield lines outside top-level fenced code blocks."""
    fence = ""
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            if marker[1][0] in marker[2]:
                raise CheckError(f"line {number}: unsupported fence")
            fence = marker[1]
        else:
            yield number, line
    if fence:
        raise CheckError("unclosed code fence")


def links(text):
    if not text.strip():
        raise CheckError("README is empty or contains only whitespace")
    found = []
    for number, line in prose(text):
        destinations = []

        def token(match):
            if match[2]:
                if match.start() and line[match.start() - 1] == "!":
                    raise CheckError(f"line {number}: image syntax needs review")
                destinations.append((number, match[2]))
            return " "

        rest = INLINE.sub(token, line)
        if UNCHECKED.search(rest) or (destinations and line.startswith(("    ", "\t"))):
            raise CheckError(f"line {number}: unsupported link or Markdown syntax")
        found.extend(destinations)
    if not found:
        raise CheckError("README contains no checkable links")
    return found


def heading_ids(text):
    ids = set()
    for number, line in prose(text):
        if "<" in line:
            raise CheckError(f"line {number}: HTML in Markdown fragment target needs review")
        heading = re.fullmatch(r" {0,3}#{1,6}\s+(.+?)\s*", line)
        if not heading:
            if re.fullmatch(r" {0,3}(?:=+|-+)\s*", line):
                raise CheckError(f"line {number}: setext heading or divider needs review")
            continue
        title = re.sub(r"\s+#+$", "", heading[1])
        if not re.fullmatch(r"[A-Za-z0-9 .,'·-]+", title):
            raise CheckError(f"line {number}: unsupported Markdown heading")
        stem = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
        slug = stem
        suffix = 0
        while slug in ids:
            suffix += 1
            slug = f"{stem}-{suffix}"
        ids.add(slug)
    return ids


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" or (tag == "a" and key == "name"):
                self.ids.add(value)


def checked_parts(url):
    if not url or re.search(r"[\x00-\x20\x7f\\]|%(?![0-9a-fA-F]{2})|&(?:#\w+|\w+);", url):
        raise CheckError("unsupported or ambiguous link destination")
    return urlsplit(url)


def public_socket(address, timeout, source_address):
    # Use the vetted numeric address for the connection. HTTPS still verifies github.com.
    host, port = address
    addresses = [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    if not addresses or any(not ip_address(ip).is_global or ip_address(ip).is_multicast for ip in addresses):
        raise CheckError("destination did not resolve exclusively to public addresses")
    for index, ip in enumerate(addresses):
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError:
            if index == len(addresses) - 1:
                raise


def github_get(parts):
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    with closing(HTTPSConnection(PUBLIC_HOST, timeout=20)) as conn:
        conn._create_connection = public_socket  # Keep the standard TLS/SNI/HTTP handling.
        conn.request("GET", target, headers={"User-Agent": "profile-readme-link-check"})
        with conn.getresponse() as response:
            body = b""
            if parts.fragment and 200 <= response.status < 300:
                if response.headers.get_content_type() != "text/html":
                    raise CheckError("fragment needs an HTML document")
                body = response.read(MAX_HTML + 1)
                if len(body) > MAX_HTML:
                    raise CheckError("HTML exceeds fragment check size limit")
            return response.status, response.getheader("Location"), body


def remote_link(url, get=github_get, sleep=time.sleep):
    visited = set()
    for redirects in range(11):
        parts = checked_parts(url)
        if parts.scheme != "https" or parts.netloc.lower() not in {PUBLIC_HOST, PUBLIC_HOST + ":443"}:
            raise CheckError("only public GitHub HTTPS destinations are supported")
        if url in visited:
            raise CheckError("redirect loop")
        visited.add(url)
        for attempt in range(4):
            try:
                status, location, body = get(parts)
            except ssl.SSLCertVerificationError as error:
                raise CheckError("TLS certificate verification failed") from error
            except (OSError, HTTPException) as error:
                if attempt == 3:
                    raise CheckError(f"request failed after four attempts: {error}") from error
            else:
                if status not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                    break
            sleep(2 ** attempt)
        if status in {301, 302, 303, 307, 308}:
            if not location:
                raise CheckError(f"HTTP {status} without a redirect destination")
            checked_parts(location)
            destination = urljoin(url, location)
            if "#" not in location and parts.fragment:
                destination = urlsplit(destination)._replace(fragment=parts.fragment).geturl()
            url = destination
            continue
        if not 200 <= status < 300:
            raise CheckError(f"HTTP {status}")
        if parts.fragment:
            anchors = Anchors()
            anchors.feed(body.decode("utf-8"))
            anchors.close()
            target = unquote(parts.fragment, errors="strict")
            if not {target, "user-content-" + target} & anchors.ids:
                raise CheckError(f"missing HTML anchor #{target}")
        return f"HTTP {status}, redirects={redirects}"
    raise CheckError("too many redirects")


def check_link(readme, url):
    parts = checked_parts(url)
    if parts.scheme or parts.netloc or url.startswith("//"):
        return remote_link(url)
    if parts.query:
        raise CheckError("relative link query needs review")
    root = readme.parent.resolve()
    name = unquote(parts.path, errors="strict")
    target = (root / name.lstrip("/")).resolve() if name else readme.resolve()
    if not target.is_relative_to(root) or ".git" in target.relative_to(root).parts or not target.is_file():
        raise CheckError("repository file is missing or outside the checkout")
    if parts.fragment:
        if target.suffix.lower() != ".md":
            raise CheckError("local fragment requires a Markdown document")
        if unquote(parts.fragment, errors="strict") not in heading_ids(target.read_text(encoding="utf-8")):
            raise CheckError(f"missing Markdown heading #{parts.fragment}")
    return "repository file and fragment verified" if parts.fragment else "repository file verified"


def main():
    readme = Path("README.md")
    if readme.is_symlink() or not readme.is_file():
        raise CheckError("README must be a regular repository file")
    entries = links(readme.read_text(encoding="utf-8"))
    failed = 0
    for number, url in entries:
        try:
            print(f"README.md:{number} {url} — {check_link(readme, url)}")
        except (OSError, ValueError, RuntimeError) as error:
            print(f"README.md:{number} {url!r} — {error}", file=sys.stderr)
            failed += 1
    print(f"{len(entries)} links, {len(entries) - failed} passed, {failed} failed")
    return int(bool(failed))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        sys.exit(f"README check incomplete: {error}")
