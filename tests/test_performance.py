"""Performance tests and benchmarks for optimized modules."""

from __future__ import annotations

import re
import time
from fnmatch import fnmatch as legacy_fnmatch
from fnmatch import translate as fnmatch_translate
from pathlib import Path

from cocoindex_code.github_mirror import GitHubMirror


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    """Pre-compile fnmatch patterns to regex for faster matching."""
    return [re.compile(fnmatch_translate(p)) for p in patterns]


class TestPatternMatchingPerformance:
    """Benchmark pattern matching optimization."""

    def test_legacy_fnmatch_performance(self):
        """Baseline: legacy fnmatch() for each file."""
        patterns = ["*.py", "src/**/*.md", "test_*.py", "**/__pycache__/**"]
        files = [f"file_{i}.py" for i in range(1000)] + [f"src/module_{i}.md" for i in range(1000)]

        start = time.perf_counter()
        for file_path in files:
            for pattern in patterns:
                legacy_fnmatch(file_path, pattern)
        legacy_time = time.perf_counter() - start

        print(f"\nLegacy fnmatch (2000 files × 4 patterns): {legacy_time:.4f}s")
        assert legacy_time > 0  # Establish baseline

    def test_compiled_regex_performance(self):
        """Optimized: pre-compiled regex patterns."""
        patterns = ["*.py", "src/**/*.md", "test_*.py", "**/__pycache__/**"]
        compiled = compile_patterns(patterns)
        files = [f"file_{i}.py" for i in range(1000)] + [f"src/module_{i}.md" for i in range(1000)]

        start = time.perf_counter()
        for file_path in files:
            for pattern_re in compiled:
                pattern_re.fullmatch(file_path)
        compiled_time = time.perf_counter() - start

        print(f"Compiled regex (2000 files × 4 patterns): {compiled_time:.4f}s")
        assert compiled_time > 0

    def test_github_mirror_pattern_caching(self, tmp_path: Path):
        """Verify GitHubMirror uses cached patterns."""
        mirror = GitHubMirror(
            owner_repo="owner/repo",
            branch="main",
            include_patterns=["*.py", "*.md"],
            exclude_patterns=["test_*.py", "**/__pycache__/**"],
            cache_root=tmp_path,
        )

        # Verify patterns are compiled in __init__
        assert len(mirror._exclude_patterns_compiled) == 2
        assert len(mirror._include_patterns_compiled) == 2

        # Verify they're regex objects
        assert all(isinstance(p, re.Pattern) for p in mirror._exclude_patterns_compiled)
        assert all(isinstance(p, re.Pattern) for p in mirror._include_patterns_compiled)

    def test_should_include_performance(self, tmp_path: Path):
        """Benchmark _should_include() with cached patterns."""
        mirror = GitHubMirror(
            owner_repo="owner/repo",
            branch="main",
            include_patterns=["src/**/*.py", "docs/**/*.md"],
            exclude_patterns=["**/__pycache__/**", "*.pyc"],
            cache_root=tmp_path,
        )

        test_paths = [
            "src/module.py",
            "src/subdir/file.py",
            "__pycache__/cache.pyc",
            "docs/readme.md",
            "docs/guide.txt",
        ] * 100

        start = time.perf_counter()
        for path in test_paths:
            mirror._should_include(path)
        duration = time.perf_counter() - start

        print(f"\n_should_include() for {len(test_paths)} paths: {duration:.4f}s")
        assert duration < 1.0  # Should be fast with cached patterns


class TestDeduplicationPerformance:
    """Benchmark deduplication optimizations."""

    def test_dict_fromkeys_vs_manual(self):
        """Compare dict.fromkeys() vs manual deduplication."""
        data = [f"item_{i % 100}" for i in range(10000)]

        # Manual (old approach)
        start = time.perf_counter()
        seen = set()
        out = []
        for value in data:
            if value not in seen:
                seen.add(value)
                out.append(value)
        manual_time = time.perf_counter() - start

        # dict.fromkeys (new approach)
        start = time.perf_counter()
        optimized = list(dict.fromkeys(data))
        optimized_time = time.perf_counter() - start

        print(f"\nManual dedup (10k items): {manual_time:.4f}s")
        print(f"dict.fromkeys (10k items): {optimized_time:.4f}s")

        # Verify correctness
        manual_set = set(out)
        optimized_set = set(optimized)
        assert manual_set == optimized_set


class TestSymlinkOptimization:
    """Benchmark symlink creation optimization."""

    def test_symlink_skip_when_correct(self, tmp_path: Path):
        """Verify symlink is skipped if already correct."""
        # Create initial symlink
        link = tmp_path / "link"
        target = tmp_path / "target"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)

        assert link.is_symlink()
        assert link.resolve() == target

        # Create orchestrator to test _link_path
        from cocoindex_code.config import CodebaseConfig
        from cocoindex_code.multi_repo import MultiRepoOrchestrator

        config = CodebaseConfig(repos=[])
        orchestrator = MultiRepoOrchestrator(
            config_path=tmp_path / "config.yaml",
            config=config,
            unified_root=tmp_path / "unified",
            github_cache=tmp_path / "cache",
        )

        # Record inode before
        import os

        stat_before = os.stat(link, follow_symlinks=False)

        # Call _link_path again (should skip if already correct)
        orchestrator._link_path(link, target)

        # Record inode after (should be same, not recreated)
        stat_after = os.stat(link, follow_symlinks=False)
        assert stat_before.st_ino == stat_after.st_ino


class TestRateLimitParsing:
    """Test rate limit parsing optimization."""

    def test_safe_int_conversion(self, tmp_path: Path):
        """Verify rate limit parsing handles edge cases safely."""
        mirror = GitHubMirror(
            owner_repo="owner/repo",
            branch="main",
            include_patterns=[],
            exclude_patterns=[],
            cache_root=tmp_path,
        )

        # Test valid rate limits
        mirror._parse_rate_limit({"X-RateLimit-Remaining": "60", "X-RateLimit-Reset": "1234567890"})
        assert mirror.rate_limit_remaining == 60
        assert mirror.rate_limit_reset == 1234567890

        # Test invalid values
        mirror._parse_rate_limit(
            {"X-RateLimit-Remaining": "invalid", "X-RateLimit-Reset": "broken"}
        )
        assert mirror.rate_limit_remaining is None
        assert mirror.rate_limit_reset is None

        # Test missing values
        mirror._parse_rate_limit({})
        assert mirror.rate_limit_remaining is None
        assert mirror.rate_limit_reset is None
