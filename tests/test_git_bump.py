"""Tests for git-bump. Run with: python3 -m unittest discover -s tests"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the parent dir importable when running `python3 tests/test_git_bump.py` directly
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import git_bump


class TestParseSemver(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(git_bump.parse_semver("1.2.3"), (1, 2, 3, None))

    def test_with_prerelease(self):
        self.assertEqual(git_bump.parse_semver("1.0.0-rc1"), (1, 0, 0, "rc1"))

    def test_with_dotted_prerelease(self):
        self.assertEqual(git_bump.parse_semver("2.3.4-alpha.1"), (2, 3, 4, "alpha.1"))

    def test_zero(self):
        self.assertEqual(git_bump.parse_semver("0.0.0"), (0, 0, 0, None))

    def test_rejects_two_part(self):
        with self.assertRaises(ValueError):
            git_bump.parse_semver("1.2")

    def test_rejects_four_part(self):
        with self.assertRaises(ValueError):
            git_bump.parse_semver("1.2.3.4")

    def test_rejects_leading_zero(self):
        with self.assertRaises(ValueError):
            git_bump.parse_semver("01.2.3")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            git_bump.parse_semver("")

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            git_bump.parse_semver("not-a-version")

    def test_rejects_empty_prerelease_identifier(self):
        # Per SemVer 2.0.0 §9 item 2: identifiers MUST NOT be empty. The
        # original regex `[0-9A-Za-z.-]+` accepted dot-separated empty
        # segments like '..' or '.a' / 'a.'.
        for bad in ("1.2.3-..", "1.2.3-.", "1.2.3-a..b", "1.2.3-.a", "1.2.3-a."):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                git_bump.parse_semver(bad)

    def test_rejects_numeric_prerelease_with_leading_zero(self):
        # §9 item 4: numeric identifiers MUST NOT include leading zeroes.
        for bad in ("1.2.3-01", "1.2.3-00", "1.2.3-0.3.07"):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                git_bump.parse_semver(bad)

    def test_rejects_build_metadata(self):
        # git-bump cannot carry build metadata through a bump (next_version
        # rebuilds from major/minor/patch and drops the suffix), so silently
        # stripping '+...' would let the user tag v1.2.3 while the version
        # file still claims 1.2.3+build. Reject with a clear message.
        with self.assertRaises(ValueError) as cm:
            git_bump.parse_semver("1.2.3+build")
        self.assertIn("build metadata", str(cm.exception))
        self.assertIn("+build", str(cm.exception))
        with self.assertRaises(ValueError):
            git_bump.parse_semver("1.2.3+")
        with self.assertRaises(ValueError):
            git_bump.parse_semver("1.2.3-a+b")

    def test_accepts_valid_edge_case_prereleases(self):
        # Alphanumeric identifiers may contain hyphens anywhere except the
        # leading position is unrestricted (semver only forbids leading-zero
        # numerics). Make sure the tighter regex still accepts these.
        self.assertEqual(git_bump.parse_semver("1.2.3-a-"), (1, 2, 3, "a-"))
        self.assertEqual(git_bump.parse_semver("1.2.3-x-y-z.-"), (1, 2, 3, "x-y-z.-"))
        self.assertEqual(git_bump.parse_semver("1.2.3-0"), (1, 2, 3, "0"))
        self.assertEqual(git_bump.parse_semver("1.2.3-0.3.7"), (1, 2, 3, "0.3.7"))


class TestNextVersion(unittest.TestCase):
    def test_patch(self):
        self.assertEqual(git_bump.next_version("1.2.3", "patch"), "1.2.4")

    def test_minor(self):
        self.assertEqual(git_bump.next_version("1.2.3", "minor"), "1.3.0")

    def test_major(self):
        self.assertEqual(git_bump.next_version("1.2.3", "major"), "2.0.0")

    def test_patch_drops_prerelease(self):
        # A patch bump off a pre-release should produce a clean stable version
        self.assertEqual(git_bump.next_version("1.2.3-rc1", "patch"), "1.2.4")

    def test_minor_drops_prerelease(self):
        self.assertEqual(git_bump.next_version("1.2.3-rc1", "minor"), "1.3.0")

    def test_major_drops_prerelease(self):
        self.assertEqual(git_bump.next_version("1.2.3-alpha.beta", "major"), "2.0.0")

    def test_unknown_level(self):
        with self.assertRaises(ValueError):
            git_bump.next_version("1.2.3", "weekly")


class TestReplaceVersion(unittest.TestCase):
    def test_package_json(self):
        text = '{"name": "x", "version": "1.2.3", "description": "y"}'
        regex = re_compile_package_json()
        out = git_bump.replace_version(text, regex, "1.2.4")
        self.assertEqual(out, '{"name": "x", "version": "1.2.4", "description": "y"}')

    def test_preserves_formatting(self):
        # The whole point: don't re-serialize, only patch the version field
        text = '{\n  "name": "x",\n  "version": "1.2.3",\n  "scripts": {\n    "test": "echo"\n  }\n}\n'
        regex = re_compile_package_json()
        out = git_bump.replace_version(text, regex, "9.9.9")
        # Indentation, trailing newline, and key order all preserved
        self.assertEqual(out, text.replace("1.2.3", "9.9.9"))

    def test_pyproject_toml(self):
        text = '[project]\nname = "x"\nversion = "0.1.0"\n'
        regex = re_compile_pyproject()
        out = git_bump.replace_version(text, regex, "0.2.0")
        self.assertEqual(out, '[project]\nname = "x"\nversion = "0.2.0"\n')

    def test_init_py(self):
        text = '__version__ = "0.1.0"\n'
        regex = re_compile_init()
        out = git_bump.replace_version(text, regex, "0.1.1")
        self.assertEqual(out, '__version__ = "0.1.1"\n')

    def test_version_file(self):
        text = "1.2.3\n"
        regex = re_compile_version()
        out = git_bump.replace_version(text, regex, "1.2.4")
        self.assertEqual(out, "1.2.4\n")

    def test_no_match_raises(self):
        regex = re_compile_package_json()
        with self.assertRaises(ValueError):
            git_bump.replace_version("no version here", regex, "1.0.0")

    def test_multiple_matches_raises(self):
        # If a regex matched two version fields, refuse to guess
        regex = re_compile_package_json()
        text = '{"version": "1.0.0", "nested": {"version": "2.0.0"}}'
        with self.assertRaises(ValueError):
            git_bump.replace_version(text, regex, "3.0.0")


class TestDetectFile(unittest.TestCase):
    def test_detects_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "package.json").write_text('{"version": "1.0.0"}')
            path, regex = git_bump.detect_file(tmp_path)
            self.assertEqual(path.name, "package.json")
            self.assertIsNotNone(regex.search('{"version": "1.0.0"}'))

    def test_detects_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
            path, regex = git_bump.detect_file(tmp_path)
            self.assertEqual(path.name, "pyproject.toml")

    def test_detects_init_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "__init__.py").write_text('__version__ = "1.0.0"\n')
            path, regex = git_bump.detect_file(tmp_path)
            self.assertEqual(path.name, "__init__.py")

    def test_detects_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            path, regex = git_bump.detect_file(tmp_path)
            self.assertEqual(path.name, "VERSION")

    def test_priority_order(self):
        # If both package.json and pyproject.toml exist, package.json wins
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.1"\n')
            (tmp_path / "package.json").write_text('{"version": "0.0.2"}')
            path, regex = git_bump.detect_file(tmp_path)
            self.assertEqual(path.name, "package.json")

    def test_raises_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                git_bump.detect_file(tmp_path)


class TestBump(unittest.TestCase):
    """Integration-style tests for bump(), each in its own temp git repo."""

    def _make_git_repo(self, version_file_content: str, version_file_name: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / version_file_name).write_text(version_file_content)
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"],
            cwd=tmp, check=True,
        )
        subprocess.run(["git", "add", "."], cwd=tmp, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=tmp, check=True,
        )
        return tmp

    def test_bump_patch_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3\n")
            new = git_bump.bump("patch", cwd=tmp_path, commit=False)
            self.assertEqual(new, "1.2.4")
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.2.4\n")

    def test_bump_creates_commit_and_tag(self):
        tmp = self._make_git_repo('{"version": "0.1.0"}\n', "package.json")
        try:
            new = git_bump.bump("minor", cwd=tmp)
            self.assertEqual(new, "0.2.0")
            # Check the commit exists
            log = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=tmp, capture_output=True, text=True, check=True,
            )
            self.assertIn("v0.2.0", log.stdout)
            # Check the tag exists
            tags = subprocess.run(
                ["git", "tag", "--list"],
                cwd=tmp, capture_output=True, text=True, check=True,
            )
            self.assertIn("v0.2.0", tags.stdout)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bump_set_specific_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            new = git_bump.bump("patch", set_version="2.0.0-rc1", cwd=tmp_path, commit=False)
            self.assertEqual(new, "2.0.0-rc1")
            self.assertEqual((tmp_path / "VERSION").read_text(), "2.0.0-rc1\n")

    def test_bump_set_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            with self.assertRaises(ValueError):
                git_bump.bump("patch", set_version="not-semver", cwd=tmp_path, commit=False)

    def test_bump_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            git_bump.bump("patch", cwd=tmp_path, commit=False, dry_run=True)
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.0.0\n")

    def test_bump_rejects_existing_tag(self):
        tmp = self._make_git_repo('{"version": "0.1.0"}\n', "package.json")
        try:
            # Pre-create the tag that patch would generate
            subprocess.run(
                ["git", "tag", "v0.1.1"],
                cwd=tmp, check=True,
            )
            with self.assertRaises(ValueError):
                git_bump.bump("patch", cwd=tmp)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bump_rejects_already_at_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("0.2.0\n")
            with self.assertRaises(ValueError):
                # patch of 0.2.0 is 0.2.1, not 0.2.0 — so this should succeed
                # Use --set to force the no-op case
                git_bump.bump("patch", set_version="0.2.0", cwd=tmp_path, commit=False)

    def test_bump_no_commit_only_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            git_bump.bump("patch", cwd=tmp_path, commit=False)
            # No git repo at all, but no error because commit=False
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.0.1\n")

    def test_bump_outside_git_with_commit_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            with self.assertRaises(RuntimeError):
                git_bump.bump("patch", cwd=tmp_path, commit=True)

    def test_bump_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "custom_version.txt").write_text("5.0.0\n")
            # custom_version.txt is not in the supported list — should error
            with self.assertRaises(ValueError):
                git_bump.bump("patch", file=tmp_path / "custom_version.txt", cwd=tmp_path, commit=False)

    def test_bump_rejects_non_semver_current_version(self):
        # The match-and-replace regex is lenient to survive JSON/TOML quoting,
        # but the actual value in the file must be parseable SemVer.
        # 'latest' would match the package.json regex but is not a version.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "package.json").write_text('{"version": "latest"}')
            with self.assertRaises(ValueError) as cm:
                git_bump.bump("patch", cwd=tmp_path, commit=False)
            self.assertIn("latest", str(cm.exception))
            # The file must not have been mutated on the error path.
            self.assertEqual((tmp_path / "package.json").read_text(), '{"version": "latest"}')

    def test_bump_rejects_single_part_current_version(self):
        # '0' matches the regex but is not three-part SemVer.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("0\n")
            with self.assertRaises(ValueError):
                git_bump.bump("patch", cwd=tmp_path, commit=False)
            self.assertEqual((tmp_path / "VERSION").read_text(), "0\n")

    def test_bump_set_rejects_invalid_prerelease_identifier(self):
        # '1.2.3-a..b' has an empty dot-separated prerelease identifier and
        # is rejected by parse_semver. The fix must propagate this rejection
        # through bump()'s --set validation, and the file must NOT be mutated
        # on the error path.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3\n")
            with self.assertRaises(ValueError) as cm:
                git_bump.bump("patch", set_version="1.2.3-a..b", cwd=tmp_path, commit=False)
            self.assertIn("1.2.3-a..b", str(cm.exception))
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.2.3\n")

    def test_bump_set_rejects_build_metadata(self):
        # '1.2.3+build' is valid SemVer 2.0.0 but git-bump cannot carry
        # build metadata through a bump, so --set with build metadata is
        # rejected. The file must NOT be mutated on the error path.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3\n")
            with self.assertRaises(ValueError) as cm:
                git_bump.bump("patch", set_version="1.2.3+build", cwd=tmp_path, commit=False)
            self.assertIn("build metadata", str(cm.exception))
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.2.3\n")


class TestCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        with mock.patch("sys.argv", ["git-bump", "--help"]):
            with self.assertRaises(SystemExit) as cm:
                git_bump.main()
            self.assertEqual(cm.exception.code, 0)

    def test_no_args_errors(self):
        with mock.patch("sys.argv", ["git-bump"]):
            with self.assertRaises(SystemExit) as cm:
                git_bump.main()
            self.assertEqual(cm.exception.code, 2)  # argparse usage error

    def test_dry_run_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.0.0\n")
            with mock.patch("sys.argv", ["git-bump", "patch", "--dry-run"]):
                with mock.patch("os.chdir", lambda p: None):
                    # Run with cwd argument via env rather than os.chdir to avoid touching CWD
                    pass
            # Direct call path
            new = git_bump.bump("patch", cwd=tmp_path, commit=False, dry_run=True)
            self.assertEqual(new, "1.0.1")
            self.assertEqual((tmp_path / "VERSION").read_text(), "1.0.0\n")  # unchanged

    def test_cwd_flag_points_at_other_directory(self):
        # The CLI's --cwd should let auto-detection look in a directory
        # other than os.getcwd(), and the file write should land there too.
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "VERSION").write_text("1.0.0\n")
            # The "current" dir deliberately has no version file at all
            with tempfile.TemporaryDirectory() as scratch_dir:
                scratch = Path(scratch_dir)
                with mock.patch("sys.argv", ["git-bump", "patch", "--cwd", str(project), "--no-commit"]):
                    rc = git_bump.main()
                self.assertEqual(rc, 0)
                self.assertEqual((project / "VERSION").read_text(), "1.0.1\n")
                # The scratch dir was never touched
                self.assertEqual(list(scratch.iterdir()), [])

    def test_cwd_nonexistent_path_errors(self):
        with mock.patch("sys.argv", ["git-bump", "patch", "--cwd", "/no/such/dir/here/12345"]):
            with self.assertRaises(SystemExit) as cm:
                git_bump.main()
            self.assertEqual(cm.exception.code, 2)  # argparse usage error

    def test_cwd_path_is_a_file_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "not-a-dir.txt"
            file_path.write_text("x")
            with mock.patch("sys.argv", ["git-bump", "patch", "--cwd", str(file_path)]):
                with self.assertRaises(SystemExit) as cm:
                    git_bump.main()
                self.assertEqual(cm.exception.code, 2)


# --- regex helpers: the same patterns used in git_bump._DETECT, exposed
# --- here so test code can refer to them by name for clarity.
def re_compile_package_json():
    from git_bump import _DETECT
    return next(r for n, r in _DETECT if n == "package.json")


def re_compile_pyproject():
    from git_bump import _DETECT
    return next(r for n, r in _DETECT if n == "pyproject.toml")


def re_compile_init():
    from git_bump import _DETECT
    return next(r for n, r in _DETECT if n == "__init__.py")


def re_compile_version():
    from git_bump import _DETECT
    return next(r for n, r in _DETECT if n == "VERSION")


if __name__ == "__main__":
    unittest.main()
