/// Proof of Concept: File-level spinner display for rustest
///
/// This demonstrates how indicatif could be integrated to show
/// real-time progress as tests run.
///
/// Run with: cargo run --example indicatif_poc
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::thread;
use std::time::Duration;

/// Simulates a test file with multiple tests
struct TestFile {
    path: String,
    test_count: usize,
    // Simulated test execution times (in milliseconds)
    test_durations: Vec<u64>,
    will_fail: Vec<bool>,
}

fn main() {
    // Simulated test suite
    let test_files = vec![
        TestFile {
            path: "tests/test_auth.py".to_string(),
            test_count: 5,
            test_durations: vec![50, 30, 40, 60, 20],
            will_fail: vec![false, false, false, false, false],
        },
        TestFile {
            path: "tests/test_database.py".to_string(),
            test_count: 8,
            test_durations: vec![100, 150, 80, 90, 120, 70, 110, 95],
            will_fail: vec![false, false, true, false, false, false, false, false],
        },
        TestFile {
            path: "tests/test_api.py".to_string(),
            test_count: 3,
            test_durations: vec![200, 180, 190],
            will_fail: vec![false, false, false],
        },
        TestFile {
            path: "tests/test_utils.py".to_string(),
            test_count: 7,
            test_durations: vec![10, 15, 12, 18, 20, 14, 11],
            will_fail: vec![false, false, false, false, false, false, false],
        },
    ];

    println!("\n🦀 Rustest - Indicatif POC\n");

    // Create multi-progress manager
    let multi = MultiProgress::new();

    // Spawn threads to simulate parallel test execution
    let mut handles = vec![];

    for file in test_files {
        let multi = multi.clone();

        let handle = thread::spawn(move || {
            run_test_file(file, &multi);
        });

        handles.push(handle);
    }

    // Wait for all test files to complete
    for handle in handles {
        handle.join().unwrap();
    }

    // Print summary
    println!("\n✅ All tests completed!\n");
}

fn run_test_file(file: TestFile, multi: &MultiProgress) {
    // Create spinner for this file
    let pb = multi.add(ProgressBar::new(file.test_count as u64));

    // Configure spinner style
    pb.set_style(
        ProgressStyle::with_template("{spinner:.green} [{elapsed_precise}] {msg:<50} {pos}/{len}")
            .unwrap()
            .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ "),
    );

    pb.set_message(format!("Running {}", file.path));

    // Enable steady tick for smooth animation
    pb.enable_steady_tick(Duration::from_millis(100));

    let mut passed = 0;
    let mut failed = 0;

    // Simulate running each test
    for (i, (duration, will_fail)) in file.test_durations.iter().zip(&file.will_fail).enumerate() {
        thread::sleep(Duration::from_millis(*duration));

        if *will_fail {
            failed += 1;
        } else {
            passed += 1;
        }

        pb.inc(1);
        pb.set_message(format!(
            "Running {} (test {}/{})",
            file.path,
            i + 1,
            file.test_count
        ));
    }

    // Finish with appropriate status
    if failed > 0 {
        pb.finish_with_message(format!(
            "❌ {} - {} passed, {} failed",
            file.path, passed, failed
        ));
    } else {
        pb.finish_with_message(format!("✅ {} - {} passed", file.path, passed));
    }
}
