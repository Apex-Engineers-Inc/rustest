/// Proof of Concept: Progress bar with live statistics
///
/// This demonstrates a compact overview mode using a single progress bar
/// with running statistics, ideal for large test suites.
///
/// Run with: cargo run --example indicatif_progress_bar_poc
use indicatif::{ProgressBar, ProgressStyle};
use std::thread;
use std::time::Duration;

struct TestSuite {
    total_tests: u64,
    test_names: Vec<String>,
    test_durations: Vec<u64>,
    test_results: Vec<bool>, // true = pass, false = fail
}

fn main() {
    // Simulate a large test suite
    let suite = TestSuite {
        total_tests: 234,
        test_names: generate_test_names(),
        test_durations: generate_test_durations(234),
        test_results: generate_test_results(234),
    };

    println!("\n🦀 Rustest - Progress Bar POC\n");
    println!("Running {} tests...\n", suite.total_tests);

    // Create main progress bar
    let pb = ProgressBar::new(suite.total_tests);

    // Configure style with statistics
    pb.set_style(
        ProgressStyle::with_template(
            "{spinner:.green} [{elapsed_precise}] [{wide_bar:.cyan/blue}] {pos}/{len} ({percent}%)\n                  {msg}"
        )
        .unwrap()
        .progress_chars("━━╸─")
        .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ "),
    );

    pb.enable_steady_tick(Duration::from_millis(100));

    let mut passed = 0;
    let mut failed = 0;
    let skipped = 0;

    // Simulate test execution
    for i in 0..suite.total_tests as usize {
        let test_name = &suite.test_names[i];
        let duration = suite.test_durations[i];
        let will_pass = suite.test_results[i];

        // Update message to show current test
        pb.set_message(format!(
            "✅ {}  ❌ {}  ⊘ {}  |  Current: {}",
            passed,
            failed,
            skipped,
            truncate_test_name(test_name, 50)
        ));

        // Simulate test execution
        thread::sleep(Duration::from_millis(duration));

        // Update counters
        if will_pass {
            passed += 1;
        } else {
            failed += 1;
        }

        pb.inc(1);
    }

    // Final summary
    pb.finish_with_message(format!(
        "✅ {}  ❌ {}  ⊘ {}  |  Done!",
        passed, failed, skipped
    ));

    println!("\n");

    // Print summary
    let total_time = pb.elapsed().as_secs_f64();
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!();
    if failed > 0 {
        println!(
            "  ❌ {} tests: {} passed, {} failed in {:.2}s",
            suite.total_tests, passed, failed, total_time
        );
    } else {
        println!(
            "  ✅ {} tests: {} passed in {:.2}s",
            suite.total_tests, passed, total_time
        );
    }
    println!();
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!();
}

fn generate_test_names() -> Vec<String> {
    let files = vec![
        "test_auth.py",
        "test_database.py",
        "test_api.py",
        "test_utils.py",
        "test_models.py",
        "test_views.py",
        "test_forms.py",
        "test_serializers.py",
    ];

    let test_prefixes = vec![
        "test_create",
        "test_read",
        "test_update",
        "test_delete",
        "test_list",
        "test_detail",
        "test_filter",
        "test_search",
        "test_validate",
        "test_authenticate",
        "test_authorize",
        "test_permission",
        "test_edge_case",
        "test_error_handling",
        "test_success",
        "test_failure",
    ];

    let mut names = Vec::new();
    for _ in 0..234 {
        let file = files[names.len() % files.len()];
        let prefix = test_prefixes[(names.len() / files.len()) % test_prefixes.len()];
        names.push(format!("tests/{}::{}", file, prefix));
    }
    names
}

fn generate_test_durations(count: u64) -> Vec<u64> {
    // Simulate varying test durations (10-100ms)
    (0..count)
        .map(|i| {
            let base = 10 + (i % 90);
            // Some tests are slower
            if i % 23 == 0 {
                base * 3
            } else {
                base
            }
        })
        .collect()
}

fn generate_test_results(count: u64) -> Vec<bool> {
    // Most tests pass, some fail
    (0..count).map(|i| i % 17 != 0).collect()
}

fn truncate_test_name(name: &str, max_len: usize) -> String {
    if name.len() <= max_len {
        name.to_string()
    } else {
        format!("...{}", &name[name.len() - (max_len - 3)..])
    }
}
