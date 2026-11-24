"""Offline profile-link fixtures. HTTP responses and address lookups are synthetic."""
from pathlib import Path
import socket
import ssl
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_readme as check


def public(path="/project", fragment=""):
    return urlunsplit(("https", check.PUBLIC_HOST, path, "", fragment))


class MarkdownTests(unittest.TestCase):
    def test_declared_links_only(self):
        text = "# Demo\n[Guide](docs/guide.md#usage) · [Section](#demo)\n"
        text += "`[code](ignored.md)`\n```sh\n[code](ignored.md)\n```\n"
        self.assertEqual(check.links(text), [(2, "docs/guide.md#usage"), (2, "#demo")])

    def test_empty_and_unsupported_are_reported(self):
        for text in ("", "Only prose", "[label][ref]", "[empty]()", "[unfinished](a",
                     "<a href='x'>label</a>", public(), "![image](preview.png)",
                     "[title](a.md 'title')", "[wrapped\nlabel](a)", "```\n[hidden](a)"):
            with self.subTest(text=text), self.assertRaises(check.CheckError):
                check.links("[ok](guide.md)\n" + text if text not in ("", "Only prose") else text)

    def test_repository_files_and_headings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            readme = root / "README.md"
            readme.write_text("# Same\n## Same\n```md\n# Hidden\n```\n", encoding="utf-8")
            doc = root / "a guide.md"
            doc.write_text("## Usage\n", encoding="utf-8")
            (Path(tmp) / "outside.md").write_text("# Outside\n", encoding="utf-8")
            (root / "outside-link").symlink_to(Path(tmp) / "outside.md")
            for url in ("#same", "#same-1", "a%20guide.md#usage", "./README.md", "/README.md"):
                with self.subTest(url=url):
                    check.check_link(readme, url)
            for url in ("#hidden", "#wrong", "gone.md", "../outside.md", "outside-link", "%zz"):
                with self.subTest(url=url), self.assertRaises(check.CheckError):
                    check.check_link(readme, url)
            doc.write_text("# _Formatted_ heading\n", encoding="utf-8")
            with self.assertRaisesRegex(check.CheckError, "unsupported Markdown heading"):
                check.check_link(readme, "a%20guide.md#formatted-heading")


class HttpTests(unittest.TestCase):
    def test_rendered_anchors(self):
        html = b'<h2 id="user-content-usage">Usage</h2><a name="custom"></a><span id="direct"></span>'
        get = Mock(return_value=(200, None, html))
        for fragment in ("usage", "custom", "direct"):
            with self.subTest(fragment=fragment):
                check.remote_link(public(fragment=fragment), get)
        with self.assertRaisesRegex(check.CheckError, "missing HTML anchor"):
            check.remote_link(public(fragment="gone"), get)
        with self.assertRaisesRegex(check.CheckError, "HTTP 404"):
            check.remote_link(public(), Mock(return_value=(404, None, b"")))

    def test_redirects_preserve_or_replace_the_fragment(self):
        for location, target in (("/new", "old"), ("/new#new", "new")):
            get = Mock(side_effect=[(301, location, b""), (200, None, f'<h2 id="{target}">'.encode())])
            self.assertIn("redirects=1", check.remote_link(public(fragment="old"), get))
            self.assertEqual(get.call_args.args[0].fragment, target)
        for location in (None, "/project", "//private.invalid/path"):
            get = Mock(return_value=(302, location, b""))
            with self.subTest(location=location), self.assertRaises(check.CheckError):
                check.remote_link(public(), get)
            self.assertEqual(get.call_count, 1)

    def test_redirect_limit(self):
        get = Mock(side_effect=[(302, f"/next-{n}", b"") for n in range(11)])
        with self.assertRaisesRegex(check.CheckError, "too many redirects"):
            check.remote_link(public(), get)
        self.assertEqual(get.call_count, 11)

    def test_fragment_requires_a_bounded_html_response(self):
        from unittest.mock import MagicMock

        for media_type, body in (("application/json", b"{}"), ("text/html", b"x" * 9)):
            with self.subTest(media_type=media_type), patch.object(check, "HTTPSConnection") as connect:
                response = MagicMock(status=200)
                response.headers.get_content_type.return_value = media_type
                response.read.return_value = body
                connect.return_value.getresponse.return_value.__enter__.return_value = response
                with patch.object(check, "MAX_HTML", 8), self.assertRaises(check.CheckError):
                    check.github_get(check.checked_parts(public(fragment="section")))

    def test_unapproved_destinations_never_reach_transport(self):
        get = Mock()
        for scheme, host in (("http", check.PUBLIC_HOST), ("https", "127.0.0.1"),
                             ("https", "elsewhere.invalid"), ("https", "user@" + check.PUBLIC_HOST),
                             ("https", check.PUBLIC_HOST + ":8443"), ("file", "")):
            with self.subTest(scheme=scheme, host=host), self.assertRaises(check.CheckError):
                check.remote_link(urlunsplit((scheme, host, "/sample", "", "")), get)
        get.assert_not_called()

    def test_retries_and_certificate_errors(self):
        sleep = Mock()
        get = Mock(side_effect=[TimeoutError(), (503, None, b""), (200, None, b"")])
        check.remote_link(public(), get, sleep)
        self.assertEqual(sleep.call_count, 2)
        for failure in (TimeoutError(), (503, None, b"")):
            get = Mock(side_effect=[failure] * 4)
            with self.assertRaises(check.CheckError):
                check.remote_link(public(), get, sleep)
            self.assertEqual(get.call_count, 4)
        get = Mock(side_effect=ssl.SSLCertVerificationError())
        with self.assertRaises(check.CheckError):
            check.remote_link(public(), get, sleep)
        self.assertEqual(get.call_count, 1)

    def test_private_resolution_is_rejected_before_connecting(self):
        results = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(socket, "getaddrinfo", return_value=results), patch.object(socket, "create_connection") as dial:
            with self.assertRaises(check.CheckError):
                check.public_socket((check.PUBLIC_HOST, 443), 20, None)
            dial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
