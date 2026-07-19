"""UPG-CACHE-LITTER: empty per-workspace cache-dir pruning + lazy creation."""
from __future__ import annotations

from pathlib import Path

from agent.cache_maintenance import (
    find_empty_cache_dirs,
    prune_empty_cache_dirs,
)


def _mk(root: Path, name: str, *, empty: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if not empty:
        (d / "working_context.sqlite").write_text("x")
    return d


class TestFindEmptyCacheDirs:
    def test_finds_empty_hash_dirs(self, tmp_path) -> None:
        _mk(tmp_path, "0041394e972f")
        _mk(tmp_path, "00c5ccee8aec")
        found = find_empty_cache_dirs(tmp_path)
        assert {p.name for p in found} == {"0041394e972f", "00c5ccee8aec"}

    def test_skips_non_empty_hash_dirs(self, tmp_path) -> None:
        _mk(tmp_path, "0041394e972f", empty=False)
        _mk(tmp_path, "00c5ccee8aec", empty=True)
        found = find_empty_cache_dirs(tmp_path)
        assert {p.name for p in found} == {"00c5ccee8aec"}

    def test_skips_reserved_models_and_db_names(self, tmp_path) -> None:
        # `models` and `db` are reserved subdir names, never 12-hex — even empty
        # they must never be considered for removal.
        (tmp_path / "models").mkdir()
        (tmp_path / "db").mkdir()
        found = find_empty_cache_dirs(tmp_path)
        assert found == []

    def test_skips_non_hash_named_dirs(self, tmp_path) -> None:
        (tmp_path / "not-a-hash").mkdir()
        (tmp_path / "0041394e972f").mkdir()  # 12 hex
        found = find_empty_cache_dirs(tmp_path)
        assert {p.name for p in found} == {"0041394e972f"}

    def test_honors_protected_slugs(self, tmp_path) -> None:
        _mk(tmp_path, "0041394e972f")
        _mk(tmp_path, "00c5ccee8aec")
        found = find_empty_cache_dirs(tmp_path, protected_slugs=frozenset({"0041394e972f"}))
        assert {p.name for p in found} == {"00c5ccee8aec"}

    def test_sweeps_legacy_db_layout(self, tmp_path) -> None:
        db = tmp_path / "db"
        _mk(db, "0041394e972f")
        _mk(db, "00c5ccee8aec", empty=False)
        found = find_empty_cache_dirs(tmp_path)
        assert {p.name for p in found} == {"0041394e972f"}

    def test_missing_root_is_empty(self, tmp_path) -> None:
        assert find_empty_cache_dirs(tmp_path / "nope") == []


class TestPruneEmptyCacheDirs:
    def test_removes_empty_dirs(self, tmp_path) -> None:
        a = _mk(tmp_path, "0041394e972f")
        b = _mk(tmp_path, "00c5ccee8aec")
        removed = prune_empty_cache_dirs(tmp_path)
        assert {p.name for p in removed} == {"0041394e972f", "00c5ccee8aec"}
        assert not a.exists()
        assert not b.exists()

    def test_dry_run_removes_nothing(self, tmp_path) -> None:
        a = _mk(tmp_path, "0041394e972f")
        removed = prune_empty_cache_dirs(tmp_path, dry_run=True)
        assert {p.name for p in removed} == {"0041394e972f"}
        assert a.exists()  # dry run — still there

    def test_never_removes_non_empty(self, tmp_path) -> None:
        live = _mk(tmp_path, "0041394e972f", empty=False)
        prune_empty_cache_dirs(tmp_path)
        assert live.exists()
        assert (live / "working_context.sqlite").exists()

    def test_protected_dir_survives(self, tmp_path) -> None:
        p = _mk(tmp_path, "0041394e972f")
        prune_empty_cache_dirs(tmp_path, protected_slugs=frozenset({"0041394e972f"}))
        assert p.exists()


class TestLazyCreationUPGCACHELITTER:
    def test_default_db_dir_does_not_create_subdir(self, tmp_path, monkeypatch) -> None:
        """UPG-CACHE-LITTER creation-side fix: resolving the DB path must not
        create the per-workspace subdir — only a real service does, when it also
        writes its notes DB."""
        from app import service as service_mod

        monkeypatch.setattr(service_mod.Path, "home", staticmethod(lambda: tmp_path))
        ws = "/some/workspace/path"
        db_dir = service_mod._default_db_dir(ws)
        # Parent cache root exists (secured), but the per-workspace subdir must not.
        assert (tmp_path / ".cache" / "vectr").is_dir()
        assert not Path(db_dir).exists()

    def test_default_db_dir_slug_is_stable(self, tmp_path, monkeypatch) -> None:
        from app import service as service_mod

        monkeypatch.setattr(service_mod.Path, "home", staticmethod(lambda: tmp_path))
        ws = "/some/workspace/path"
        d1 = service_mod._default_db_dir(ws)
        d2 = service_mod._default_db_dir(ws)
        assert d1 == d2
        assert Path(d1).name == service_mod._cache_dir_slug(ws)
