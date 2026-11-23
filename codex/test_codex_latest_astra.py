#!/usr/bin/env python3
"""Tests for bin/codex-latest-astra: python3 codex/test_codex_latest_astra.py"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "codex-latest-astra"
NEEDS_TOML = unittest.skipIf(sys.version_info < (3, 11), "--sync-config needs Python 3.11 or later")


def model(slug, visibility="list"):
    return {"slug": slug, "visibility": visibility}


CATALOG = [model("gpt-6.2-sol"), model("gpt-6.2-astra"), model("gpt-6.1-terra"), model("gpt-6-astra"),
           model("gpt-6-astra-preview"), model("gpt-7-astra", "hide")]


class LatestAstra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "codex-home"
        self.home.mkdir()
        self.fake = self.tmp / "codex"  # stands in for the codex binary: prints the catalog in live.json
        self.fake.write_text("#!/bin/sh\n"
                             "[ \"$1\" = \"--version\" ] && { echo \"codex-cli ${FAKE_CODEX_VERSION:-1.0.0}\"; exit 0; }\n"
                             f"if [ \"$1 $2\" = \"login status\" ]; then [ ! -f '{self.tmp}/signed-out' ]; exit $?; fi\n"
                             "[ \"$1 $2\" = \"debug models\" ] || exit 9\n"
                             f"[ -f '{self.tmp}/live.json' ] || exit 1\ncat '{self.tmp}/live.json'\n")
        self.fake.chmod(0o755)

    def cache(self, models, age=0, version="1.0.0"):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - age)) + ".000000Z"
        (self.home / "models_cache.json").write_text(json.dumps({"fetched_at": stamp, "client_version": version,
                                                                 "models": models}))

    def live(self, models):
        (self.tmp / "live.json").write_text(json.dumps({"models": models}))

    def run_it(self, *args):
        env = dict(os.environ, CODEX_HOME=str(self.home), CODEX_BIN=str(self.fake))
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False)

    def test_newest_listed_astra_from_a_fresh_cache(self):
        self.cache(CATALOG)
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (0, "gpt-6.2-astra\n"), r.stderr)  # not hidden 7, not Sol

    def test_versions_compare_as_numbers(self):
        self.cache([model("gpt-6.9-astra"), model("gpt-6.10-astra"), model("gpt-6-astra")])
        self.assertEqual(self.run_it().stdout, "gpt-6.10-astra\n")
        self.cache([model("gpt-6.10-astra"), model("gpt-7-astra")])
        self.assertEqual(self.run_it().stdout, "gpt-7-astra\n")

    def test_a_stale_cache_asks_codex_and_falls_back_to_the_cache_with_a_note(self):
        self.cache([model("gpt-6-astra")], age=3 * 86400)
        self.live(CATALOG)
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "gpt-6.2-astra\n", ""))
        (self.tmp / "live.json").unlink()  # codex cannot answer
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (0, "gpt-6-astra\n"))
        self.assertIn("cached list 72 hours old", r.stderr)

    def test_a_live_list_without_astra_wins_over_an_old_cache(self):
        self.cache(CATALOG, age=2 * 86400)
        self.live([model("gpt-6.2-sol")])
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("lists no astra model", r.stderr)

    def test_a_signed_out_login_is_not_asked(self):
        self.live(CATALOG)  # what a signed-out codex prints: the list built into the binary
        (self.tmp / "signed-out").write_text("")
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.cache([model("gpt-6-astra")], age=3 * 86400)
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (0, "gpt-6-astra\n"))
        self.assertIn("could not be asked", r.stderr)

    def test_a_list_fetched_by_another_codex_version_is_not_used(self):
        self.cache([model("gpt-6-astra")], version="0.9.0")  # fresh, but what an older Codex was offered
        self.live(CATALOG)
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "gpt-6.2-astra\n", ""))
        (self.tmp / "live.json").unlink()  # and when codex cannot answer, that list is no fallback either
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (1, ""))

    def test_no_cache_asks_codex(self):
        self.live(CATALOG)
        self.assertEqual(self.run_it().stdout, "gpt-6.2-astra\n")

    def test_no_astra_is_an_error_and_never_another_family(self):
        self.cache([model("gpt-6.2-sol"), model("gpt-6.1-terra")])
        self.live([model("gpt-6.2-sol")])
        r = self.run_it()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("lists no astra model", r.stderr)
        (self.home / "models_cache.json").unlink()
        (self.tmp / "live.json").unlink()
        self.assertEqual(self.run_it().returncode, 1)

    def test_another_family_by_name(self):
        self.cache(CATALOG)
        self.assertEqual(self.run_it("--family", "sol").stdout, "gpt-6.2-sol\n")
        self.assertEqual(self.run_it("--bogus").returncode, 2)

    @NEEDS_TOML
    def test_sync_config_changes_only_the_top_level_model_line(self):
        self.cache(CATALOG)
        cfg = self.home / "config.toml"
        cfg.write_text('model = "gpt-6-astra"  # the gate\nmodel_reasoning_effort = "xhigh"\n\n'
                       '[profiles.fast]\nmodel = "gpt-6.1-terra"\n')
        cfg.chmod(0o600)
        r = self.run_it("--sync-config")
        self.assertEqual((r.returncode, r.stdout), (0, "gpt-6.2-astra\n"))
        self.assertIn("model gpt-6-astra -> gpt-6.2-astra", r.stderr)
        self.assertEqual(cfg.read_text(), 'model = "gpt-6.2-astra"  # the gate\nmodel_reasoning_effort = "xhigh"\n\n'
                                          '[profiles.fast]\nmodel = "gpt-6.1-terra"\n')
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
        before = cfg.stat().st_mtime_ns
        r = self.run_it("--sync-config")  # already current: the file is not rewritten
        self.assertEqual((r.stderr, cfg.stat().st_mtime_ns), ("", before))
        cfg.write_text('# mine\n[profiles.fast]\nmodel = "gpt-6.1-terra"\n')  # no top-level model: one is added
        r = self.run_it("--sync-config")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("model (unset) -> gpt-6.2-astra", r.stderr)
        self.assertEqual(cfg.read_text(), 'model = "gpt-6.2-astra"\n# mine\n[profiles.fast]\nmodel = "gpt-6.1-terra"\n')

    @NEEDS_TOML
    def test_sync_config_reads_toml_not_just_lines(self):
        self.cache(CATALOG)
        cfg = self.home / "config.toml"
        decoy = 'developer_instructions = """\nSet it like this:\nmodel = "example"\n"""\n'
        cfg.write_text(decoy + "model = 'gpt-6-astra'\n")  # a look-alike line inside a string, and single quotes
        r = self.run_it("--sync-config")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(cfg.read_text(), decoy + 'model = "gpt-6.2-astra"\n')
        cfg.write_text('model = "gpt-6-astra\n')  # not TOML: nothing is written, and the caller is told
        r = self.run_it("--sync-config")
        self.assertEqual((r.returncode, r.stdout), (3, "gpt-6.2-astra\n"))
        self.assertIn("is not valid TOML", r.stderr)
        self.assertEqual(cfg.read_text(), 'model = "gpt-6-astra\n')
        cfg.write_text('"model" = "gpt-6.2-astra"\n')  # an odd form that is already current is fine as it is
        self.assertEqual(self.run_it("--sync-config").returncode, 0)
        cfg.write_text('"model" = "gpt-6-astra"\n')  # a form the line reader does not take
        r = self.run_it("--sync-config")
        self.assertEqual(r.returncode, 3)
        self.assertIn("does not rewrite", r.stderr)
        self.assertEqual(cfg.read_text(), '"model" = "gpt-6-astra"\n')
        # a quote in a comment is not a string, and the key under it is still found
        cfg.write_text('# three quotes: """\nmodel = "gpt-6-astra"\n')
        self.assertEqual(self.run_it("--sync-config").returncode, 0)
        self.assertEqual(cfg.read_text(), '# three quotes: """\nmodel = "gpt-6.2-astra"\n')
        cfg.unlink()
        r = self.run_it("--sync-config")  # a login with no config yet gets one, private to the user
        self.assertEqual((r.returncode, r.stdout), (0, "gpt-6.2-astra\n"))
        self.assertEqual(cfg.read_text(), 'model = "gpt-6.2-astra"\n')
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)

    @NEEDS_TOML
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write a read-only folder")
    def test_sync_config_says_so_when_the_config_cannot_be_written(self):
        self.cache(CATALOG)
        cfg = self.home / "config.toml"
        cfg.write_text('model = "gpt-6-astra"\n')
        self.home.chmod(0o500)
        try:
            r = self.run_it("--sync-config")
        finally:
            self.home.chmod(0o700)
        self.assertEqual((r.returncode, r.stdout), (3, "gpt-6.2-astra\n"))
        self.assertIn("cannot write", r.stderr)
        self.assertEqual(cfg.read_text(), 'model = "gpt-6-astra"\n')

    @unittest.skipIf(sys.version_info >= (3, 11), "this interpreter can read TOML")
    def test_sync_config_is_refused_where_toml_cannot_be_read(self):
        self.cache(CATALOG)
        cfg = self.home / "config.toml"
        cfg.write_text('model = "gpt-6-astra"\n')
        r = self.run_it("--sync-config")
        self.assertEqual((r.returncode, r.stdout), (3, "gpt-6.2-astra\n"))
        self.assertIn("needs Python 3.11", r.stderr)
        self.assertEqual(cfg.read_text(), 'model = "gpt-6-astra"\n')

    @NEEDS_TOML
    def test_sync_config_follows_a_symlinked_config(self):
        self.cache(CATALOG)
        real = self.tmp / "dotfiles-config.toml"
        real.write_text('model = "gpt-6-astra"\n')
        (self.home / "config.toml").symlink_to(real)
        self.run_it("--sync-config")
        self.assertTrue((self.home / "config.toml").is_symlink())
        self.assertEqual(real.read_text(), 'model = "gpt-6.2-astra"\n')


if __name__ == "__main__":
    unittest.main()
