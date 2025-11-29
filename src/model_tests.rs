//! Unit tests for model.rs

#[cfg(test)]
mod tests {
    use super::super::model::*;
    use indexmap::IndexMap;
    use pyo3::ffi::c_str;
    use pyo3::prelude::*;
    use std::path::PathBuf;

    #[test]
    fn test_fixture_new() {
        Python::with_gil(|py| {
            let callable = py.eval(c_str!("lambda x: x"), None, None).unwrap();
            let fixture = Fixture::new(
                "test_fixture".to_string(),
                callable.unbind(),
                vec!["param1".to_string(), "param2".to_string()],
                FixtureScope::Function,
                false,
                false,
                false,
                false,
                None,
            );

            assert_eq!(fixture.name, "test_fixture");
            assert_eq!(fixture.parameters, vec!["param1", "param2"]);
            assert_eq!(fixture.scope, FixtureScope::Function);
            assert!(!fixture.is_generator);
            assert!(!fixture.is_async);
            assert!(!fixture.is_async_generator);
        });
    }

    #[test]
    fn test_test_case_unique_id() {
        Python::with_gil(|py| {
            let callable = py.eval(c_str!("lambda: None"), None, None).unwrap();
            let test_case = TestCase {
                name: "test_example".to_string(),
                display_name: "test_example".to_string(),
                path: PathBuf::from("/home/test/test_file.py"),
                callable: callable.unbind(),
                parameters: vec![],
                parameter_values: ParameterMap::new(),
                skip_reason: None,
                marks: vec![],
                class_name: None,
                fixture_param_indices: IndexMap::new(),
                indirect_params: vec![],
            };

            let unique_id = test_case.unique_id();
            assert!(unique_id.contains("test_file.py"));
            assert!(unique_id.contains("test_example"));
        });
    }

    #[test]
    fn test_test_case_with_skip_reason() {
        Python::with_gil(|py| {
            let callable = py.eval(c_str!("lambda: None"), None, None).unwrap();
            let test_case = TestCase {
                name: "test_skipped".to_string(),
                display_name: "test_skipped".to_string(),
                path: PathBuf::from("/home/test/test_file.py"),
                callable: callable.unbind(),
                parameters: vec![],
                parameter_values: ParameterMap::new(),
                skip_reason: Some("Not implemented yet".to_string()),
                marks: vec![],
                class_name: None,
                fixture_param_indices: IndexMap::new(),
                indirect_params: vec![],
            };

            assert_eq!(
                test_case.skip_reason,
                Some("Not implemented yet".to_string())
            );
        });
    }

    #[test]
    fn test_test_module_new() {
        let fixtures = IndexMap::new();
        let tests = vec![];
        let module = TestModule::new(PathBuf::from("/test/module.py"), fixtures, tests);

        assert_eq!(module.path, PathBuf::from("/test/module.py"));
        assert!(module.fixtures.is_empty());
        assert!(module.tests.is_empty());
    }

    #[test]
    fn test_run_configuration_new_with_defaults() {
        let config = RunConfiguration::new(
            None,
            None,
            None,
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
            None,
        );

        assert!(config.pattern.is_none());
        assert!(config.mark_expr.is_none());
        assert!(config.worker_count >= 1);
        assert!(config.capture_output);
    }

    #[test]
    fn test_run_configuration_new_with_pattern() {
        let config = RunConfiguration::new(
            Some("test_.*".to_string()),
            None,
            Some(4),
            false,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
            None,
        );

        assert_eq!(config.pattern, Some("test_.*".to_string()));
        assert_eq!(config.worker_count, 4);
        assert!(!config.capture_output);
    }

    #[test]
    fn test_run_configuration_clone() {
        let config = RunConfiguration::new(
            Some("pattern".to_string()),
            None,
            Some(2),
            true,
            true,
            LastFailedMode::None,
            false,
            false,
            false,
            false,
            false,
            None,
        );
        let cloned = config.clone();

        assert_eq!(config.pattern, cloned.pattern);
        assert_eq!(config.worker_count, cloned.worker_count);
        assert_eq!(config.capture_output, cloned.capture_output);
    }

    #[test]
    fn test_py_run_report_new() {
        Python::with_gil(|_py| {
            let results = vec![];
            let collection_errors = vec![];
            let report = PyRunReport::new(10, 8, 1, 1, 1.5, results, collection_errors);

            assert_eq!(report.total, 10);
            assert_eq!(report.passed, 8);
            assert_eq!(report.failed, 1);
            assert_eq!(report.skipped, 1);
            assert_eq!(report.duration, 1.5);
        });
    }

    #[test]
    fn test_py_test_result_passed() {
        let result = PyTestResult::passed(
            "test_example".to_string(),
            "/path/to/test.py".to_string(),
            0.5,
            Some("output".to_string()),
            None,
            vec![],
        );

        assert_eq!(result.name, "test_example");
        assert_eq!(result.path, "/path/to/test.py");
        assert_eq!(result.status, "passed");
        assert_eq!(result.duration, 0.5);
        assert_eq!(result.message, None);
        assert_eq!(result.stdout, Some("output".to_string()));
        assert_eq!(result.stderr, None);
    }

    #[test]
    fn test_py_test_result_failed() {
        let result = PyTestResult::failed(
            "test_fail".to_string(),
            "/path/to/test.py".to_string(),
            1.0,
            "AssertionError".to_string(),
            Some("stdout".to_string()),
            Some("stderr".to_string()),
            vec![],
        );

        assert_eq!(result.name, "test_fail");
        assert_eq!(result.status, "failed");
        assert_eq!(result.message, Some("AssertionError".to_string()));
        assert_eq!(result.stdout, Some("stdout".to_string()));
        assert_eq!(result.stderr, Some("stderr".to_string()));
    }

    #[test]
    fn test_py_test_result_skipped() {
        let result = PyTestResult::skipped(
            "test_skip".to_string(),
            "/path/to/test.py".to_string(),
            0.0,
            "Not implemented".to_string(),
            vec![],
        );

        assert_eq!(result.name, "test_skip");
        assert_eq!(result.status, "skipped");
        assert_eq!(result.message, Some("Not implemented".to_string()));
        assert_eq!(result.stdout, None);
        assert_eq!(result.stderr, None);
    }

    #[test]
    fn test_module_id_generator_sequential() {
        let generator = ModuleIdGenerator::default();

        let id1 = generator.next();
        let id2 = generator.next();
        let id3 = generator.next();

        assert_eq!(id1, 0);
        assert_eq!(id2, 1);
        assert_eq!(id3, 2);
    }

    #[test]
    fn test_module_id_generator_multiple_instances() {
        let gen1 = ModuleIdGenerator::default();
        let gen2 = ModuleIdGenerator::default();

        assert_eq!(gen1.next(), 0);
        assert_eq!(gen2.next(), 0);
        assert_eq!(gen1.next(), 1);
        assert_eq!(gen2.next(), 1);
    }

    #[test]
    fn test_invalid_test_definition() {
        let err = invalid_test_definition("Test error message");
        assert!(err.to_string().contains("Test error message"));
    }

    #[test]
    fn test_parameter_map_ordering() {
        let mut params = ParameterMap::new();
        Python::with_gil(|py| {
            params.insert("first".to_string(), py.None());
            params.insert("second".to_string(), py.None());
            params.insert("third".to_string(), py.None());

            let keys: Vec<&String> = params.keys().collect();
            assert_eq!(keys, vec!["first", "second", "third"]);
        });
    }

    #[test]
    fn test_test_case_with_parameters() {
        Python::with_gil(|py| {
            let callable = py.eval(c_str!("lambda x, y: x + y"), None, None).unwrap();
            let mut param_values = ParameterMap::new();
            param_values.insert(
                "x".to_string(),
                5i32.into_pyobject(py).unwrap().into_any().unbind(),
            );
            param_values.insert(
                "y".to_string(),
                10i32.into_pyobject(py).unwrap().into_any().unbind(),
            );

            let test_case = TestCase {
                name: "test_add".to_string(),
                display_name: "test_add[5-10]".to_string(),
                path: PathBuf::from("/test.py"),
                callable: callable.unbind(),
                parameters: vec!["x".to_string(), "y".to_string()],
                parameter_values: param_values,
                skip_reason: None,
                marks: vec![],
                class_name: None,
                fixture_param_indices: IndexMap::new(),
                indirect_params: vec![],
            };

            assert_eq!(test_case.parameters.len(), 2);
            assert_eq!(test_case.parameter_values.len(), 2);
            assert!(test_case.display_name.contains("[5-10]"));
        });
    }

    #[test]
    fn test_to_relative_path_within_cwd() {
        let cwd = std::env::current_dir().unwrap();
        let test_path = cwd.join("tests").join("test_file.py");

        let result = to_relative_path(&test_path);

        // Should start with separator and contain the relative path
        assert!(result.starts_with(std::path::MAIN_SEPARATOR));
        assert!(result.contains("tests"));
        assert!(result.contains("test_file.py"));
        // Should NOT contain the full cwd path
        assert!(!result.contains(cwd.to_str().unwrap()));
    }

    #[test]
    fn test_to_relative_path_outside_cwd() {
        // Path that's definitely not under cwd
        let test_path = if cfg!(windows) {
            PathBuf::from("C:\\some\\other\\path\\test.py")
        } else {
            PathBuf::from("/some/other/path/test.py")
        };

        let result = to_relative_path(&test_path);

        // Should return the full path since it's not under cwd
        assert_eq!(result, test_path.to_string_lossy().to_string());
    }

    #[test]
    fn test_to_relative_path_cwd_itself() {
        let cwd = std::env::current_dir().unwrap();

        let result = to_relative_path(&cwd);

        // Should return "." for the cwd itself
        assert_eq!(result, ".");
    }

    #[test]
    fn test_to_relative_path_nested_directory() {
        let cwd = std::env::current_dir().unwrap();
        let test_path = cwd.join("src").join("output").join("spinner_display.rs");

        let result = to_relative_path(&test_path);

        // Should contain the nested path components
        assert!(result.contains("src"));
        assert!(result.contains("output"));
        assert!(result.contains("spinner_display.rs"));
        // Should start with separator
        assert!(result.starts_with(std::path::MAIN_SEPARATOR));
    }
}
