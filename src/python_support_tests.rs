//! Unit tests for python_support.rs

#[cfg(test)]
mod tests {
    use super::super::python_support::*;
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
}
