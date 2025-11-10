//! Unit tests for python_support.rs

#[cfg(test)]
mod tests {
    use super::super::python_support::*;
    use pyo3::prelude::*;
    use pyo3::types::PyList;
    use std::env;
    use std::fs;

    #[test]
    fn test_pypaths_from_vec() {
        let paths = vec!["/tmp/test1".to_string(), "/tmp/test2".to_string()];
        let py_paths = PyPaths::from_vec(paths.clone());

        // The debug format should contain the paths
        let debug_str = format!("{:?}", py_paths);
        assert!(debug_str.contains("/tmp/test1"));
        assert!(debug_str.contains("/tmp/test2"));
    }

    #[test]
    fn test_pypaths_clone() {
        let paths = vec!["/tmp/test".to_string()];
        let py_paths = PyPaths::from_vec(paths);
        let cloned = py_paths.clone();

        assert_eq!(format!("{:?}", py_paths), format!("{:?}", cloned));
    }

    #[test]
    fn test_pypaths_materialise_with_existing_path() {
        // Create a temporary directory for testing
        let temp_dir = env::temp_dir().join("rustest_test");
        fs::create_dir_all(&temp_dir).unwrap();

        let paths = vec![temp_dir.to_string_lossy().to_string()];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_ok());
            let materialized = result.unwrap();
            assert_eq!(materialized.len(), 1);
            assert!(materialized[0].exists());
        });

        // Cleanup
        fs::remove_dir(&temp_dir).ok();
    }

    #[test]
    fn test_pypaths_materialise_with_nonexistent_path() {
        let paths = vec!["/nonexistent/path/12345".to_string()];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_err());
            let err = result.unwrap_err();
            assert!(err.to_string().contains("does not exist"));
        });
    }

    #[test]
    fn test_pypaths_materialise_multiple_paths() {
        // Create multiple temporary directories
        let temp_dir1 = env::temp_dir().join("rustest_test1");
        let temp_dir2 = env::temp_dir().join("rustest_test2");
        fs::create_dir_all(&temp_dir1).unwrap();
        fs::create_dir_all(&temp_dir2).unwrap();

        let paths = vec![
            temp_dir1.to_string_lossy().to_string(),
            temp_dir2.to_string_lossy().to_string(),
        ];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_ok());
            let materialized = result.unwrap();
            assert_eq!(materialized.len(), 2);
            assert!(materialized[0].exists());
            assert!(materialized[1].exists());
        });

        // Cleanup
        fs::remove_dir(&temp_dir1).ok();
        fs::remove_dir(&temp_dir2).ok();
    }

    #[test]
    fn test_pypaths_materialise_canonicalization() {
        // Create a temp directory
        let temp_dir = env::temp_dir().join("rustest_canon");
        fs::create_dir_all(&temp_dir).unwrap();

        // Use a path with .. to test canonicalization
        let complex_path = temp_dir.join("..").join(temp_dir.file_name().unwrap());
        let paths = vec![complex_path.to_string_lossy().to_string()];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_ok());
            let materialized = result.unwrap();
            // The path should be canonicalized (no ..)
            let path_str = materialized[0].to_string_lossy();
            assert!(!path_str.contains(".."));
        });

        // Cleanup
        fs::remove_dir(&temp_dir).ok();
    }

    #[test]
    fn test_pypaths_materialise_with_file() {
        // Create a temporary file
        let temp_file = env::temp_dir().join("rustest_test_file.txt");
        fs::write(&temp_file, "test content").unwrap();

        let paths = vec![temp_file.to_string_lossy().to_string()];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_ok());
            let materialized = result.unwrap();
            assert_eq!(materialized.len(), 1);
            assert!(materialized[0].is_file());
        });

        // Cleanup
        fs::remove_file(&temp_file).ok();
    }

    #[test]
    fn test_pypaths_empty_vec() {
        let py_paths = PyPaths::from_vec(vec![]);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            assert!(result.is_ok());
            let materialized = result.unwrap();
            assert_eq!(materialized.len(), 0);
        });
    }

    #[test]
    fn test_pypaths_materialise_mixed_valid_invalid() {
        // Create one valid directory
        let temp_dir = env::temp_dir().join("rustest_valid");
        fs::create_dir_all(&temp_dir).unwrap();

        let paths = vec![
            temp_dir.to_string_lossy().to_string(),
            "/nonexistent/invalid".to_string(),
        ];
        let py_paths = PyPaths::from_vec(paths);

        pyo3::Python::with_gil(|_py| {
            let result = py_paths.materialise();
            // Should fail because one path is invalid
            assert!(result.is_err());
        });

        // Cleanup
        fs::remove_dir(&temp_dir).ok();
    }

    // ========================
    // Path Discovery Tests
    // ========================

    #[test]
    fn test_find_basedir_with_test_directory_without_init() {
        // Create: project/tests/ (no __init__.py)
        let project_dir = env::temp_dir().join("rustest_basedir_test1");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();

        // When tests/ has no __init__.py, basedir should be project/
        let basedir = find_basedir(&tests_dir);
        assert_eq!(basedir, project_dir);

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_basedir_with_nested_packages() {
        // Create: project/mypackage/subpackage/tests/
        // with __init__.py in mypackage/ and subpackage/
        let project_dir = env::temp_dir().join("rustest_basedir_test2");
        let mypackage = project_dir.join("mypackage");
        let subpackage = mypackage.join("subpackage");
        let tests_dir = subpackage.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();

        // Create __init__.py files
        fs::write(mypackage.join("__init__.py"), "").unwrap();
        fs::write(subpackage.join("__init__.py"), "").unwrap();

        // tests/ has no __init__.py, so should return project/
        let basedir = find_basedir(&tests_dir);
        assert_eq!(basedir, tests_dir.parent().unwrap());

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_basedir_with_file_path() {
        // Create: project/tests/test_example.py (no __init__.py in tests/)
        let project_dir = env::temp_dir().join("rustest_basedir_test3");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();
        let test_file = tests_dir.join("test_example.py");
        fs::write(&test_file, "def test(): pass").unwrap();

        // When given a file, should find basedir from parent directory
        let basedir = find_basedir(&test_file);
        assert_eq!(basedir, project_dir);

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_basedir_with_package_tests() {
        // Create: project/mypackage/ with __init__.py
        // and project/mypackage/tests/ without __init__.py
        let project_dir = env::temp_dir().join("rustest_basedir_test4");
        let mypackage = project_dir.join("mypackage");
        let tests_dir = mypackage.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();
        fs::write(mypackage.join("__init__.py"), "").unwrap();

        // tests/ has no __init__.py, basedir should be mypackage/
        let basedir = find_basedir(&tests_dir);
        assert_eq!(basedir, mypackage);

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_src_directory_exists_at_project_root() {
        // Create: project/src/mypackage/
        let project_dir = env::temp_dir().join("rustest_src_test1");
        let src_dir = project_dir.join("src");
        let mypackage = src_dir.join("mypackage");
        fs::create_dir_all(&mypackage).unwrap();

        // Should find src/ directory
        let found_src = find_src_directory(&project_dir);
        assert!(found_src.is_some());
        assert_eq!(found_src.unwrap(), src_dir);

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_src_directory_from_subdirectory() {
        // Create: project/src/ and project/tests/
        let project_dir = env::temp_dir().join("rustest_src_test2");
        let src_dir = project_dir.join("src");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&src_dir).unwrap();
        fs::create_dir_all(&tests_dir).unwrap();

        // When searching from tests/, should find src/ at parent level
        let found_src = find_src_directory(&tests_dir);
        assert!(found_src.is_some());
        assert_eq!(found_src.unwrap(), src_dir);

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_src_directory_not_exists() {
        // Create: project/ without src/
        let project_dir = env::temp_dir().join("rustest_src_test3");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();

        // Should return None when no src/ directory exists
        let found_src = find_src_directory(&tests_dir);
        assert!(found_src.is_none());

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_find_src_directory_stops_at_filesystem_root() {
        // Use a deep path that doesn't have src/ anywhere
        let deep_path = env::temp_dir().join("a").join("b").join("c");
        fs::create_dir_all(&deep_path).unwrap();

        // Should return None and not panic when reaching filesystem root
        let found_src = find_src_directory(&deep_path);
        assert!(found_src.is_none());

        // Cleanup
        fs::remove_dir_all(env::temp_dir().join("a")).ok();
    }

    #[test]
    fn test_setup_python_path_adds_project_root() {
        // Create: project/tests/
        let project_dir = env::temp_dir().join("rustest_setup_test1");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();

        pyo3::Python::with_gil(|py| {
            let paths = vec![tests_dir.clone()];
            let result = setup_python_path(py, &paths);
            assert!(result.is_ok());

            // Verify project_dir is in sys.path
            let sys = py.import("sys").unwrap();
            let sys_path: Bound<'_, PyList> = sys.getattr("path").unwrap().extract().unwrap();
            let project_str = project_dir.to_string_lossy();

            let found = sys_path.iter().any(|item| {
                item.extract::<String>()
                    .map(|s| s == project_str)
                    .unwrap_or(false)
            });

            assert!(found, "Project directory should be in sys.path");
        });

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_setup_python_path_adds_src_directory() {
        // Create: project/src/mypackage/ and project/tests/
        let project_dir = env::temp_dir().join("rustest_setup_test2");
        let src_dir = project_dir.join("src");
        let mypackage = src_dir.join("mypackage");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&mypackage).unwrap();
        fs::create_dir_all(&tests_dir).unwrap();

        pyo3::Python::with_gil(|py| {
            let paths = vec![tests_dir.clone()];
            let result = setup_python_path(py, &paths);
            assert!(result.is_ok());

            // Verify both project_dir and src_dir are in sys.path
            let sys = py.import("sys").unwrap();
            let sys_path: Bound<'_, PyList> = sys.getattr("path").unwrap().extract().unwrap();
            let src_str = src_dir.to_string_lossy();

            let found_src = sys_path.iter().any(|item| {
                item.extract::<String>()
                    .map(|s| s == src_str)
                    .unwrap_or(false)
            });

            assert!(found_src, "src/ directory should be in sys.path");
        });

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_setup_python_path_avoids_duplicates() {
        // Create: project/tests/
        let project_dir = env::temp_dir().join("rustest_setup_test3");
        let tests_dir = project_dir.join("tests");
        fs::create_dir_all(&tests_dir).unwrap();

        pyo3::Python::with_gil(|py| {
            let paths = vec![tests_dir.clone()];

            // Add the path twice
            setup_python_path(py, &paths).unwrap();
            setup_python_path(py, &paths).unwrap();

            // Verify project_dir appears only once in sys.path
            let sys = py.import("sys").unwrap();
            let sys_path: Bound<'_, PyList> = sys.getattr("path").unwrap().extract().unwrap();
            let project_str = project_dir.to_string_lossy();

            let count = sys_path
                .iter()
                .filter(|item| {
                    item.extract::<String>()
                        .map(|s| s == project_str.as_ref())
                        .unwrap_or(false)
                })
                .count();

            assert_eq!(
                count, 1,
                "Project directory should appear only once in sys.path"
            );
        });

        // Cleanup
        fs::remove_dir_all(&project_dir).ok();
    }

    #[test]
    fn test_setup_python_path_with_multiple_test_paths() {
        // Create: project1/tests/ and project2/tests/
        let project1 = env::temp_dir().join("rustest_setup_test4a");
        let tests1 = project1.join("tests");
        let project2 = env::temp_dir().join("rustest_setup_test4b");
        let tests2 = project2.join("tests");
        fs::create_dir_all(&tests1).unwrap();
        fs::create_dir_all(&tests2).unwrap();

        pyo3::Python::with_gil(|py| {
            let paths = vec![tests1.clone(), tests2.clone()];
            let result = setup_python_path(py, &paths);
            assert!(result.is_ok());

            // Verify both project directories are in sys.path
            let sys = py.import("sys").unwrap();
            let sys_path: Bound<'_, PyList> = sys.getattr("path").unwrap().extract().unwrap();
            let project1_str = project1.to_string_lossy();
            let project2_str = project2.to_string_lossy();

            let found1 = sys_path.iter().any(|item| {
                item.extract::<String>()
                    .map(|s| s == project1_str)
                    .unwrap_or(false)
            });

            let found2 = sys_path.iter().any(|item| {
                item.extract::<String>()
                    .map(|s| s == project2_str)
                    .unwrap_or(false)
            });

            assert!(found1, "Project1 directory should be in sys.path");
            assert!(found2, "Project2 directory should be in sys.path");
        });

        // Cleanup
        fs::remove_dir_all(&project1).ok();
        fs::remove_dir_all(&project2).ok();
    }
}
