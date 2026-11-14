"""``caplog``'s level rules, which are the half of it that is easy to get wrong.

pytest installs its capture handler on the **root logger** and changes **no level** unless
the ``log_level`` ini is set (`_pytest/logging.py::LoggingPlugin._runtest_for` passes
``level=self.log_level``, and ``get_log_level_for_setting`` answers ``None`` by default). So
with nothing configured the root logger keeps its stdlib default of ``WARNING`` and an
``INFO`` record is never created — which is why ``caplog.set_level`` exists at all.

Every assertion below is about that: what is captured with no configuration, what
``set_level`` and ``at_level`` change, how far the change reaches, and when it is undone.
The last test is the one that catches a "capture everything" implementation, because a
handler on the root logger cannot see a logger that does not propagate — under pytest
either.
"""

import logging


def test_the_default_level_is_the_root_logger_s(caplog):
    logging.getLogger("app").debug("debug")
    logging.getLogger("app").info("info")
    logging.getLogger("app").warning("warning")
    assert caplog.messages == ["warning"]


def test_set_level_lowers_it_for_the_rest_of_the_test(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("app").info("now visible")
    assert caplog.messages == ["now visible"]


def test_set_level_is_undone_after_the_test(caplog):
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("app").level == logging.NOTSET


def test_at_level_scopes_the_change_to_the_block(caplog):
    with caplog.at_level(logging.DEBUG, logger="app"):
        logging.getLogger("app").debug("inside")
    logging.getLogger("app").debug("outside")
    assert caplog.messages == ["inside"]


def test_set_level_on_one_logger_leaves_its_siblings_alone(caplog):
    caplog.set_level(logging.DEBUG, logger="one")
    logging.getLogger("one").debug("one")
    logging.getLogger("two").debug("two")
    assert caplog.messages == ["one"]


def test_records_carry_the_record_object(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("app.sub").info("hello %s", "world")
    (record,) = caplog.records
    assert record.name == "app.sub"
    assert record.levelname == "INFO"
    assert record.getMessage() == "hello world"
    assert caplog.record_tuples == [("app.sub", logging.INFO, "hello world")]


def test_text_is_the_formatted_output(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("app").info("formatted")
    assert "INFO     app:test_caplog.py:" in caplog.text
    assert caplog.text.endswith("formatted\n")


def test_clear_empties_records_and_text(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("app").info("first")
    caplog.clear()
    assert caplog.records == []
    assert caplog.text == ""


def test_a_non_propagating_logger_is_not_captured(caplog):
    caplog.set_level(logging.INFO)
    quiet = logging.getLogger("quiet")
    quiet.propagate = False
    try:
        quiet.info("unseen")
    finally:
        quiet.propagate = True
    assert caplog.messages == []
