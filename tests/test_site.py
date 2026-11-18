"""Regression checks against copies of a real production build."""

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_site", ROOT / "scripts/check-site.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class SiteTests(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "_site").is_dir():
            self.fail("Build the production site before running these tests")
        preferred = Path("/tmp/opencode")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="pages-check-", dir=preferred if preferred.is_dir() else None
        )
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name) / "site"
        shutil.copytree(ROOT / "_site", self.site)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def post_path(self):
        return self.site / checks.POST.lstrip("/") / "index.html"

    def test_real_build_passes(self):
        checks.check_site(self.site)

    def test_wrong_canonical_host_is_rejected(self):
        home = self.site / "index.html"
        home.write_text(home.read_text().replace(checks.CANONICAL + "/", "https://example.invalid/", 1))
        with self.assertRaisesRegex(RuntimeError, "canonical"):
            checks.check_site(self.site)

    def test_modified_download_is_rejected(self):
        (self.site / checks.SCRIPT.lstrip("/")).write_text("# changed\n")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            checks.check_site(self.site)

    def test_stale_post_checksum_is_rejected(self):
        script = ROOT / checks.SCRIPT.lstrip("/")
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        post = self.post_path()
        self.assertIn(digest, post.read_text())
        post.write_text(post.read_text().replace(digest, "0" * 64))
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            checks.check_site(self.site)

    def test_missing_header_command_is_rejected(self):
        post = self.post_path()
        post.write_text(post.read_text().replace("kernel-devel-uname-r", "wrong-header-package"))
        with self.assertRaisesRegex(RuntimeError, "instruction"):
            checks.check_site(self.site)

    def test_published_development_file_is_rejected(self):
        (self.site / "README.md").write_text("Should not be published\n")
        with self.assertRaisesRegex(RuntimeError, "Development-only"):
            checks.check_site(self.site)

    def test_broken_internal_link_is_rejected(self):
        home = self.site / "index.html"
        home.write_text(home.read_text() + '<a href="/missing-page/">Missing</a>')
        with self.assertRaisesRegex(RuntimeError, "broken internal"):
            checks.check_site(self.site)

    def test_missing_theme_toggle_is_rejected(self):
        home = self.site / "index.html"
        home.write_text(home.read_text().replace('id="mode-toggle"', 'id="removed-toggle"'))
        with self.assertRaisesRegex(RuntimeError, "theme selector"):
            checks.check_site(self.site)

    def test_search_cannot_link_to_an_unpublished_page(self):
        (self.site / "assets/js/data/search.json").write_text(
            f'[{{"url": "{checks.POST}"}}, {{"url": "/missing-draft/"}}]'
        )
        with self.assertRaisesRegex(RuntimeError, "Search index"):
            checks.check_site(self.site)

    def test_invalid_shell_syntax_is_rejected_without_running_it(self):
        post = self.post_path()
        post.write_text(post.read_text() + "<pre><code>if true; then\n</code></pre>")
        with self.assertRaisesRegex(RuntimeError, "shell syntax"):
            checks.check_site(self.site)

    def test_unselected_content_license_is_rejected(self):
        post = self.post_path()
        post.write_text(post.read_text() + "<p>Licensed under CC BY 4.0</p>")
        with self.assertRaisesRegex(RuntimeError, "content license"):
            checks.check_site(self.site)

    def test_parser_preserves_commands_inside_highlighting_spans(self):
        document = checks.Document('<pre><code><span>printf</span> '
                                   '<span>&quot;%s\\n&quot;</span> hello</code></pre>')
        self.assertEqual(document.code_blocks, ['printf "%s\\n" hello'])

    def test_parser_excludes_chirpy_line_number_gutter(self):
        document = checks.Document(
            '<table><tr><td class="rouge-gutter gl"><pre>1\n2\n</pre></td>'
            '<td class="rouge-code"><pre>printf hello\nprintf world\n</pre></td></tr></table>'
        )
        self.assertEqual(document.code_blocks, ["printf hello\nprintf world\n"])


if __name__ == "__main__":
    unittest.main()
