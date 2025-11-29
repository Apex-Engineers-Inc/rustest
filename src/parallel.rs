//! Parallel test execution infrastructure.
//!
//! This module provides thread-safe fixture caching and parallel test execution
//! strategies for rustest. It supports two execution paths:
//!
//! 1. **Sync tests**: Run in parallel using Rayon thread pool
//! 2. **Async tests**: Run concurrently using asyncio.gather()
//!
//! The design is future-proof for Python 3.14t (free-threaded Python) where
//! threads can truly execute Python code in parallel without the GIL.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, RwLock};

use indexmap::IndexMap;
use pyo3::prelude::*;

use crate::model::{FixtureScope, PyTestResult, RunConfiguration, TestCase};

/// Thread-safe fixture cache that can be shared across worker threads.
///
/// Uses RwLock for efficient concurrent reads and exclusive writes.
/// Reference counting tracks how many tests are using each cached value.
#[derive(Debug)]
pub struct ThreadSafeFixtureCache {
    cache: RwLock<IndexMap<String, CachedFixture>>,
}

/// A cached fixture value with reference counting for teardown timing.
#[derive(Debug)]
struct CachedFixture {
    /// The actual Python fixture value.
    value: Py<PyAny>,
    /// Number of tests currently using this fixture.
    ref_count: AtomicUsize,
}

impl CachedFixture {
    fn new(value: Py<PyAny>) -> Self {
        Self {
            value,
            ref_count: AtomicUsize::new(1),
        }
    }

    fn acquire(&self) {
        self.ref_count.fetch_add(1, Ordering::SeqCst);
    }

    fn release(&self) -> usize {
        self.ref_count
            .fetch_sub(1, Ordering::SeqCst)
            .saturating_sub(1)
    }

    fn count(&self) -> usize {
        self.ref_count.load(Ordering::SeqCst)
    }
}

impl ThreadSafeFixtureCache {
    pub fn new() -> Self {
        Self {
            cache: RwLock::new(IndexMap::new()),
        }
    }

    /// Get a cached fixture value, incrementing its reference count.
    pub fn get(&self, key: &str, py: Python<'_>) -> Option<Py<PyAny>> {
        let cache = self.cache.read().expect("cache lock poisoned");
        cache.get(key).map(|cached| {
            cached.acquire();
            cached.value.clone_ref(py)
        })
    }

    /// Check if a fixture is cached without incrementing reference count.
    pub fn contains(&self, key: &str) -> bool {
        let cache = self.cache.read().expect("cache lock poisoned");
        cache.contains_key(key)
    }

    /// Insert a fixture into the cache with initial reference count of 1.
    pub fn insert(&self, key: String, value: Py<PyAny>) {
        let mut cache = self.cache.write().expect("cache lock poisoned");
        cache.insert(key, CachedFixture::new(value));
    }

    /// Release a reference to a cached fixture.
    /// Returns true if this was the last reference (fixture can be torn down).
    pub fn release(&self, key: &str) -> bool {
        let cache = self.cache.read().expect("cache lock poisoned");
        if let Some(cached) = cache.get(key) {
            cached.release() == 0
        } else {
            false
        }
    }

    /// Get current reference count for a fixture.
    #[allow(dead_code)]
    pub fn ref_count(&self, key: &str) -> usize {
        let cache = self.cache.read().expect("cache lock poisoned");
        cache.get(key).map(|c| c.count()).unwrap_or(0)
    }

    /// Clear the cache.
    pub fn clear(&self) {
        let mut cache = self.cache.write().expect("cache lock poisoned");
        cache.clear();
    }

    /// Remove a specific fixture from the cache.
    pub fn remove(&self, key: &str) -> Option<Py<PyAny>> {
        let mut cache = self.cache.write().expect("cache lock poisoned");
        cache.shift_remove(key).map(|cached| cached.value)
    }

    /// Get all keys currently in the cache.
    pub fn keys(&self) -> Vec<String> {
        let cache = self.cache.read().expect("cache lock poisoned");
        cache.keys().cloned().collect()
    }
}

impl Default for ThreadSafeFixtureCache {
    fn default() -> Self {
        Self::new()
    }
}

/// Thread-safe teardown collector that stores generators for cleanup.
pub struct ThreadSafeTeardownCollector {
    session: Mutex<Vec<TeardownEntry>>,
    package: Mutex<Vec<TeardownEntry>>,
    module: Mutex<Vec<TeardownEntry>>,
    class: Mutex<Vec<TeardownEntry>>,
}

/// Entry in the teardown collector with reference counting.
struct TeardownEntry {
    generator: Py<PyAny>,
    cache_key: String,
    /// Number of tests that need this fixture to be kept alive.
    ref_count: AtomicUsize,
}

impl TeardownEntry {
    fn new(generator: Py<PyAny>, cache_key: String) -> Self {
        Self {
            generator,
            cache_key,
            ref_count: AtomicUsize::new(1),
        }
    }

    fn acquire(&self) {
        self.ref_count.fetch_add(1, Ordering::SeqCst);
    }

    fn release(&self) -> usize {
        self.ref_count
            .fetch_sub(1, Ordering::SeqCst)
            .saturating_sub(1)
    }
}

impl ThreadSafeTeardownCollector {
    pub fn new() -> Self {
        Self {
            session: Mutex::new(Vec::new()),
            package: Mutex::new(Vec::new()),
            module: Mutex::new(Vec::new()),
            class: Mutex::new(Vec::new()),
        }
    }

    /// Add a generator to the appropriate scope's teardown list.
    pub fn add(&self, scope: FixtureScope, generator: Py<PyAny>, cache_key: String) {
        let entry = TeardownEntry::new(generator, cache_key);
        match scope {
            FixtureScope::Session => {
                self.session.lock().expect("lock poisoned").push(entry);
            }
            FixtureScope::Package => {
                self.package.lock().expect("lock poisoned").push(entry);
            }
            FixtureScope::Module => {
                self.module.lock().expect("lock poisoned").push(entry);
            }
            FixtureScope::Class => {
                self.class.lock().expect("lock poisoned").push(entry);
            }
            FixtureScope::Function => {
                // Function-scoped teardowns are handled per-test, not here
            }
        }
    }

    /// Acquire a reference to a teardown entry by cache key.
    pub fn acquire(&self, scope: FixtureScope, cache_key: &str) {
        let list = match scope {
            FixtureScope::Session => &self.session,
            FixtureScope::Package => &self.package,
            FixtureScope::Module => &self.module,
            FixtureScope::Class => &self.class,
            FixtureScope::Function => return,
        };
        let entries = list.lock().expect("lock poisoned");
        for entry in entries.iter() {
            if entry.cache_key == cache_key {
                entry.acquire();
                break;
            }
        }
    }

    /// Release a reference and check if teardown should occur.
    /// Returns true if this was the last reference.
    pub fn release(&self, scope: FixtureScope, cache_key: &str) -> bool {
        let list = match scope {
            FixtureScope::Session => &self.session,
            FixtureScope::Package => &self.package,
            FixtureScope::Module => &self.module,
            FixtureScope::Class => &self.class,
            FixtureScope::Function => return false,
        };
        let entries = list.lock().expect("lock poisoned");
        for entry in entries.iter() {
            if entry.cache_key == cache_key && entry.release() == 0 {
                return true;
            }
        }
        false
    }

    /// Drain all generators from a scope for finalization.
    pub fn drain(&self, scope: FixtureScope) -> Vec<Py<PyAny>> {
        let list = match scope {
            FixtureScope::Session => &self.session,
            FixtureScope::Package => &self.package,
            FixtureScope::Module => &self.module,
            FixtureScope::Class => &self.class,
            FixtureScope::Function => return Vec::new(),
        };
        let mut entries = list.lock().expect("lock poisoned");
        entries.drain(..).map(|entry| entry.generator).collect()
    }
}

impl Default for ThreadSafeTeardownCollector {
    fn default() -> Self {
        Self::new()
    }
}

/// Thread-safe fixture context for parallel test execution.
pub struct ParallelFixtureContext {
    pub session_cache: Arc<ThreadSafeFixtureCache>,
    pub package_cache: Arc<ThreadSafeFixtureCache>,
    pub module_cache: Arc<ThreadSafeFixtureCache>,
    pub class_cache: Arc<ThreadSafeFixtureCache>,
    pub teardowns: Arc<ThreadSafeTeardownCollector>,
    /// Track the current package to detect package transitions
    pub current_package: Mutex<Option<String>>,
    /// Event loops for different scopes (for async fixtures)
    /// These are protected by Mutex since they need exclusive access
    pub session_event_loop: Mutex<Option<Py<PyAny>>>,
    pub package_event_loop: Mutex<Option<Py<PyAny>>>,
    pub module_event_loop: Mutex<Option<Py<PyAny>>>,
    pub class_event_loop: Mutex<Option<Py<PyAny>>>,
}

impl ParallelFixtureContext {
    pub fn new() -> Self {
        Self {
            session_cache: Arc::new(ThreadSafeFixtureCache::new()),
            package_cache: Arc::new(ThreadSafeFixtureCache::new()),
            module_cache: Arc::new(ThreadSafeFixtureCache::new()),
            class_cache: Arc::new(ThreadSafeFixtureCache::new()),
            teardowns: Arc::new(ThreadSafeTeardownCollector::new()),
            current_package: Mutex::new(None),
            session_event_loop: Mutex::new(None),
            package_event_loop: Mutex::new(None),
            module_event_loop: Mutex::new(None),
            class_event_loop: Mutex::new(None),
        }
    }

    /// Clear package-scoped caches and teardowns.
    pub fn clear_package_scope(&self, py: Python<'_>) {
        let generators = self.teardowns.drain(FixtureScope::Package);
        let event_loop = self
            .package_event_loop
            .lock()
            .expect("lock poisoned")
            .take();
        finalize_generators_parallel(py, generators, event_loop.as_ref());
        self.package_cache.clear();
    }

    /// Clear module-scoped caches and teardowns.
    pub fn clear_module_scope(&self, py: Python<'_>) {
        let generators = self.teardowns.drain(FixtureScope::Module);
        let event_loop = self.module_event_loop.lock().expect("lock poisoned").take();
        finalize_generators_parallel(py, generators, event_loop.as_ref());
        self.module_cache.clear();
    }

    /// Clear class-scoped caches and teardowns.
    pub fn clear_class_scope(&self, py: Python<'_>) {
        let generators = self.teardowns.drain(FixtureScope::Class);
        let event_loop = self.class_event_loop.lock().expect("lock poisoned").take();
        finalize_generators_parallel(py, generators, event_loop.as_ref());
        self.class_cache.clear();
    }

    /// Clear session-scoped caches and teardowns.
    pub fn clear_session_scope(&self, py: Python<'_>) {
        let generators = self.teardowns.drain(FixtureScope::Session);
        let event_loop = self
            .session_event_loop
            .lock()
            .expect("lock poisoned")
            .take();
        finalize_generators_parallel(py, generators, event_loop.as_ref());
        self.session_cache.clear();
    }
}

impl Default for ParallelFixtureContext {
    fn default() -> Self {
        Self::new()
    }
}

/// Finalize generator fixtures by running their teardown code.
/// Thread-safe version that takes ownership of generators.
fn finalize_generators_parallel(
    py: Python<'_>,
    generators: Vec<Py<PyAny>>,
    event_loop: Option<&Py<PyAny>>,
) {
    // Process generators in reverse order (LIFO) to match pytest behavior
    for generator in generators.into_iter().rev() {
        let gen_bound = generator.bind(py);

        // Check if this is an async generator
        let is_async_gen = gen_bound.hasattr("__anext__").unwrap_or(false);

        let result = if is_async_gen {
            // For async generators, use anext() with the scoped event loop
            match py.import("builtins").and_then(|builtins| {
                let anext = builtins.getattr("anext")?;
                let coro = anext.call1((gen_bound,))?;

                if let Some(loop_obj) = event_loop {
                    loop_obj
                        .bind(py)
                        .call_method1("run_until_complete", (coro,))
                } else {
                    let asyncio = py.import("asyncio")?;
                    asyncio.call_method1("run", (coro,))
                }
            }) {
                Ok(_) => Ok(()),
                Err(err) => Err(err),
            }
        } else {
            gen_bound.call_method0("__next__").map(|_| ())
        };

        if let Err(err) = result {
            if !err.is_instance_of::<pyo3::exceptions::PyStopIteration>(py)
                && !err.is_instance_of::<pyo3::exceptions::PyStopAsyncIteration>(py)
            {
                eprintln!("Warning: Error during fixture teardown: {}", err);
            }
        }
    }
}

/// Close an event loop if it exists, properly cleaning up pending tasks.
fn close_event_loop_parallel(py: Python<'_>, event_loop: Option<Py<PyAny>>) {
    if let Some(loop_obj) = event_loop {
        let loop_bound = loop_obj.bind(py);

        let is_closed = loop_bound
            .call_method0("is_closed")
            .and_then(|v| v.extract::<bool>())
            .unwrap_or(true);

        if !is_closed {
            if let Ok(asyncio) = py.import("asyncio") {
                if let Ok(tasks) = asyncio.call_method1("all_tasks", (loop_bound,)) {
                    if let Ok(task_list) = tasks.extract::<Vec<Py<PyAny>>>() {
                        for task in task_list {
                            let _ = task.bind(py).call_method0("cancel");
                        }
                    }
                }
            }
            let _ = loop_bound.call_method0("close");
        }
    }
}

/// Result of a single test execution in parallel mode.
pub struct ParallelTestResult {
    pub result: PyTestResult,
    pub function_teardowns: Vec<Py<PyAny>>,
    pub function_event_loop: Option<Py<PyAny>>,
}

/// Test classification for routing to appropriate execution path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TestKind {
    /// Synchronous test (no async/await)
    Sync,
    /// Asynchronous test (async def)
    Async,
}

/// Classify a test as sync or async based on its callable.
pub fn classify_test(py: Python<'_>, test_case: &TestCase) -> TestKind {
    let inspect = match py.import("inspect") {
        Ok(m) => m,
        Err(_) => return TestKind::Sync,
    };

    let callable = test_case.callable.bind(py);

    // Check if it's a coroutine function (async def)
    let is_async = inspect
        .call_method1("iscoroutinefunction", (callable,))
        .and_then(|r| r.extract::<bool>())
        .unwrap_or(false);

    if is_async {
        TestKind::Async
    } else {
        TestKind::Sync
    }
}

/// Partition tests into sync and async categories for dual-path execution.
pub fn partition_tests<'a>(
    py: Python<'_>,
    tests: &[&'a TestCase],
) -> (Vec<&'a TestCase>, Vec<&'a TestCase>) {
    let mut sync_tests = Vec::new();
    let mut async_tests = Vec::new();

    for test in tests {
        match classify_test(py, test) {
            TestKind::Sync => sync_tests.push(*test),
            TestKind::Async => async_tests.push(*test),
        }
    }

    (sync_tests, async_tests)
}

/// Configuration for parallel test execution.
#[derive(Clone)]
pub struct ParallelConfig {
    /// Maximum number of concurrent tests
    pub max_workers: usize,
    /// Whether to capture output (needs per-test isolation)
    pub capture_output: bool,
}

impl ParallelConfig {
    pub fn new(worker_count: usize, capture_output: bool) -> Self {
        Self {
            max_workers: worker_count,
            capture_output,
        }
    }

    pub fn from_run_config(config: &RunConfiguration) -> Self {
        Self {
            max_workers: config.worker_count,
            capture_output: config.capture_output,
        }
    }
}

/// Concurrency semaphore to limit the number of concurrent tests.
pub struct ConcurrencySemaphore {
    current: AtomicUsize,
    max: usize,
}

impl ConcurrencySemaphore {
    pub fn new(max: usize) -> Self {
        Self {
            current: AtomicUsize::new(0),
            max,
        }
    }

    /// Try to acquire a permit. Returns true if acquired.
    pub fn try_acquire(&self) -> bool {
        loop {
            let current = self.current.load(Ordering::SeqCst);
            if current >= self.max {
                return false;
            }
            if self
                .current
                .compare_exchange(current, current + 1, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                return true;
            }
        }
    }

    /// Acquire a permit, blocking if necessary.
    pub fn acquire(&self) {
        loop {
            if self.try_acquire() {
                return;
            }
            std::hint::spin_loop();
        }
    }

    /// Release a permit.
    pub fn release(&self) {
        self.current.fetch_sub(1, Ordering::SeqCst);
    }

    /// Get the current count.
    #[allow(dead_code)]
    pub fn current(&self) -> usize {
        self.current.load(Ordering::SeqCst)
    }
}

/// RAII guard for semaphore permit.
pub struct SemaphoreGuard<'a> {
    semaphore: &'a ConcurrencySemaphore,
}

impl<'a> SemaphoreGuard<'a> {
    pub fn acquire(semaphore: &'a ConcurrencySemaphore) -> Self {
        semaphore.acquire();
        Self { semaphore }
    }
}

impl<'a> Drop for SemaphoreGuard<'a> {
    fn drop(&mut self) {
        self.semaphore.release();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_thread_safe_cache_basic() {
        Python::attach(|py| {
            let cache = ThreadSafeFixtureCache::new();
            let value = py.None();

            cache.insert("test".to_string(), value.clone_ref(py));
            assert!(cache.contains("test"));

            let retrieved = cache.get("test", py);
            assert!(retrieved.is_some());
        });
    }

    #[test]
    fn test_thread_safe_cache_ref_counting() {
        Python::attach(|py| {
            let cache = ThreadSafeFixtureCache::new();
            let value = py.None();

            cache.insert("test".to_string(), value.clone_ref(py));

            // Initial ref count is 1
            assert_eq!(cache.ref_count("test"), 1);

            // Get increments ref count
            let _ = cache.get("test", py);
            assert_eq!(cache.ref_count("test"), 2);

            // Release decrements ref count
            cache.release("test");
            assert_eq!(cache.ref_count("test"), 1);
        });
    }

    #[test]
    fn test_concurrency_semaphore() {
        let sem = ConcurrencySemaphore::new(2);

        assert!(sem.try_acquire());
        assert!(sem.try_acquire());
        assert!(!sem.try_acquire()); // Max reached

        sem.release();
        assert!(sem.try_acquire());
    }
}
