"""UPG-RELATIVE-PATH-RENDER: workspace-relative path rendering + relative/absolute
chunk-id resolution."""
from __future__ import annotations

from agent.render_paths import workspace_relpath, resolve_chunk_id


class TestWorkspaceRelpath:
    def test_absolute_under_root_becomes_relative(self) -> None:
        assert workspace_relpath("/ws/root/django/db/base.py", "/ws/root") == "django/db/base.py"

    def test_root_itself_renders_as_dot(self) -> None:
        assert workspace_relpath("/ws/root", "/ws/root") == "."

    def test_path_outside_root_kept_absolute(self) -> None:
        # rendering ../../elsewhere would be noisier and less honest than the abs path
        assert workspace_relpath("/other/place/x.py", "/ws/root") == "/other/place/x.py"

    def test_empty_root_returns_path_unchanged(self) -> None:
        assert workspace_relpath("/ws/root/a.py", "") == "/ws/root/a.py"

    def test_empty_path_returns_unchanged(self) -> None:
        assert workspace_relpath("", "/ws/root") == ""


class TestResolveChunkId:
    def test_relative_id_joined_onto_root(self) -> None:
        assert resolve_chunk_id("django/db/base.py:10-20", "/ws/root") == "/ws/root/django/db/base.py:10-20"

    def test_absolute_id_passes_through(self) -> None:
        assert resolve_chunk_id("/ws/root/django/db/base.py:10-20", "/ws/root") == "/ws/root/django/db/base.py:10-20"

    def test_empty_root_returns_unchanged(self) -> None:
        assert resolve_chunk_id("django/db/base.py:10-20", "") == "django/db/base.py:10-20"

    def test_relative_id_without_line_range(self) -> None:
        # a bare path (no :start-end) still resolves against the root
        assert resolve_chunk_id("django/db/base.py", "/ws/root") == "/ws/root/django/db/base.py"

    def test_line_range_split_on_last_colon(self) -> None:
        # the line-range suffix is split off the LAST colon so the path is intact
        assert resolve_chunk_id("a/b.py:1-2", "/ws/root") == "/ws/root/a/b.py:1-2"

    def test_round_trip_relpath_then_resolve(self) -> None:
        """The relative id a search renders resolves back to the absolute id the
        index stores — the core round-trip the fetch back-compat relies on."""
        root = "/ws/root"
        abs_path = "/ws/root/pkg/mod.py"
        rel = workspace_relpath(abs_path, root)
        rel_id = f"{rel}:5-9"
        assert resolve_chunk_id(rel_id, root) == f"{abs_path}:5-9"
