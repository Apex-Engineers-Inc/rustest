"""Per-test mutation verification for P1b.2 Task 4.

Each row applies one textual mutation to a Rust source file, rebuilds, and runs ONLY the
tests named for that row. A non-zero cargo exit is a KILL. A zero exit is a SURVIVOR. A
timeout (180s) is a SURVIVOR, never a kill. A row whose anchor does not appear exactly once
is reported as BAD ANCHOR rather than silently skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIMEOUT = 180


@dataclass
class Row:
    id: int
    area: str
    file: str
    old: str
    new: str
    tests: list[str]
    note: str = ""
    count: int = 1
    lang: str = "rust"
    extra: list[tuple[str, str, str]] = field(default_factory=list)


SEL = "src/v2/selection.rs"
CORE = "python/rustest/core.py"
CLI = "python/rustest/cli.py"
EXE = "src/v2/execute.rs"
COL = "src/v2/collect.rs"

ROWS: list[Row] = [
    # ---------------------------------------------------------------- grammar
    Row(
        1,
        "grammar",
        SEL,
        "Node::Const(false)\n        } else {",
        "Node::Const(true)\n        } else {",
        [
            "v2::selection::tests::the_empty_expression_is_false",
            "v2::selection::tests::empty_k_selects_everything_and_empty_m_selects_nothing",
        ],
        "empty expression selects everything",
    ),
    Row(
        2,
        "grammar",
        SEL,
        "node = Node::Or(Box::new(node), Box::new(rhs));",
        "node = Node::And(Box::new(node), Box::new(rhs));",
        ["v2::selection::tests::boolean_operators_follow_the_usual_semantics"],
        "or means and",
    ),
    Row(
        3,
        "grammar",
        SEL,
        "node = Node::And(Box::new(node), Box::new(rhs));",
        "node = Node::Or(Box::new(node), Box::new(rhs));",
        ["v2::selection::tests::boolean_operators_follow_the_usual_semantics"],
        "and means or",
    ),
    Row(
        4,
        "grammar",
        SEL,
        "    while scanner.accept(TokenType::Or).is_some() {\n        let rhs = parse_and_expr(scanner)?;\n        node = Node::Or(",
        "    while scanner.accept(TokenType::And).is_some() {\n        let rhs = parse_and_expr(scanner)?;\n        node = Node::And(",
        ["v2::selection::tests::and_binds_tighter_than_or"],
        "or binds tighter than and (the two levels swapped)",
        extra=[
            (
                SEL,
                "    while scanner.accept(TokenType::And).is_some() {\n        let rhs = parse_not_expr(scanner)?;\n        node = Node::And(",
                "    while scanner.accept(TokenType::Or).is_some() {\n        let rhs = parse_not_expr(scanner)?;\n        node = Node::Or(",
            )
        ],
    ),
    Row(
        5,
        "grammar",
        SEL,
        "return Ok(Node::Not(Box::new(parse_not_expr(scanner)?)));",
        "return parse_not_expr(scanner);",
        ["v2::selection::tests::boolean_operators_follow_the_usual_semantics"],
        "not is a no-op",
    ),
    Row(
        6,
        "grammar",
        SEL,
        "        return Ok(Node::Not(Box::new(parse_not_expr(scanner)?)));",
        "        return Ok(Node::Not(Box::new(parse_expr(scanner)?)));",
        ["v2::selection::tests::not_is_right_recursive_and_binds_tightest"],
        "not swallows the whole rest",
    ),
    Row(
        7,
        "grammar",
        SEL,
        "        let node = parse_expr(scanner)?;\n        scanner.expect(TokenType::RParen)?;\n        return Ok(node);",
        "        let node = parse_expr(scanner)?;\n        let _ = scanner.accept(TokenType::RParen);\n        return Ok(node);",
        ["v2::selection::tests::an_unclosed_paren_is_rejected_at_the_eof_token"],
        "unclosed paren accepted",
    ),
    Row(
        8,
        "grammar",
        SEL,
        "            let node = parse_expr(&mut scanner)?;\n            scanner.expect(TokenType::Eof)?;",
        "            let node = parse_expr(&mut scanner)?;",
        ["v2::selection::tests::trailing_junk_after_a_complete_expression_is_rejected"],
        "trailing junk accepted",
    ),
    # ------------------------------------------------------------------ lexer
    Row(
        9,
        "lexer",
        SEL,
        """                "or" => TokenType::Or,""",
        """                "or" => TokenType::Ident,""",
        ["v2::selection::tests::boolean_operators_follow_the_usual_semantics"],
        "`or` lexes as an ident",
    ),
    Row(
        10,
        "lexer",
        SEL,
        "            while end < chars.len() && is_ident_char(chars[end]) {\n                end += 1;\n            }",
        "            if end < chars.len() && is_ident_char(chars[end]) {\n                end += 1;\n            }",
        ["v2::selection::tests::a_bare_identifier_defers_to_the_matcher"],
        "idents are one character",
    ),
    Row(
        11,
        "lexer",
        SEL,
        "    is_word_char(ch) || matches!(ch, ':' | '+' | '-' | '.' | '[' | ']' | '\\\\' | '/')",
        "    is_word_char(ch)",
        ["v2::selection::tests::the_ident_class_admits_nodeid_punctuation"],
        "ident class is only \\w",
    ),
    Row(
        12,
        "lexer",
        SEL,
        "    ch.is_alphanumeric() || ch == '_'",
        "    ch.is_ascii_alphanumeric() || ch == '_'",
        ["v2::selection::tests::identifiers_may_be_non_ascii"],
        "ascii-only \\w",
    ),
    Row(
        14,
        "lexer",
        SEL,
        "            let Some(offset) = chars[pos + 1..].iter().position(|c| *c == ch) else {",
        "            let Some(offset) = chars[pos..].iter().position(|c| *c == ch) else {",
        ["v2::selection::tests::a_missing_closing_quote_names_the_quote_character"],
        "quote scan includes the opener",
    ),
    Row(
        15,
        "lexer",
        SEL,
        "            if let Some(backslash) = first_backslash {",
        "            if let Some(backslash) = None::<usize> {",
        ["v2::selection::tests::the_backslash_check_scans_the_whole_input_not_the_literal"],
        "backslash check removed",
    ),
    Row(
        16,
        "lexer",
        SEL,
        '                column: pos + 1,\n                message: format!("unexpected character \\"{ch}\\""),',
        '                column: pos,\n                message: format!("unexpected character \\"{ch}\\""),',
        ["v2::selection::tests::an_unexpected_character_reports_its_column"],
        "0-based column",
    ),
    Row(
        17,
        "lexer",
        SEL,
        "            column: self.current().pos + 1,",
        "            column: self.current().pos,",
        ["v2::selection::tests::a_leading_operator_reproduces_pytests_reject_message"],
        "0-based reject column",
    ),
    Row(
        18,
        "lexer",
        SEL,
        '                names.join(" OR "),',
        '                names.join(", "),',
        ["v2::selection::tests::a_leading_operator_reproduces_pytests_reject_message"],
        "reject wording",
    ),
    # ------------------------------------------------------------------ kwargs
    Row(
        19,
        "kwargs",
        SEL,
        "        let text: String = literal.value.chars().skip(1).collect();\n        let mut text = text;\n        let _ = text.pop();",
        "        let text: String = literal.value.chars().collect();\n        let mut text = text;",
        [
            "v2::selection::tests::string_literals_accept_either_quote_and_lose_both",
            "v2::selection::tests::mark_kwargs_must_all_match_on_one_mark",
        ],
        "quotes kept in the value",
    ),
    Row(
        20,
        "kwargs",
        SEL,
        "    if !is_python_identifier(&name.value) {",
        "    if false {",
        ["v2::selection::tests::a_kwarg_name_must_be_a_python_identifier"],
        "identifier check removed",
    ),
    Row(
        21,
        "kwargs",
        SEL,
        "    if PYTHON_KEYWORDS.contains(&name.value.as_str()) {",
        "    if false {",
        ["v2::selection::tests::a_reserved_keyword_cannot_be_a_kwarg_name"],
        "keyword check removed",
    ),
    Row(
        22,
        "kwargs",
        SEL,
        "    let digits = number.strip_prefix('-').unwrap_or(number);",
        "    let digits = number.as_str();",
        ["v2::selection::tests::kwarg_values_cover_every_documented_literal"],
        "negative ints rejected",
    ),
    Row(
        23,
        "kwargs",
        SEL,
        "            KwValue::None => Value::Null,",
        '            KwValue::None => Value::String("None".to_string()),',
        ["v2::selection::tests::an_absent_kwarg_never_matches_not_even_none"],
        'None is the string "None"',
    ),
    Row(
        24,
        "kwargs",
        SEL,
        '        "True" => Ok((name.value, KwValue::Bool(true))),',
        '        "True" => Ok((name.value, KwValue::Bool(false))),',
        ["v2::selection::tests::kwarg_values_cover_every_documented_literal"],
        "True is False",
    ),
    # -------------------------------------------------------------- evaluation
    Row(
        25,
        "evaluate",
        SEL,
        "        Node::And(lhs, rhs) => {\n            if evaluate(lhs, matcher)? {\n                evaluate(rhs, matcher)\n            } else {\n                Ok(false)\n            }\n        }",
        "        Node::And(lhs, rhs) => {\n            let right = evaluate(rhs, matcher)?;\n            Ok(evaluate(lhs, matcher)? && right)\n        }",
        ["v2::selection::tests::boolean_short_circuit_decides_whether_a_refusal_is_reached"],
        "and does not short-circuit",
    ),
    Row(
        26,
        "evaluate",
        SEL,
        "        Node::Or(lhs, rhs) => {\n            if evaluate(lhs, matcher)? {\n                Ok(true)\n            } else {\n                evaluate(rhs, matcher)\n            }\n        }",
        "        Node::Or(lhs, rhs) => {\n            let right = evaluate(rhs, matcher)?;\n            Ok(evaluate(lhs, matcher)? || right)\n        }",
        ["v2::selection::tests::boolean_short_circuit_decides_whether_a_refusal_is_reached"],
        "or does not short-circuit",
        count=1,
        extra=[
            (
                SEL,
                '        let just_two = vec![test_case("test_x.py", "test_two")];\n        assert!(deselect(just_two, Some("one and x(y=1)"), None).is_ok());',
                '        let just_two = vec![test_case("test_x.py", "test_two")];\n        assert!(deselect(just_two.clone(), Some("one and x(y=1)"), None).is_ok());\n        assert!(deselect(just_two, Some("two or x(y=1)"), None).is_ok());',
            )
        ],
    ),
    # ---------------------------------------------------------- KeywordMatcher
    Row(
        27,
        "-k",
        SEL,
        "        Ok(self.names.iter().any(|name| name.contains(&needle)))",
        "        Ok(self.names.iter().any(|name| *name == needle))",
        ["v2::selection::tests::keyword_matching_reproduces_every_probed_node_name"],
        "-k is exact, not substring",
    ),
    Row(
        28,
        "-k",
        SEL,
        "        let needle = name.to_lowercase();",
        "        let needle = name.to_string();",
        ["v2::selection::tests::keyword_matching_lowercases_the_names_as_well_as_the_needle"],
        "needle not lowercased",
    ),
    Row(
        29,
        "-k",
        SEL,
        "            names.push(segment.to_lowercase());",
        "            names.push((*segment).to_string());",
        ["v2::selection::tests::keyword_matching_lowercases_the_names_as_well_as_the_needle"],
        "path names not lowercased",
    ),
    Row(
        30,
        "-k",
        SEL,
        "        for segment in &segments {\n            names.push(segment.to_lowercase());\n        }",
        "        for segment in segments.iter().skip(segments.len().saturating_sub(1)) {\n            names.push(segment.to_lowercase());\n        }",
        ["v2::selection::tests::keyword_matching_reproduces_every_probed_node_name"],
        "directories are not node names",
    ),
    Row(
        31,
        "-k",
        SEL,
        "        for mark in &test.marks {\n            names.push(mark.name.to_lowercase());\n        }",
        "",
        ["v2::selection::tests::keyword_matching_reproduces_every_probed_node_name"],
        "mark names not in the -k set",
    ),
    Row(
        32,
        "-k",
        SEL,
        '            let function = match &test.param_id {\n                Some(param) => format!("{last}[{param}]"),\n                None => (*last).to_string(),\n            };',
        "            let function = (*last).to_string();",
        ["v2::selection::tests::the_function_name_carries_the_param_suffix"],
        "function name loses its param suffix",
    ),
    Row(
        33,
        "-k",
        SEL,
        "            for class in classes {\n                names.push(class.to_lowercase());\n            }",
        "            for class in classes.iter().skip(classes.len().saturating_sub(1)) {\n                names.push(class.to_lowercase());\n            }",
        ["v2::selection::tests::every_enclosing_class_is_a_name"],
        "only the innermost class",
    ),
    Row(
        34,
        "-k",
        SEL,
        """            return Err(SelectionError::Usage(
                "Keyword expressions do not support call parameters.".to_string(),
            ));""",
        "            return Ok(false);",
        [
            "v2::selection::tests::keyword_expressions_refuse_call_parameters",
            "v2::selection::tests::keyword_call_parameters_only_fail_when_there_is_something_to_match",
        ],
        "-k call parameters silently ignored",
    ),
    # ------------------------------------------------------------- MarkMatcher
    Row(
        35,
        "-m",
        SEL,
        "        let Some(marks) = self.by_name.get(name) else {\n            return Ok(false);\n        };",
        "        let Some(marks) = self.by_name.iter().find(|(known, _)| known.contains(name)).map(|(_, v)| v) else {\n            return Ok(false);\n        };",
        ["v2::selection::tests::mark_matching_is_by_name_and_is_exact"],
        "-m matches substrings",
    ),
    Row(
        36,
        "-m",
        SEL,
        "            kwargs.iter().all(|(key, expected)| {",
        "            kwargs.iter().any(|(key, expected)| {",
        ["v2::selection::tests::mark_kwargs_must_all_match_on_one_mark"],
        "one matching kwarg is enough",
    ),
    Row(
        37,
        "-m",
        SEL,
        "                mark.kwargs\n                    .get(key)\n                    .is_some_and(|actual| *actual == expected.as_json())",
        "                mark.kwargs\n                    .get(key)\n                    .is_none_or(|actual| *actual == expected.as_json())",
        [
            "v2::selection::tests::an_absent_kwarg_never_matches_not_even_none",
            "v2::selection::tests::mark_kwargs_must_all_match_on_one_mark",
        ],
        "an absent kwarg matches anything",
    ),
    Row(
        38,
        "-m",
        SEL,
        "        Ok(marks.iter().any(|mark| {",
        "        Ok(marks.iter().all(|mark| {",
        ["v2::selection::tests::any_mark_of_the_name_may_satisfy_the_kwargs"],
        "every mark of the name must match",
    ),
    # ---------------------------------------------------------------- deselect
    Row(
        39,
        "deselect",
        SEL,
        "        let expression = raw.trim_start();",
        "        let expression = raw;",
        ["v2::selection::tests::empty_k_selects_everything_and_empty_m_selects_nothing"],
        "-k not lstripped",
    ),
    Row(
        40,
        "deselect",
        SEL,
        "    if let Some(expression) = mark {\n        if !expression.is_empty() {",
        "    if let Some(expression) = mark {\n        if true {",
        ["v2::selection::tests::empty_k_selects_everything_and_empty_m_selects_nothing"],
        '-m "" deselects everything',
    ),
    Row(
        41,
        "deselect",
        SEL,
        "                if *keep && !compiled.evaluate(&MarkMatcher::from_marks(&test.marks))? {\n                    *keep = false;\n                }",
        "                *keep = compiled.evaluate(&MarkMatcher::from_marks(&test.marks))?;",
        ["v2::selection::tests::keyword_and_mark_expressions_compose_and_the_count_is_cumulative"],
        "-m resurrects tests that -k already deselected",
    ),
    Row(
        42,
        "deselect",
        SEL,
        "    Expression::compile(expression).map_err(|error| SelectionError::Parse {\n        flag,",
        '    Expression::compile(expression).map_err(|error| SelectionError::Parse {\n        flag: "-k",',
        ["v2::selection::tests::a_bad_expression_names_the_flag_that_carried_it"],
        "flag hard-coded in the error",
    ),
    # ------------------------------------------------------------- exit codes
    Row(
        43,
        "exitcode",
        EXE,
        "    if collection_errors > 0 {\n        return 2;\n    }\n    if failures > 0 {\n        return 1;\n    }",
        "    if failures > 0 {\n        return 1;\n    }\n    if collection_errors > 0 {\n        return 2;\n    }",
        ["v2::execute::tests::a_collection_error_exits_two_and_outranks_failures"],
        "failures outrank collection errors",
    ),
    Row(
        44,
        "exitcode",
        EXE,
        "    if failures > 0 {\n        return 1;\n    }\n    if collected == 0 {\n        return 5;\n    }",
        "    if collected == 0 {\n        return 5;\n    }\n    if failures > 0 {\n        return 1;\n    }",
        ["v2::execute::tests::failures_outrank_zero_collected"],
        "zero-collected outranks failures",
    ),
    Row(
        45,
        "exitcode",
        EXE,
        "    if collection_errors > 0 {\n        return 2;\n    }",
        "    if collection_errors > 0 && collected > 0 {\n        return 2;\n    }",
        ["v2::execute::tests::a_collection_error_outranks_zero_collected"],
        "collection error ignored when nothing collected",
    ),
    Row(
        46,
        "exitcode",
        EXE,
        "    if collection_errors > 0 {\n        return 2;\n    }",
        "    if collection_errors > 0 {\n        return 1;\n    }",
        ["v2::execute::tests::a_collection_error_exits_two_and_outranks_failures"],
        "collection error is exit 1",
    ),
    Row(
        47,
        "exitcode",
        EXE,
        "    if collected == 0 {\n        return 5;\n    }",
        "    if collected == 0 {\n        return 0;\n    }",
        ["v2::execute::tests::zero_collected_exits_five"],
        "nothing collected is green",
    ),
    # ------------------------------------------------------- failure taxonomy
    Row(
        48,
        "failure",
        EXE,
        "        matches!(self, TestStatus::Failed | TestStatus::Error)",
        "        matches!(self, TestStatus::Failed)",
        [
            "v2::execute::tests::only_failed_and_error_are_failures",
            "v2::execute::tests::a_teardown_error_alone_exits_one",
        ],
        "an ERROR is not a failure",
    ),
    Row(
        49,
        "failure",
        EXE,
        "        matches!(self, TestStatus::Failed | TestStatus::Error)",
        "        matches!(self, TestStatus::Failed | TestStatus::Error | TestStatus::XPassed)",
        [
            "v2::execute::tests::only_failed_and_error_are_failures",
            "v2::execute::tests::skips_xfails_and_xpasses_leave_a_run_green",
        ],
        "a plain xpass is a failure",
    ),
    Row(
        50,
        "failure",
        EXE,
        "        matches!(self, TestStatus::Failed | TestStatus::Error)",
        "        !matches!(self, TestStatus::Passed)",
        [
            "v2::execute::tests::only_failed_and_error_are_failures",
            "v2::execute::tests::skips_xfails_and_xpasses_leave_a_run_green",
        ],
        "anything not passed is a failure",
    ),
    Row(
        51,
        "failure",
        EXE,
        "        tests.iter().filter(|test| test.status.is_failure()).count() + teardown_errors.len();",
        "        tests.iter().filter(|test| test.status.is_failure()).count();",
        ["v2::execute::tests::a_shutdown_teardown_failure_fails_the_run_without_owning_a_test"],
        "an unattributable teardown failure is not counted",
    ),
    # ------------------------------------------------------------- the summary
    Row(
        52,
        "summary",
        EXE,
        "                TestStatus::XFailed => summary.xfailed += 1,",
        "                TestStatus::XFailed => summary.skipped += 1,",
        ["v2::execute::tests::xfailed_and_xpassed_do_not_leak_into_the_v1_buckets"],
        "xfailed folded into skipped",
    ),
    Row(
        53,
        "summary",
        EXE,
        "                TestStatus::XPassed => summary.xpassed += 1,",
        "                TestStatus::XPassed => summary.passed += 1,",
        ["v2::execute::tests::xfailed_and_xpassed_do_not_leak_into_the_v1_buckets"],
        "xpassed folded into passed",
    ),
    Row(
        54,
        "summary",
        EXE,
        "            deselected,\n            duration,",
        "            deselected: 0,\n            duration,",
        ["v2::execute::tests::the_summary_has_a_bucket_for_every_status"],
        "deselected always zero",
    ),
    # ------------------------------------------------------ status validation
    Row(
        55,
        "status",
        EXE,
        '            "xpassed" => Some(TestStatus::XPassed),\n            "error" => Some(TestStatus::Error),\n            _ => None,',
        '            "xpassed" => Some(TestStatus::XPassed),\n            "error" => Some(TestStatus::Error),\n            _ => Some(TestStatus::Passed),',
        [
            "v2::execute::tests::an_undocumented_status_is_rejected",
            "v2::execute::tests::an_unknown_status_is_protocol_fatal_and_names_the_value",
        ],
        "an unknown status is treated as passed",
    ),
    Row(
        56,
        "status",
        COL,
        '                let Some(status) = TestStatus::parse(&status) else {\n                    return Err(self.execute_protocol(\n                        id,\n                        format!("unknown status `{status}`"),\n                        line,\n                    ));\n                };',
        "                let status = TestStatus::parse(&status).unwrap_or(TestStatus::Passed);",
        ["v2::execute::tests::an_unknown_status_is_protocol_fatal_and_names_the_value"],
        "orchestrator does not validate the status",
    ),
    Row(
        57,
        "protocol",
        COL,
        '                if echoed != id {\n                    return Err(self.execute_protocol(\n                        id,\n                        format!("the response names `{echoed}`, not the requested test"),\n                        line,\n                    ));\n                }',
        "",
        ["v2::execute::tests::a_result_for_the_wrong_test_is_protocol_fatal"],
        "id echo unchecked",
    ),
    # --------------------------------------------------------------- shutdown
    Row(
        58,
        "shutdown",
        COL,
        "        if status.code() == Some(SHUTDOWN_TEARDOWN_EXIT) {",
        "        if false {",
        ["v2::execute::tests::a_shutdown_teardown_failure_reaches_the_report_and_reddens_the_run"],
        "exit 3 after bye is an orchestration failure",
    ),
    Row(
        59,
        "shutdown",
        COL,
        "    pub(crate) fn shutdown_run(&mut self) -> Result<Option<String>, CollectError> {\n        let status = self.shutdown_and_reap()?;\n        if status.success() {\n            return Ok(None);\n        }",
        "    pub(crate) fn shutdown_run(&mut self) -> Result<Option<String>, CollectError> {\n        let status = self.shutdown_and_reap()?;\n        if true {\n            return Ok(None);\n        }",
        ["v2::execute::tests::a_shutdown_teardown_failure_reaches_the_report_and_reddens_the_run"],
        "exit 3 after bye is swallowed",
    ),
    Row(
        60,
        "shutdown",
        EXE,
        "                    if !run.stderr.is_empty() {\n                        worker_stderr.push(run.stderr);\n                    }",
        "",
        ["v2::execute::tests::boundary_teardown_output_is_carried_without_failing_the_run"],
        "worker stderr discarded on a green run",
    ),
    # -------------------------------------------------------------- scheduling
    Row(
        61,
        "schedule",
        EXE,
        "    if !assembled.errors.is_empty() {\n        return Ok(Staged {",
        "    if false {\n        return Ok(Staged {",
        ["v2::execute::tests::a_collection_error_stops_the_run_before_any_test"],
        "tests run despite a collection error",
    ),
    Row(
        62,
        "schedule",
        EXE,
        "    let per_worker = grouped\n        .into_iter()\n        .map(|files| files.into_iter().flatten().collect())\n        .collect();",
        "    let mut per_worker: Vec<Vec<(usize, String)>> = vec![Vec::new(); pool_size];\n    let mut flat: Vec<(usize, usize, String)> = Vec::new();\n    for (worker, files) in grouped.into_iter().enumerate() {\n        for group in files {\n            for item in group {\n                flat.push((worker, item.0, item.1));\n            }\n        }\n    }\n    flat.sort_by_key(|(_, slot, _)| *slot);\n    for (worker, slot, id) in flat {\n        per_worker[worker].push((slot, id));\n    }",
        ["v2::execute::tests::one_workers_tests_are_dispatched_grouped_by_file"],
        "dispatch interleaves files (manifest order, not grouped)",
    ),
    Row(
        63,
        "schedule",
        EXE,
        "            owner[*target] = worker;",
        "            owner[*target] = 0;",
        ["v2::execute::tests::every_test_runs_on_the_worker_that_collected_its_file"],
        "every test routed to worker 0",
    ),
    Row(
        64,
        "schedule",
        EXE,
        "                    for (slot, outcome) in run.results {\n                        slots[slot] = Some(outcome);\n                    }",
        "                    let mut next = slots.iter().position(|s| s.is_none()).unwrap_or(0);\n                    for (_, outcome) in run.results {\n                        slots[next] = Some(outcome);\n                        next += 1;\n                    }",
        ["v2::execute::tests::report_order_is_manifest_order_however_many_workers_run_it"],
        "report assembled in completion order",
    ),
    Row(
        65,
        "schedule",
        EXE,
        "        let _ = select_mask(&[], keyword, mark)?;",
        "",
        ["v2::execute::tests::a_malformed_expression_is_a_usage_error_on_an_empty_tree"],
        "expressions not compiled when nothing was collected",
    ),
    # ------------------------------------------------------------- duplicates
    Row(
        66,
        "duplicates",
        COL,
        "    fn push_file_arg(&mut self, path: &Path, out: &mut Vec<PathBuf>) {\n        if self.walked.contains(path) {\n            return;\n        }\n        self.emitted.insert(path.to_path_buf());\n        out.push(path.to_path_buf());",
        "    fn push_file_arg(&mut self, path: &Path, out: &mut Vec<PathBuf>) {\n        if self.emitted.insert(path.to_path_buf()) {\n            out.push(path.to_path_buf());\n        }",
        ["v2::collect::tests::the_same_file_argument_twice_is_collected_twice"],
        "file args deduplicated like walked files",
    ),
    Row(
        67,
        "duplicates",
        COL,
        "        if self.walked.contains(path) {\n            return;\n        }\n        self.emitted.insert(path.to_path_buf());",
        "        self.emitted.insert(path.to_path_buf());",
        ["v2::collect::tests::a_directory_arg_and_a_file_inside_it_collect_the_file_once"],
        "a walked file no longer suppresses a later file arg",
    ),
    Row(
        68,
        "duplicates",
        COL,
        "        self.walked.insert(path.to_path_buf());\n        if self.emitted.insert(path.to_path_buf()) {\n            out.push(path.to_path_buf());\n        }",
        "        self.walked.insert(path.to_path_buf());\n        out.push(path.to_path_buf());",
        ["v2::collect::tests::a_repeated_directory_argument_is_walked_once"],
        "walked files not deduplicated",
    ),
    # --------------------------------------------------- non-python arguments
    Row(
        69,
        "args",
        COL,
        '    if suffix == "txt" || suffix == "rst" {\n        return Ok(false);\n    }',
        "",
        ["v2::collect::tests::a_text_or_rst_argument_collects_nothing_without_failing"],
        ".txt/.rst treated as `found no collectors`",
    ),
    Row(
        70,
        "args",
        COL,
        "    Err(CollectError::NoCollectors(path.to_path_buf()))",
        "    Ok(false)",
        ["v2::collect::tests::any_other_non_python_argument_is_a_usage_error"],
        "an uncollectable file argument is silently empty",
    ),
    # ------------------------------------------------- the Python CLI surface
    Row(
        71,
        "cli",
        CORE,
        '    errors = summary["error"] + collection_errors',
        '    errors = summary["error"]',
        [
            "python/tests/test_v2_run_cli.py::test_a_collection_error_run_says_error_not_no_tests_ran",
            "python/tests/test_v2_run_cli.py::test_the_summary_line_omits_empty_buckets",
        ],
        "collection errors dropped from the summary line",
        lang="py",
    ),
    Row(
        72,
        "cli",
        CORE,
        '    return report["exit_code"]',
        "    return 0",
        ["python/tests/test_v2_run_cli.py::test_exit_codes_match_pytest[failure]"],
        "the report's exit code is ignored",
        lang="py",
    ),
    Row(
        73,
        "cli",
        CORE,
        "        if report_json is not None:",
        "        if False:",
        ["python/tests/test_v2_run_cli.py::test_the_json_report_is_schema_v2_with_six_statuses"],
        "--report-json never written",
        lang="py",
    ),
    Row(
        74,
        "cli",
        CORE,
        '            if test["status"] in ("failed", "error"):',
        "            if False:",
        [
            "python/tests/test_v2_run_cli.py::test_a_failure_is_reported_on_stdout_without_a_report_file"
        ],
        "failures not printed",
        lang="py",
    ),
    Row(
        75,
        "cli",
        CLI,
        "            keyword=args.pattern,\n            mark_expr=args.mark_expr,\n            report_json=args.report_json,",
        "            keyword=None,\n            mark_expr=None,\n            report_json=args.report_json,",
        [
            "python/tests/test_v2_run_cli.py::test_the_cli_forwards_selection_pool_size_and_the_report_path"
        ],
        "-k/-m not forwarded to the run",
        lang="py",
    ),
    Row(
        76,
        "cli",
        CORE,
        '        for chunk in report.get("worker_stderr", []):',
        "        for chunk in []:",
        [
            "python/tests/test_v2_run_cli.py::test_boundary_teardown_output_is_surfaced_but_never_fails_the_run"
        ],
        "worker stderr never printed",
        lang="py",
    ),
]


def run_pytest(tests: list[str]) -> tuple[int, str]:
    cmd = ["uv", "run", "pytest", "-q", "--no-header", *tests]
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def run_cargo(tests: list[str]) -> tuple[int, str]:
    env = dict(os.environ)
    base = subprocess.run(
        ["uv", "run", "python", "-c", "import sys;print(sys.base_prefix)"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    env["PATH"] = base + os.pathsep + env["PATH"]
    cmd = ["cargo", "test", "--lib", "--", "--exact", *tests]
    try:
        proc = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True, env=env, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def main() -> int:
    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    killed: list[int] = []
    survived: list[tuple[int, str]] = []
    bad: list[tuple[int, str]] = []

    for row in ROWS:
        if row.count == 0 or (only is not None and row.id not in only):
            continue
        edits = [(row.file, row.old, row.new), *row.extra]
        originals: dict[str, str] = {}
        ok = True
        for path, old, new in edits:
            target = REPO / path
            if path not in originals:
                originals[path] = target.read_text(encoding="utf-8")
            current = target.read_text(encoding="utf-8")
            hits = current.count(old)
            if hits != 1:
                bad.append((row.id, f"anchor appears {hits}x in {path}"))
                ok = False
                break
            target.write_text(current.replace(old, new), encoding="utf-8")
        if not ok:
            for path, text in originals.items():
                (REPO / path).write_text(text, encoding="utf-8")
            continue

        started = time.time()
        code, tail = run_pytest(row.tests) if row.lang == "py" else run_cargo(row.tests)
        elapsed = time.time() - started
        for path, text in originals.items():
            (REPO / path).write_text(text, encoding="utf-8")

        if code == -1:
            survived.append((row.id, f"TIMEOUT after {TIMEOUT}s"))
            verdict = "SURVIVED (timeout)"
        elif code != 0:
            killed.append(row.id)
            verdict = "killed"
        else:
            survived.append((row.id, tail))
            verdict = "SURVIVED"
        print(f"[{row.id:>3}] {row.area:<11} {verdict:<18} {elapsed:5.1f}s  {row.note}", flush=True)

    total = len(killed) + len(survived)
    print(f"\n{len(killed)}/{total} killed")
    if survived:
        print("\nSURVIVORS:")
        for rid, tail in survived:
            print(f"  row {rid}: {tail[:300]}")
    if bad:
        print("\nBAD ANCHORS:")
        for rid, why in bad:
            print(f"  row {rid}: {why}")
    return 0 if not survived and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
