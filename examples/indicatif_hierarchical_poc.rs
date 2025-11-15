/// Proof of Concept: Hierarchical test display with nested spinners
///
/// This demonstrates a more detailed view showing individual tests
/// within each file, similar to verbose mode with live updates.
///
/// Run with: cargo run --example indicatif_hierarchical_poc

use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone)]
struct Test {
    name: String,
    duration_ms: u64,
    will_fail: bool,
}

struct TestFile {
    path: String,
    tests: Vec<Test>,
}

fn main() {
    let test_files = vec![
        TestFile {
            path: "tests/test_auth.py".to_string(),
            tests: vec![
                Test {
                    name: "test_login".to_string(),
                    duration_ms: 50,
                    will_fail: false,
                },
                Test {
                    name: "test_logout".to_string(),
                    duration_ms: 30,
                    will_fail: false,
                },
                Test {
                    name: "test_invalid_credentials".to_string(),
                    duration_ms: 40,
                    will_fail: true,
                },
            ],
        },
        TestFile {
            path: "tests/test_database.py".to_string(),
            tests: vec![
                Test {
                    name: "test_connection".to_string(),
                    duration_ms: 100,
                    will_fail: false,
                },
                Test {
                    name: "test_query".to_string(),
                    duration_ms: 150,
                    will_fail: false,
                },
                Test {
                    name: "test_transaction".to_string(),
                    duration_ms: 120,
                    will_fail: false,
                },
            ],
        },
    ];

    println!("\n🦀 Rustest - Hierarchical Display POC\n");

    let multi = MultiProgress::new();

    // Style for file-level progress
    let file_style = ProgressStyle::with_template(
        "{spinner:.cyan} {msg:<60} {pos}/{len}"
    )
    .unwrap()
    .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ");

    // Style for test-level progress
    let test_style = ProgressStyle::with_template(
        "  {spinner:.green} {msg:<58} {elapsed:.dim}"
    )
    .unwrap()
    .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ");

    let completed_test_style = ProgressStyle::with_template(
        "  {prefix} {msg:<58} {elapsed:.dim}"
    )
    .unwrap();

    let mut handles = vec![];

    for file in test_files {
        let multi = multi.clone();
        let file_style = file_style.clone();
        let test_style = test_style.clone();
        let completed_test_style = completed_test_style.clone();

        let handle = thread::spawn(move || {
            // Create file-level progress bar
            let file_pb = multi.add(ProgressBar::new(file.tests.len() as u64));
            file_pb.set_style(file_style);
            file_pb.set_message(file.path.clone());
            file_pb.enable_steady_tick(Duration::from_millis(100));

            let file_start = Instant::now();

            // Run each test with its own progress indicator
            for test in &file.tests {
                // Create test-level spinner
                let test_pb = multi.insert_after(&file_pb, ProgressBar::new_spinner());
                test_pb.set_style(test_style.clone());
                test_pb.set_message(test.name.clone());
                test_pb.enable_steady_tick(Duration::from_millis(100));

                // Simulate test execution
                thread::sleep(Duration::from_millis(test.duration_ms));

                // Update to completion state
                test_pb.set_style(completed_test_style.clone());
                if test.will_fail {
                    test_pb.set_prefix("❌");
                    test_pb.finish_with_message(format!(
                        "{} - FAILED",
                        test.name
                    ));
                } else {
                    test_pb.set_prefix("✅");
                    test_pb.finish_with_message(format!(
                        "{}",
                        test.name
                    ));
                }

                file_pb.inc(1);
            }

            let file_duration = file_start.elapsed();
            let passed = file.tests.iter().filter(|t| !t.will_fail).count();
            let failed = file.tests.iter().filter(|t| t.will_fail).count();

            // Finish file progress bar
            if failed > 0 {
                file_pb.finish_with_message(format!(
                    "{} - {} passed, {} failed ({:.2}s)",
                    file.path,
                    passed,
                    failed,
                    file_duration.as_secs_f64()
                ));
            } else {
                file_pb.finish_with_message(format!(
                    "{} - {} passed ({:.2}s)",
                    file.path,
                    passed,
                    file_duration.as_secs_f64()
                ));
            }
        });

        handles.push(handle);

        // Small delay between starting files to show the effect
        thread::sleep(Duration::from_millis(100));
    }

    for handle in handles {
        handle.join().unwrap();
    }

    println!("\n✨ Test run complete!\n");
}
