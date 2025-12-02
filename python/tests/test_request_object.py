"""Tests for the request fixture's node and config objects.

This module tests the pytest compatibility features of request.node and
request.config, which provide access to test metadata and configuration.
"""

from __future__ import annotations

import pytest

from rustest.compat.pytest import FixtureRequest, Node, Config


# =============================================================================
# Tests for Node object
# =============================================================================


class TestNode:
    """Tests for the Node object."""

    def test_node_initialization(self):
        """Test that Node can be initialized with basic attributes."""
        node = Node(name="test_example", nodeid="tests/test_foo.py::test_example")

        assert node.name == "test_example"
        assert node.nodeid == "tests/test_foo.py::test_example"
        assert node.parent is None
        assert node.session is None

    def test_node_with_markers(self):
        """Test Node with markers."""
        markers = [
            {"name": "slow", "args": (), "kwargs": {}},
            {"name": "skip", "args": (), "kwargs": {"reason": "not ready"}},
        ]

        node = Node(name="test", markers=markers)

        assert len(node._markers) == 2
        assert "slow" in node.keywords
        assert "skip" in node.keywords

    def test_node_get_closest_marker_found(self):
        """Test get_closest_marker when marker exists."""
        markers = [
            {"name": "slow", "args": (), "kwargs": {}},
            {"name": "skip", "args": (), "kwargs": {"reason": "not ready"}},
        ]

        node = Node(name="test", markers=markers)
        marker = node.get_closest_marker("skip")

        assert marker is not None
        assert marker.name == "skip"
        assert marker.kwargs == {"reason": "not ready"}

    def test_node_get_closest_marker_not_found(self):
        """Test get_closest_marker when marker doesn't exist."""
        node = Node(name="test")
        marker = node.get_closest_marker("nonexistent")

        assert marker is None

    def test_node_get_closest_marker_multiple_same_name(self):
        """Test get_closest_marker returns the most recent marker."""
        markers = [
            {"name": "skip", "args": (), "kwargs": {"reason": "first"}},
            {"name": "skip", "args": (), "kwargs": {"reason": "second"}},
        ]

        node = Node(name="test", markers=markers)
        marker = node.get_closest_marker("skip")

        # Should return the most recently added (last in list)
        assert marker.kwargs["reason"] == "second"

    def test_node_add_marker_string(self):
        """Test adding a marker by string name."""
        node = Node(name="test")

        node.add_marker("slow")

        assert "slow" in node.keywords
        marker = node.get_closest_marker("slow")
        assert marker is not None
        assert marker.name == "slow"

    def test_node_add_marker_dict(self):
        """Test adding a marker as a dictionary."""
        node = Node(name="test")

        marker_dict = {"name": "skip", "args": (), "kwargs": {"reason": "test"}}
        node.add_marker(marker_dict)

        marker = node.get_closest_marker("skip")
        assert marker is not None
        assert marker.kwargs["reason"] == "test"

    def test_node_add_marker_append(self):
        """Test that markers are appended by default."""
        node = Node(name="test")

        node.add_marker("first")
        node.add_marker("second")

        # Second marker should come after first
        assert len(node._markers) == 2
        assert node._markers[0]["name"] == "first"
        assert node._markers[1]["name"] == "second"

    def test_node_add_marker_prepend(self):
        """Test prepending markers with append=False."""
        node = Node(name="test")

        node.add_marker("first")
        node.add_marker("second", append=False)

        # Second marker should come before first
        assert len(node._markers) == 2
        assert node._markers[0]["name"] == "second"
        assert node._markers[1]["name"] == "first"

    def test_node_listextrakeywords(self):
        """Test listextrakeywords returns set of marker names."""
        markers = [
            {"name": "slow", "args": (), "kwargs": {}},
            {"name": "skip", "args": (), "kwargs": {}},
        ]

        node = Node(name="test", markers=markers)
        keywords = node.listextrakeywords()

        assert isinstance(keywords, set)
        assert "slow" in keywords
        assert "skip" in keywords


# =============================================================================
# Tests for Config object
# =============================================================================


class TestConfig:
    """Tests for the Config object."""

    def test_config_initialization(self):
        """Test Config initialization."""
        config = Config()

        assert config.rootpath.exists()
        assert config.inipath is None
        assert config.pluginmanager is not None

    def test_config_with_options(self):
        """Test Config with command-line options."""
        options = {"verbose": 2, "capture": "no"}
        config = Config(options=options)

        assert config.getoption("verbose") == 2
        assert config.getoption("capture") == "no"

    def test_config_getoption_default(self):
        """Test getoption returns default when option not found."""
        config = Config()

        value = config.getoption("nonexistent", default=42)

        assert value == 42

    def test_config_getoption_strips_dashes(self):
        """Test that getoption strips leading dashes from option names."""
        options = {"verbose": 1}
        config = Config(options=options)

        # Should work with or without leading dashes
        assert config.getoption("verbose") == 1
        assert config.getoption("-verbose") == 1
        assert config.getoption("--verbose") == 1

    def test_config_getoption_skip(self):
        """Test getoption with skip=True raises Skipped."""
        config = Config()

        from rustest.compat.pytest import Skipped

        with pytest.raises(Skipped):
            config.getoption("nonexistent", skip=True)

    def test_config_option_namespace(self):
        """Test accessing options via config.option namespace."""
        options = {"verbose": 2, "capture": "no"}
        config = Config(options=options)

        assert config.option.verbose == 2
        assert config.option.capture == "no"
        assert config.option.nonexistent is None

    def test_config_getini_returns_value(self):
        """Test getini returns configured values."""
        ini_values = {"testpaths": ["tests"], "python_files": ["test_*.py"]}
        config = Config(ini_values=ini_values)

        assert config.getini("testpaths") == ["tests"]
        assert config.getini("python_files") == ["test_*.py"]

    def test_config_getini_list_defaults(self):
        """Test getini returns empty list for list-type options."""
        config = Config()

        # Common list-type ini values should default to []
        assert config.getini("testpaths") == []
        assert config.getini("python_files") == []
        assert config.getini("markers") == []
        assert config.getini("filterwarnings") == []

    def test_config_getini_string_defaults(self):
        """Test getini returns empty string for string-type options."""
        config = Config()

        # Unknown ini values should default to ""
        assert config.getini("unknown_option") == ""
        assert config.getini("custom_setting") == ""

    def test_config_pluginmanager_stub(self):
        """Test that pluginmanager is a functional stub."""
        config = Config()

        # Pluginmanager should have basic methods that return safe values
        assert config.pluginmanager.get_plugin("pytest_timeout") is None
        assert config.pluginmanager.hasplugin("pytest_timeout") is False

        # register should not raise
        config.pluginmanager.register(object(), name="test")

    def test_config_addinivalue_line_noop(self):
        """Test addinivalue_line is a no-op."""
        config = Config()

        # Should not raise
        config.addinivalue_line("markers", "slow: marks tests as slow")


# =============================================================================
# Tests for FixtureRequest with node and config
# =============================================================================


class TestFixtureRequestNodeAndConfig:
    """Tests for FixtureRequest with node and config objects."""

    def test_request_has_node_and_config(self):
        """Test that request has node and config attributes."""
        request = FixtureRequest()

        assert hasattr(request, "node")
        assert hasattr(request, "config")
        assert isinstance(request.node, Node)
        assert isinstance(request.config, Config)

    def test_request_with_node_name(self):
        """Test creating request with node name."""
        request = FixtureRequest(node_name="test_example")

        assert request.node.name == "test_example"
        assert request.node.nodeid == "test_example"

    def test_request_with_nodeid_override(self):
        """Test creating request with explicit nodeid."""
        request = FixtureRequest(
            node_name="test_example",
            nodeid="tests/test_file.py::test_example[param]",
        )

        assert request.node.name == "test_example"
        assert request.node.nodeid == "tests/test_file.py::test_example[param]"

    def test_request_with_node_markers(self):
        """Test creating request with node markers."""
        markers = [{"name": "slow", "args": (), "kwargs": {}}]
        request = FixtureRequest(node_markers=markers)

        marker = request.node.get_closest_marker("slow")
        assert marker is not None

    def test_request_with_config_options(self):
        """Test creating request with config options."""
        options = {"verbose": 2}
        request = FixtureRequest(config_options=options)

        assert request.config.getoption("verbose") == 2

    def test_request_node_has_config_reference(self):
        """Test that node has reference to config."""
        request = FixtureRequest()

        assert request.node.config is request.config

    def test_request_backwards_compatibility(self):
        """Test that request maintains backwards compatibility."""
        # Old code that creates FixtureRequest with just param should still work
        request = FixtureRequest(param=42)

        assert request.param == 42
        assert request.scope == "function"
        assert request.fixturename is None
        # Node and config should be created with defaults
        assert request.node.name == ""
        assert request.config.rootpath.exists()


# =============================================================================
# Integration tests
# =============================================================================


class TestRequestIntegration:
    """Integration tests for request object features."""

    def test_request_node_marker_workflow(self):
        """Test a realistic workflow using request.node markers."""
        markers = [{"name": "skip", "args": (), "kwargs": {"reason": "not implemented"}}]
        request = FixtureRequest(
            node_name="test_feature",
            node_markers=markers,
        )

        # Check for skip marker
        skip_marker = request.node.get_closest_marker("skip")
        assert skip_marker is not None
        assert skip_marker.kwargs["reason"] == "not implemented"

        # Add another marker dynamically
        request.node.add_marker("slow")
        assert "slow" in request.node.keywords

    def test_request_config_option_workflow(self):
        """Test a realistic workflow using request.config options."""
        options = {"verbose": 2, "tb": "short"}
        request = FixtureRequest(config_options=options)

        # Access options different ways
        assert request.config.getoption("verbose") == 2
        assert request.config.getoption("--tb") == "short"
        assert request.config.option.verbose == 2

    def test_request_conditional_behavior_based_on_marker(self):
        """Test conditional fixture behavior based on markers."""
        markers = [{"name": "use_database", "args": (), "kwargs": {"engine": "postgres"}}]
        request = FixtureRequest(node_markers=markers)

        # Fixture could conditionally set up resources
        db_marker = request.node.get_closest_marker("use_database")
        if db_marker:
            engine = db_marker.kwargs.get("engine", "sqlite")
            assert engine == "postgres"

    def test_request_conditional_behavior_based_on_config(self):
        """Test conditional fixture behavior based on config."""
        options = {"enable_mocks": True}
        request = FixtureRequest(config_options=options)

        # Fixture could conditionally enable mocking
        if request.config.getoption("enable_mocks", default=False):
            # Would set up mocks here
            assert True
        else:
            pytest.fail("Should have enabled mocks")

    def test_multiple_markers_same_type(self):
        """Test handling multiple markers of the same type."""
        markers = [
            {"name": "parametrize", "args": ("x", [1, 2]), "kwargs": {}},
            {"name": "parametrize", "args": ("y", [3, 4]), "kwargs": {}},
        ]
        request = FixtureRequest(node_markers=markers)

        # Should be able to retrieve the most recent one
        marker = request.node.get_closest_marker("parametrize")
        assert marker.args == ("y", [3, 4])

    def test_node_keywords_dictionary(self):
        """Test that node.keywords works like pytest."""
        markers = [
            {"name": "slow", "args": (), "kwargs": {}},
            {"name": "integration", "args": (), "kwargs": {}},
        ]
        request = FixtureRequest(node_markers=markers)

        # Check keywords dict
        assert "slow" in request.node.keywords
        assert "integration" in request.node.keywords
        assert "nonexistent" not in request.node.keywords
