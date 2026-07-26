//! v2 configuration subsystem: rootdir resolution + ini semantics.
//!
//! **Every rule in this module was extracted from the installed pytest source**
//! (`.venv/Lib/site-packages/_pytest/`, pytest 8.4.2) — pytest is the oracle, not
//! memory.  Each constant and branch carries a citation of the file and function it
//! came from so that Task 5's PyO3 differential harness has something to argue with.

use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Extracted constants
// ---------------------------------------------------------------------------

/// Candidate config file names, in the exact precedence order pytest tries them
/// *within a single directory*.
///
/// Source: `_pytest/config/findpaths.py::locate_config`, local `config_names` list.
/// Note `.pytest.ini` (hidden variant) **does** participate, ranked second.
pub const CONFIG_NAMES: &[&str] = &[
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
];

/// Source: `_pytest/python.py::pytest_addoption` — `addini("python_files", type="args", default=["test_*.py", "*_test.py"])`.
pub const DEFAULT_PYTHON_FILES: &[&str] = &["test_*.py", "*_test.py"];

/// Source: `_pytest/python.py::pytest_addoption` — `addini("python_classes", type="args", default=["Test"])`.
pub const DEFAULT_PYTHON_CLASSES: &[&str] = &["Test"];

/// Source: `_pytest/python.py::pytest_addoption` — `addini("python_functions", type="args", default=["test"])`.
///
/// This is the Phase 0 lesson made load-bearing: the default is the bare prefix
/// `test`, **not** `test_*`, so `testfoo` is collected (corpus case
/// `collection/naming-testfoo`).
pub const DEFAULT_PYTHON_FUNCTIONS: &[&str] = &["test"];

/// Source: `_pytest/main.py::pytest_addoption` — `addini("norecursedirs", type="args", default=[...])`.
pub const DEFAULT_NORECURSEDIRS: &[&str] = &[
    "*.egg",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
];

/// Source: `_pytest/config/findpaths.py::CFG_PYTEST_SECTION`.
const CFG_PYTEST_SECTION: &str =
    "[pytest] section in setup.cfg files is no longer supported, change to [tool:pytest] instead.";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Errors surfaced while resolving configuration.
///
/// pytest raises `UsageError` for all of these (see `_pytest/config/findpaths.py`);
/// we keep the originating path so callers can render a pytest-shaped message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    /// A config file could not be read (missing/permission/non-UTF-8).
    Io { path: PathBuf, message: String },
    /// A config file was malformed. Mirrors `iniconfig.ParseError` / `TOMLDecodeError`.
    Parse { path: PathBuf, message: String },
    /// A usage-level rejection, e.g. `[pytest]` in `setup.cfg`.
    Usage { path: PathBuf, message: String },
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io { path, message } => write!(f, "{}: {}", path.display(), message),
            ConfigError::Parse { path, message } => write!(f, "{}: {}", path.display(), message),
            ConfigError::Usage { message, .. } => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ConfigError {}

/// The resolved rootdir plus the ini values v2 collection and the CLI need.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedConfig {
    pub rootdir: PathBuf,
    pub config_file: Option<PathBuf>,
    pub testpaths: Vec<String>,
    pub python_files: Vec<String>,
    pub python_classes: Vec<String>,
    pub python_functions: Vec<String>,
    pub norecursedirs: Vec<String>,
    pub addopts: Vec<String>,
    pub markers: Vec<String>,
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Resolve rootdir + ini exactly as pytest does, from CLI path args.
///
/// Port of `_pytest/config/findpaths.py::determine_setup` for the case
/// `inifile is None and rootdir_cmd_arg is None` (`-c` / `--rootdir` are Phase 1c
/// concerns and are not modelled here).
///
/// `invocation_dir` stands in for `Config.invocation_params.dir`; it is also used to
/// absolutise relative `args`, which pytest does via `os.path.abspath` against the
/// process CWD (the same directory in practice).
pub fn resolve_config(
    invocation_dir: &Path,
    args: &[PathBuf],
) -> Result<ResolvedConfig, ConfigError> {
    let dirs = get_dirs_from_args(invocation_dir, args);
    let ancestor = get_common_ancestor(invocation_dir, &dirs);

    // findpaths.py::determine_setup — `locate_config(invocation_dir, [ancestor])`.
    let (mut rootdir, mut inipath, mut inicfg) =
        locate_config(invocation_dir, std::slice::from_ref(&ancestor))?;

    if rootdir.is_none() {
        // `for possible_rootdir in (ancestor, *ancestor.parents): if setup.py -> break`
        let via_setup_py = ancestor
            .ancestors()
            .find(|d| d.join("setup.py").is_file())
            .map(Path::to_path_buf);
        match via_setup_py {
            Some(dir) => rootdir = Some(dir),
            // The `else:` clause of the for/else — only reached when no setup.py exists.
            None => {
                if dirs.len() != 1 || dirs[0] != ancestor {
                    let located = locate_config(invocation_dir, &dirs)?;
                    rootdir = located.0;
                    inipath = located.1;
                    inicfg = located.2;
                }
                if rootdir.is_none() {
                    let mut rd = get_common_ancestor(
                        invocation_dir,
                        &[invocation_dir.to_path_buf(), ancestor.clone()],
                    );
                    if is_fs_root(&rd) {
                        rd = ancestor.clone();
                    }
                    rootdir = Some(rd);
                }
            }
        }
    }

    // `assert rootdir is not None` in determine_setup.
    let rootdir = rootdir.expect("determine_setup always produces a rootdir");
    let err_path = inipath
        .clone()
        .unwrap_or_else(|| invocation_dir.to_path_buf());

    Ok(ResolvedConfig {
        rootdir,
        // All of the following are `type="args"` inis: see the citations on the
        // DEFAULT_* constants, plus `_pytest/main.py` (testpaths) and
        // `_pytest/config/__init__.py::Config._initini` L1256 (addopts, no explicit
        // default => `config/argparsing.py::get_ini_default_for_type` L238-258 => `[]`).
        testpaths: getini_args(&inicfg, "testpaths", &[], &err_path)?,
        python_files: getini_args(&inicfg, "python_files", DEFAULT_PYTHON_FILES, &err_path)?,
        python_classes: getini_args(&inicfg, "python_classes", DEFAULT_PYTHON_CLASSES, &err_path)?,
        python_functions: getini_args(
            &inicfg,
            "python_functions",
            DEFAULT_PYTHON_FUNCTIONS,
            &err_path,
        )?,
        norecursedirs: getini_args(&inicfg, "norecursedirs", DEFAULT_NORECURSEDIRS, &err_path)?,
        addopts: getini_args(&inicfg, "addopts", &[], &err_path)?,
        // `_pytest/mark/__init__.py::pytest_addoption` — addini("markers", ..., "linelist").
        markers: getini_linelist(&inicfg, "markers"),
        config_file: inipath,
    })
}

/// pytest's name-matching rule for `python_classes` / `python_functions`.
///
/// Port of `_pytest/python.py::PyCollector._matches_prefix_or_glob_option`:
///
/// ```text
/// for option in self.config.getini(option_name):
///     if name.startswith(option):
///         return True
///     elif ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(name, option):
///         return True
/// return False
/// ```
///
/// The prefix test comes first and is always case-sensitive, which is why the default
/// `python_functions = ["test"]` collects `testfoo` (corpus `collection/naming-testfoo`).
pub fn matches_name_pattern(name: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|option| {
        name.starts_with(option.as_str())
            || (option.contains(['*', '?', '['])) && fnmatch(name, option)
    })
}

/// File-pattern matching for `python_files` (fnmatch on the basename).
///
/// Port of `_pytest/python.py::path_matches_patterns` -> `_pytest/pathlib.py::fnmatch_ex`
/// for the (overwhelmingly common) case of a separator-free pattern, where `fnmatch_ex`
/// reduces to `fnmatch.fnmatch(path.name, pattern)`.
///
/// Limitation: `fnmatch_ex` matches a pattern that *contains* a path separator against
/// the whole path instead of the basename (and, on Windows, rewrites a posix-separator
/// pattern to backslashes first). This helper is basename-only by contract; Phase 1b will
/// need a full-path `fnmatch_ex` variant alongside it to cover separator-bearing
/// `python_files` / `norecursedirs` patterns.
pub fn matches_file_pattern(basename: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|pattern| fnmatch(basename, pattern))
}

// ---------------------------------------------------------------------------
// fnmatch — a port of CPython's `fnmatch`, which pytest calls directly
// ---------------------------------------------------------------------------

/// Port of `os.path.normcase`, applied by `fnmatch.fnmatch` to *both* operands.
///
/// On Windows this makes pytest's glob matching case-insensitive; elsewhere
/// `posixpath.normcase` is the identity and matching stays case-sensitive. We reproduce
/// the platform split rather than picking one, because pytest is the oracle on each
/// platform.
///
/// **The real implementation is not `str.lower()`.** `Lib/ntpath.py::normcase` (L50-67)
/// is:
///
/// ```python
/// return _LCMapStringEx(_LOCALE_NAME_INVARIANT, _LCMAP_LOWERCASE, s.replace('/', '\\'))
/// ```
///
/// i.e. the Win32 invariant-locale case mapping. The `s.replace('/','\\').lower()` form
/// at L69-77 is only the `except ImportError` fallback for builds without `_winapi`, and
/// is never what pytest gets on a normal CPython.
///
/// **Accepted approximation:** Rust's `str::to_lowercase` implements Unicode full
/// lowercasing, which matches `str.lower()`, not `LCMapStringEx`. Verified divergences
/// (against the installed CPython 3.14.2):
///
/// | input | `normcase` | `to_lowercase` |
/// |---|---|---|
/// | U+212A KELVIN SIGN | U+212A (unchanged) | `k` |
/// | U+1E9E CAPITAL SHARP S | U+1E9E (unchanged) | U+00DF `ß` |
/// | U+0130 CAPITAL I WITH DOT | U+0130 (unchanged) | `i` + U+0307 (**grows**) |
///
/// `LCMapStringEx` leaves all three alone; `to_lowercase` folds them. So we are *more*
/// permissive than pytest on those codepoints. On ASCII — every realistic module, class
/// and function name — the two are identical, so the blast radius is ~zero. The
/// divergence is pinned by `normcase_kelvin_sign_is_an_accepted_divergence` so Task 5's
/// oracle diff sees a documented waiver rather than a surprise.
#[cfg(windows)]
fn normcase(s: &str) -> std::borrow::Cow<'_, str> {
    std::borrow::Cow::Owned(s.replace('/', "\\").to_lowercase())
}

#[cfg(not(windows))]
fn normcase(s: &str) -> std::borrow::Cow<'_, str> {
    std::borrow::Cow::Borrowed(s)
}

/// `fnmatch.fnmatch(name, pattern)`.
fn fnmatch(name: &str, pattern: &str) -> bool {
    fnmatchcase(&normcase(name), &normcase(pattern))
}

/// `fnmatch.fnmatchcase` — a whole-string (`\Z`-anchored) match with `*`, `?` and `[...]`.
///
/// Deliberately *not* built on `globset`: `globset` understands `{a,b}` alternation and
/// `**`, neither of which Python's `fnmatch` has. `norecursedirs`' default contains the
/// literal `{arch}`, which globset would silently reinterpret.
fn fnmatchcase(name: &str, pattern: &str) -> bool {
    let n: Vec<char> = name.chars().collect();
    let p: Vec<char> = pattern.chars().collect();
    let mut ni = 0usize;
    let mut pi = 0usize;
    let mut star: Option<usize> = None;
    let mut star_ni = 0usize;

    loop {
        if pi < p.len() && p[pi] == '*' {
            star = Some(pi);
            star_ni = ni;
            pi += 1;
            continue;
        }
        if ni < n.len() {
            if let Some(next_pi) = fnmatch_one(&p, pi, n[ni]) {
                pi = next_pi;
                ni += 1;
                continue;
            }
        }
        if ni >= n.len() && pi >= p.len() {
            return true;
        }
        match star {
            Some(sp) if star_ni < n.len() => {
                star_ni += 1;
                ni = star_ni;
                pi = sp + 1;
            }
            _ => return false,
        }
    }
}

/// Match a single (non-`*`) pattern atom at `pi` against `ch`; returns the next pattern index.
fn fnmatch_one(p: &[char], pi: usize, ch: char) -> Option<usize> {
    match *p.get(pi)? {
        '?' => Some(pi + 1),
        '[' => match fnmatch_class_bounds(p, pi) {
            Some((start, close)) => {
                if fnmatch_class_matches(&p[start..close], ch) {
                    Some(close + 1)
                } else {
                    None
                }
            }
            // `fnmatch.translate`: an unterminated "[" is emitted as a literal "[".
            None => (ch == '[').then_some(pi + 1),
        },
        literal => (ch == literal).then_some(pi + 1),
    }
}

/// Locate a character class's content span, mirroring `fnmatch.translate`'s scan:
/// an optional leading `!`, then an optional literal `]`, then up to the closing `]`.
fn fnmatch_class_bounds(p: &[char], open_idx: usize) -> Option<(usize, usize)> {
    let start = open_idx + 1;
    let mut j = start;
    if j < p.len() && p[j] == '!' {
        j += 1;
    }
    if j < p.len() && p[j] == ']' {
        j += 1;
    }
    while j < p.len() && p[j] != ']' {
        j += 1;
    }
    (j < p.len()).then_some((start, j))
}

/// Evaluate a character class body (`!` negation, `a-z` ranges, literals otherwise).
///
/// Limitation: CPython's `translate` hands the class body to `re`, so exotic bodies
/// (`--` set-difference escapes, backslashes) follow `re` rules. Test-name/file-name
/// patterns in the wild use plain members and ranges, which this reproduces.
fn fnmatch_class_matches(content: &[char], ch: char) -> bool {
    let (negated, body) = match content.first() {
        Some('!') => (true, &content[1..]),
        _ => (false, content),
    };
    let mut hit = false;
    let mut k = 0usize;
    while k < body.len() {
        if k + 2 < body.len() && body[k + 1] == '-' {
            if body[k] <= ch && ch <= body[k + 2] {
                hit = true;
            }
            k += 3;
        } else {
            if body[k] == ch {
                hit = true;
            }
            k += 1;
        }
    }
    hit != negated
}

// ---------------------------------------------------------------------------
// shlex — `type="args"` ini values are split by `shlex.split` (POSIX mode)
// ---------------------------------------------------------------------------

/// `shlex.whitespace` is exactly `" \t\r\n"` (CPython `shlex.shlex.__init__`).
fn is_shlex_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}

/// Port of `shlex.split(value)` (i.e. `posix=True, comments=False`).
///
/// Note `comments=False`: `#` is an ordinary character in ini `args` values.
/// Limitation: `punctuation_chars` mode is not modelled (`shlex.split` never enables it).
fn shlex_split(input: &str, path: &Path) -> Result<Vec<String>, ConfigError> {
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum St {
        Idle,
        Word,
        Single,
        Double,
    }

    let mut out: Vec<String> = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut st = St::Idle;
    let mut escape_from: Option<St> = None;

    for ch in input.chars() {
        if let Some(prev) = escape_from.take() {
            quoted = true;
            // shlex: "In posix shells, only the quote itself or the escape character
            // may be escaped by it." Otherwise the backslash is retained.
            if prev == St::Double && ch != '"' && ch != '\\' {
                token.push('\\');
            }
            token.push(ch);
            st = prev;
            continue;
        }
        match st {
            St::Idle => match ch {
                c if is_shlex_space(c) => {}
                '\'' => {
                    quoted = true;
                    st = St::Single;
                }
                '"' => {
                    quoted = true;
                    st = St::Double;
                }
                '\\' => {
                    st = St::Word;
                    escape_from = Some(St::Word);
                }
                c => {
                    token.push(c);
                    st = St::Word;
                }
            },
            St::Word => match ch {
                c if is_shlex_space(c) => {
                    out.push(std::mem::take(&mut token));
                    quoted = false;
                    st = St::Idle;
                }
                '\'' => {
                    quoted = true;
                    st = St::Single;
                }
                '"' => {
                    quoted = true;
                    st = St::Double;
                }
                '\\' => escape_from = Some(St::Word),
                c => token.push(c),
            },
            St::Single => {
                if ch == '\'' {
                    st = St::Word;
                } else {
                    token.push(ch);
                }
            }
            St::Double => {
                if ch == '"' {
                    st = St::Word;
                } else if ch == '\\' {
                    escape_from = Some(St::Double);
                } else {
                    token.push(ch);
                }
            }
        }
    }

    if escape_from.is_some() {
        return Err(ConfigError::Parse {
            path: path.to_path_buf(),
            message: "No escaped character".to_string(),
        });
    }
    if st == St::Single || st == St::Double {
        return Err(ConfigError::Parse {
            path: path.to_path_buf(),
            message: "No closing quotation".to_string(),
        });
    }
    if st == St::Word && (!token.is_empty() || quoted) {
        out.push(token);
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// ini parsing — a port of `iniconfig` as pytest invokes it
// ---------------------------------------------------------------------------

/// `_pytest/config/findpaths.py::_parse_ini_config` calls `iniconfig.IniConfig(str(path))`,
/// i.e. the *constructor* path (iniconfig 2.3.0 `__init__.py` L110-119), which pins
/// `strip_inline_comments=False` and `strip_section_whitespace=False` for backwards
/// compatibility. The newer `IniConfig.parse()` classmethod defaults
/// `strip_inline_comments=True` — that is **not** what pytest gets.
///
/// Limitations vs. `iniconfig`:
/// * Line splitting is `\n`-based (with a `\r` trim) rather than Python's
///   `str.splitlines`, so exotic line terminators (`\v`, `\f`, `\x85`, ` `) are not
///   treated as line breaks.
/// * Files must be valid UTF-8 (iniconfig also decodes as UTF-8, but replaces nothing).
#[derive(Debug, Default)]
struct IniFile {
    sections: Vec<(String, Vec<(String, String)>)>,
}

impl IniFile {
    fn section(&self, name: &str) -> Option<&Vec<(String, String)>> {
        self.sections
            .iter()
            .find(|(s, _)| s == name)
            .map(|(_, items)| items)
    }
}

enum IniLine {
    Blank,
    Section(String),
    Kv(String, String),
    Continuation(String),
}

/// Port of `iniconfig._parse._parseline`.
fn parse_ini_line(path: &Path, raw: &str, lineno: usize) -> Result<IniLine, ConfigError> {
    let parse_err = |msg: &str| ConfigError::Parse {
        path: path.to_path_buf(),
        message: format!("line {}: {msg}", lineno + 1),
    };

    // `if iscommentline(line): line = "" else: line = line.rstrip()`
    let line = if raw.trim_start().starts_with(['#', ';']) {
        ""
    } else {
        raw.trim_end()
    };
    let Some(first) = line.chars().next() else {
        return Ok(IniLine::Blank);
    };

    if first == '[' {
        // `for c in COMMENTCHARS: line = line.split(c)[0].rstrip()`
        let mut cut = line;
        for c in ['#', ';'] {
            cut = cut.split(c).next().unwrap_or("").trim_end();
        }
        if cut.ends_with(']') {
            return Ok(IniLine::Section(cut[1..cut.len() - 1].to_string()));
        }
        // `return None, realline.strip()` — treated as a value continuation.
        return Ok(IniLine::Continuation(line.trim().to_string()));
    }

    if !first.is_whitespace() {
        // `name, value = line.split("=", 1)`; on failure, or if ":" is in `name`,
        // retry with `line.split(":", 1)`; if that fails too it is a parse error.
        let split_at_eq = line.find('=').filter(|&i| !line[..i].contains(':'));
        let idx = match split_at_eq.or_else(|| line.find(':')) {
            Some(i) => i,
            None => return Err(parse_err(&format!("unexpected line: {line:?}"))),
        };
        let (name, value) = line.split_at(idx);
        return Ok(IniLine::Kv(
            name.trim().to_string(),
            value[1..].trim().to_string(),
        ));
    }

    Ok(IniLine::Continuation(line.trim().to_string()))
}

/// Port of `iniconfig._parse.parse_lines` + `parse_ini_data`.
fn parse_ini(path: &Path, data: &str) -> Result<IniFile, ConfigError> {
    enum Last {
        Nothing,
        Section,
        Kv(usize, usize),
    }

    let mut file = IniFile::default();
    let mut current: Option<usize> = None;
    let mut last = Last::Nothing;

    for (lineno, raw) in data.split('\n').enumerate() {
        let raw = raw.strip_suffix('\r').unwrap_or(raw);
        let parse_err = |msg: String| ConfigError::Parse {
            path: path.to_path_buf(),
            message: format!("line {}: {msg}", lineno + 1),
        };
        match parse_ini_line(path, raw, lineno)? {
            IniLine::Blank => {}
            IniLine::Section(name) => {
                if name.is_empty() {
                    return Err(parse_err("empty section name".to_string()));
                }
                if file.sections.iter().any(|(s, _)| *s == name) {
                    return Err(parse_err(format!("duplicate section {name:?}")));
                }
                file.sections.push((name, Vec::new()));
                current = Some(file.sections.len() - 1);
                last = Last::Section;
            }
            IniLine::Kv(key, value) => {
                let Some(ci) = current else {
                    return Err(parse_err("no section header defined".to_string()));
                };
                if file.sections[ci].1.iter().any(|(k, _)| *k == key) {
                    return Err(parse_err(format!("duplicate name {key:?}")));
                }
                file.sections[ci].1.push((key, value));
                let ki = file.sections[ci].1.len() - 1;
                last = Last::Kv(ci, ki);
            }
            IniLine::Continuation(text) => {
                let Last::Kv(ci, ki) = last else {
                    return Err(parse_err("unexpected value continuation".to_string()));
                };
                let value = &mut file.sections[ci].1[ki].1;
                if value.is_empty() {
                    *value = text;
                } else {
                    value.push('\n');
                    value.push_str(&text);
                }
                last = Last::Kv(ci, ki);
            }
        }
    }
    Ok(file)
}

// ---------------------------------------------------------------------------
// Config file loading
// ---------------------------------------------------------------------------

/// An ini value as pytest's `ConfigDict` models it: `str` from ini files, `str | list[str]`
/// from TOML (see the `ConfigDict` TypeAlias comment in `findpaths.py`).
#[derive(Debug, Clone)]
enum IniValue {
    Str(String),
    List(Vec<String>),
}

type ConfigDict = Vec<(String, IniValue)>;

fn lookup<'a>(cfg: &'a ConfigDict, name: &str) -> Option<&'a IniValue> {
    cfg.iter().find(|(k, _)| k == name).map(|(_, v)| v)
}

fn read_file(path: &Path) -> Result<String, ConfigError> {
    std::fs::read_to_string(path).map_err(|e| ConfigError::Io {
        path: path.to_path_buf(),
        message: e.to_string(),
    })
}

fn ini_items_to_dict(items: &[(String, String)]) -> ConfigDict {
    items
        .iter()
        .map(|(k, v)| (k.clone(), IniValue::Str(v.clone())))
        .collect()
}

/// Port of `_pytest/config/findpaths.py::load_config_dict_from_file`.
///
/// Returns `None` when the file exists but carries no pytest configuration, which is
/// what makes `locate_config` keep searching.
fn load_config_dict_from_file(path: &Path) -> Result<Option<ConfigDict>, ConfigError> {
    let suffix = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    let name = path.file_name().and_then(|e| e.to_str()).unwrap_or("");

    match suffix {
        // "Configuration from ini files are obtained from the [pytest] section, if present."
        "ini" => {
            let ini = parse_ini(path, &read_file(path)?)?;
            if let Some(items) = ini.section("pytest") {
                return Ok(Some(ini_items_to_dict(items)));
            }
            // '"pytest.ini" files are always the source of configuration, even if empty.'
            // Keyed on the exact file *name*, so a section-less ".pytest.ini" does NOT
            // qualify and the search continues.
            Ok((name == "pytest.ini").then(Vec::new))
        }
        // "'.cfg' files are considered if they contain a [tool:pytest] section."
        "cfg" => {
            let ini = parse_ini(path, &read_file(path)?)?;
            if let Some(items) = ini.section("tool:pytest") {
                return Ok(Some(ini_items_to_dict(items)));
            }
            if ini.section("pytest").is_some() {
                // `fail(CFG_PYTEST_SECTION.format(filename="setup.cfg"), pytrace=False)`
                return Err(ConfigError::Usage {
                    path: path.to_path_buf(),
                    message: CFG_PYTEST_SECTION.to_string(),
                });
            }
            Ok(None)
        }
        // "'.toml' files are considered if they contain a [tool.pytest.ini_options] table."
        "toml" => {
            let text = read_file(path)?;
            let value: toml::Value =
                text.parse()
                    .map_err(|e: toml::de::Error| ConfigError::Parse {
                        path: path.to_path_buf(),
                        message: e.to_string(),
                    })?;
            let table = value
                .get("tool")
                .and_then(|t| t.get("pytest"))
                .and_then(|p| p.get("ini_options"));
            match table {
                None => Ok(None),
                Some(v) => Ok(Some(match v.as_table() {
                    Some(t) => t
                        .iter()
                        .map(|(k, v)| (k.clone(), toml_to_ini_value(v)))
                        .collect(),
                    // pytest would raise AttributeError here; an empty dict keeps the
                    // file authoritative without inventing a new failure mode.
                    None => Vec::new(),
                })),
            }
        }
        _ => Ok(None),
    }
}

/// Port of `load_config_dict_from_file`'s inner `make_scalar`:
/// `return v if isinstance(v, list) else str(v)`.
///
/// Limitation: pytest leaves list *elements* untouched (a TOML `[1, 2]` stays a list of
/// ints); we stringify them with the same scalar rule so the value type stays uniform.
fn toml_to_ini_value(v: &toml::Value) -> IniValue {
    match v {
        toml::Value::Array(items) => IniValue::List(items.iter().map(python_str).collect()),
        other => IniValue::Str(python_str(other)),
    }
}

/// Python's `str()` for the scalar types `tomllib` yields.
fn python_str(v: &toml::Value) -> String {
    match v {
        toml::Value::String(s) => s.clone(),
        toml::Value::Integer(i) => i.to_string(),
        toml::Value::Boolean(b) => if *b { "True" } else { "False" }.to_string(),
        toml::Value::Float(f) => {
            // Python's repr keeps a decimal point: str(1.0) == "1.0".
            if f.is_finite() && f.fract() == 0.0 && f.abs() < 1e16 {
                format!("{f:.1}")
            } else {
                f.to_string()
            }
        }
        // Datetimes / nested tables: pytest would `str()` a Python object; the TOML
        // rendering is the closest faithful stand-in. Not exercised by real configs.
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Ini value coercion — `_pytest/config/__init__.py::Config._getini`
// ---------------------------------------------------------------------------

/// `type == "args"`: `shlex.split(value) if isinstance(value, str) else value`.
fn getini_args(
    cfg: &ConfigDict,
    name: &str,
    default: &[&str],
    path: &Path,
) -> Result<Vec<String>, ConfigError> {
    match lookup(cfg, name) {
        None => Ok(default.iter().map(|s| (*s).to_string()).collect()),
        Some(IniValue::Str(s)) => shlex_split(s, path),
        Some(IniValue::List(items)) => Ok(items.clone()),
    }
}

/// `type == "linelist"`: `[t for t in map(str.strip, value.split("\n")) if t]`.
///
/// The registered default is `[]` (`get_ini_default_for_type("linelist")`).
fn getini_linelist(cfg: &ConfigDict, name: &str) -> Vec<String> {
    match lookup(cfg, name) {
        None => Vec::new(),
        Some(IniValue::Str(s)) => s
            .split('\n')
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .map(str::to_string)
            .collect(),
        Some(IniValue::List(items)) => items.clone(),
    }
}

// ---------------------------------------------------------------------------
// Path helpers — `_pytest/pathlib.py`
// ---------------------------------------------------------------------------

/// `_pytest/pathlib.py::absolutepath` == `Path(os.path.abspath(path))`: join against the
/// invocation dir when relative, then normalise lexically (no symlink resolution — the
/// docstring explicitly prefers this over `Path.resolve()`, see pytest #6523).
fn absolutepath(invocation_dir: &Path, path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        invocation_dir.join(path)
    };
    normpath(&joined)
}

/// Lexical `os.path.normpath`.
fn normpath(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    let mut normals = 0usize;
    let mut rooted = false;
    for component in path.components() {
        match component {
            Component::Prefix(_) => out.push(component.as_os_str()),
            Component::RootDir => {
                rooted = true;
                out.push(component.as_os_str());
            }
            Component::CurDir => {}
            Component::ParentDir => {
                if normals > 0 {
                    out.pop();
                    normals -= 1;
                } else if !rooted {
                    out.push("..");
                }
                // At the filesystem root, `..` collapses away, like os.path.normpath.
            }
            Component::Normal(part) => {
                out.push(part);
                normals += 1;
            }
        }
    }
    if out.as_os_str().is_empty() {
        out.push(".");
    }
    out
}

/// Stands in for `_pytest/pathlib.py::commonpath`, which wraps `os.path.commonpath` and
/// returns `None` on `ValueError` (different drives, or a mix of absolute and relative
/// paths).
///
/// Not a byte-exact equivalence. Two known differences, both unreachable from
/// [`resolve_config`], which only ever calls this with two existing absolute directories:
/// * Relative pairs with no shared leading component (`"a/b"` vs `"c/d"`): Python returns
///   `""`, we return `None`.
/// * Python raises `ValueError` for a mixed absolute/relative pair, which the pytest
///   wrapper maps to `None`; our component walk would return `None` there too, but by
///   coincidence of the components differing rather than by an explicit check.
fn commonpath(path1: &Path, path2: &Path) -> Option<PathBuf> {
    let c1: Vec<_> = path1.components().collect();
    let c2: Vec<_> = path2.components().collect();
    let shared = c1.iter().zip(c2.iter()).take_while(|(a, b)| a == b).count();
    if shared == 0 {
        return None;
    }
    let mut out = PathBuf::new();
    for component in &c1[..shared] {
        out.push(component.as_os_str());
    }
    Some(out)
}

/// `_pytest/config/findpaths.py::is_fs_root` == `os.path.splitdrive(str(p))[1] == os.sep`.
///
/// UNC divergence (verified against CPython 3.14.2 `os.path.splitdrive`):
///
/// | path | Python tail | Python root? | ours |
/// |---|---|---|---|
/// | `\\server\share` | `""` | no | no |
/// | `\\server\share\` | `"\"` | **yes** | **no** |
/// | `C:\` | `"\"` | yes | yes |
/// | `C:` | `""` | no | no |
///
/// So Python treats the *trailing-separator* UNC share form as a filesystem root and we
/// do not, because we reject non-disk prefixes outright. Left as a comment rather than
/// code: this function is only reachable from the deepest `determine_setup` fallback
/// (no config file, no `setup.py`, nothing in common with the invocation dir), and the
/// consequence of the divergence there is merely which of two already-degenerate
/// rootdirs is chosen.
fn is_fs_root(p: &Path) -> bool {
    use std::path::{Component, Prefix};
    let mut saw_root = false;
    for component in p.components() {
        match component {
            Component::Prefix(prefix) => {
                if !matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)) {
                    return false;
                }
            }
            Component::RootDir => saw_root = true,
            _ => return false,
        }
    }
    saw_root
}

/// `path in other.parents` — a *strict* ancestor test.
fn is_strict_ancestor(candidate: &Path, path: &Path) -> bool {
    path.ancestors().skip(1).any(|a| a == candidate)
}

// ---------------------------------------------------------------------------
// rootdir algorithm — `_pytest/config/findpaths.py`
// ---------------------------------------------------------------------------

/// Port of `findpaths.py::get_dirs_from_args`: drop option-looking args, strip the
/// `::`-suffixed nodeid part, keep only paths that exist, and map files to their parent.
fn get_dirs_from_args(invocation_dir: &Path, args: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for arg in args {
        let raw = arg.to_string_lossy();
        if raw.starts_with('-') {
            continue;
        }
        let file_part = raw.split("::").next().unwrap_or("");
        let path = absolutepath(invocation_dir, Path::new(file_part));
        // `safe_exists` == `Path.exists()` with OSError/ValueError swallowed, which is
        // exactly what Rust's `Path::exists` already does.
        if !path.exists() {
            continue;
        }
        if path.is_dir() {
            out.push(path);
        } else {
            let parent = path
                .parent()
                .map_or_else(|| path.clone(), Path::to_path_buf);
            out.push(parent);
        }
    }
    out
}

/// Port of `findpaths.py::get_common_ancestor`.
fn get_common_ancestor(invocation_dir: &Path, paths: &[PathBuf]) -> PathBuf {
    let mut common: Option<PathBuf> = None;
    for path in paths {
        if !path.exists() {
            continue;
        }
        let Some(ca) = common.take() else {
            common = Some(path.clone());
            continue;
        };
        common = Some(if is_strict_ancestor(&ca, path) || *path == ca {
            ca
        } else if is_strict_ancestor(path, &ca) {
            path.clone()
        } else {
            commonpath(path, &ca).unwrap_or(ca)
        });
    }
    let mut result = common.unwrap_or_else(|| invocation_dir.to_path_buf());
    if result.is_file() {
        result = result.parent().map_or(result.clone(), Path::to_path_buf);
    }
    result
}

/// Port of `findpaths.py::locate_config`.
///
/// Returns `(rootdir, inifile, cfg)`. Within each candidate directory the search follows
/// [`CONFIG_NAMES`] order; directories are visited nearest-first (`argpath`, then its
/// parents). The first *qualifying* file wins outright. A `pyproject.toml` seen along the
/// way is remembered even if it does not qualify, and supplies the rootdir as a last resort.
#[allow(clippy::type_complexity)]
fn locate_config(
    invocation_dir: &Path,
    args: &[PathBuf],
) -> Result<(Option<PathBuf>, Option<PathBuf>, ConfigDict), ConfigError> {
    let filtered: Vec<&PathBuf> = args
        .iter()
        .filter(|x| !x.to_string_lossy().starts_with('-'))
        .collect();
    let fallback = invocation_dir.to_path_buf();
    let effective: Vec<&PathBuf> = if filtered.is_empty() {
        vec![&fallback]
    } else {
        filtered
    };

    let mut found_pyproject_toml: Option<PathBuf> = None;
    for arg in effective {
        let argpath = absolutepath(invocation_dir, arg);
        for base in argpath.ancestors() {
            for config_name in CONFIG_NAMES {
                let p = base.join(config_name);
                if !p.is_file() {
                    continue;
                }
                if *config_name == "pyproject.toml" && found_pyproject_toml.is_none() {
                    found_pyproject_toml = Some(p.clone());
                }
                if let Some(cfg) = load_config_dict_from_file(&p)? {
                    return Ok((Some(base.to_path_buf()), Some(p), cfg));
                }
            }
        }
    }
    if let Some(pyproject) = found_pyproject_toml {
        let parent = pyproject
            .parent()
            .map_or_else(PathBuf::new, Path::to_path_buf);
        return Ok((Some(parent), Some(pyproject), Vec::new()));
    }
    Ok((None, None, Vec::new()))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn pats(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| (*s).to_string()).collect()
    }

    fn defaults_owned(items: &[&str]) -> Vec<String> {
        pats(items)
    }

    fn write(dir: &Path, name: &str, contents: &str) {
        fs::write(dir.join(name), contents).unwrap();
    }

    fn mkdirs(root: &Path, rel: &str) -> PathBuf {
        let p = root.join(rel);
        fs::create_dir_all(&p).unwrap();
        p
    }

    /// The four populated config kinds, so we can prove which one wins.
    fn write_pyproject(dir: &Path) {
        write(
            dir,
            "pyproject.toml",
            "[tool.pytest.ini_options]\npython_classes = [\"FromPyproject\"]\n",
        );
    }
    fn write_tox(dir: &Path) {
        write(dir, "tox.ini", "[pytest]\npython_classes = FromTox\n");
    }
    fn write_setupcfg(dir: &Path) {
        write(
            dir,
            "setup.cfg",
            "[tool:pytest]\npython_classes = FromSetupCfg\n",
        );
    }

    // -- precedence quartet --------------------------------------------------

    #[test]
    fn pytest_ini_wins_even_when_empty() {
        // findpaths.py::load_config_dict_from_file: "pytest.ini files are always the
        // source of configuration, even if empty."
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "");
        write_pyproject(root);
        write_tox(root);
        write_setupcfg(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.rootdir, root);
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pytest.ini").as_path())
        );
        // empty pytest.ini => no ini values at all => registered defaults apply
        assert_eq!(cfg.python_classes, defaults_owned(DEFAULT_PYTHON_CLASSES));
    }

    #[test]
    fn pyproject_wins_over_tox_and_setupcfg() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write_pyproject(root);
        write_tox(root);
        write_setupcfg(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pyproject.toml").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromPyproject"]));
    }

    #[test]
    fn tox_ini_wins_over_setupcfg() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write_tox(root);
        write_setupcfg(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("tox.ini").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromTox"]));
    }

    #[test]
    fn setupcfg_is_last_resort() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write_setupcfg(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("setup.cfg").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromSetupCfg"]));
    }

    #[test]
    fn pyproject_without_ini_options_table_is_not_authoritative() {
        // findpaths.py::load_config_dict_from_file returns None unless
        // [tool.pytest.ini_options] exists.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pyproject.toml", "[project]\nname = \"x\"\n");
        write_tox(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("tox.ini").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromTox"]));
    }

    #[test]
    fn hidden_dot_pytest_ini_participates_ranked_second() {
        // findpaths.py::locate_config config_names includes ".pytest.ini" right after
        // "pytest.ini" and before "pyproject.toml".
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            ".pytest.ini",
            "[pytest]\npython_classes = FromHidden\n",
        );
        write_pyproject(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join(".pytest.ini").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromHidden"]));
    }

    #[test]
    fn empty_dot_pytest_ini_is_not_authoritative() {
        // The "even if empty" escape hatch is keyed on `filepath.name == "pytest.ini"`,
        // so a section-less `.pytest.ini` falls through.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, ".pytest.ini", "");
        write_pyproject(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pyproject.toml").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromPyproject"]));
    }

    #[test]
    fn plain_pytest_section_in_setup_cfg_is_a_usage_error() {
        // findpaths.py::load_config_dict_from_file -> fail(CFG_PYTEST_SECTION)
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "setup.cfg", "[pytest]\npython_classes = Nope\n");

        let err = resolve_config(root, &[root.to_path_buf()]).unwrap_err();
        assert!(
            matches!(&err, ConfigError::Usage { message, .. } if message == CFG_PYTEST_SECTION),
            "unexpected error: {err:?}"
        );
    }

    // -- rootdir algorithm ---------------------------------------------------

    #[test]
    fn rootdir_anchors_at_nearest_ancestor_with_config() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "[pytest]\npython_classes = Anchored\n");
        let deep = mkdirs(root, "pkg/sub/tests");

        let cfg = resolve_config(root, std::slice::from_ref(&deep)).unwrap();
        assert_eq!(cfg.rootdir, root);
        assert_eq!(cfg.python_classes, pats(&["Anchored"]));
    }

    #[test]
    fn rootdir_uses_common_ancestor_of_multiple_args() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "");
        let a = mkdirs(root, "a/tests");
        let b = mkdirs(root, "b/tests");

        let cfg = resolve_config(root, &[a, b]).unwrap();
        assert_eq!(cfg.rootdir, root);
    }

    #[test]
    fn file_arg_and_nodeid_arg_anchor_on_the_containing_dir() {
        // findpaths.py::get_dirs_from_args strips "::..." and maps files to parents.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "");
        let tests = mkdirs(root, "tests");
        write(&tests, "test_a.py", "");

        let nodeid = PathBuf::from(format!("{}::test_x", tests.join("test_a.py").display()));
        let cfg = resolve_config(root, &[nodeid]).unwrap();
        assert_eq!(cfg.rootdir, root);
    }

    #[test]
    fn empty_args_fall_back_to_invocation_dir() {
        // findpaths.py::locate_config: `if not args: args = [invocation_dir]`
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        let inner = mkdirs(root, "inner");
        write(
            &inner,
            "pytest.ini",
            "[pytest]\npython_classes = FromInner\n",
        );

        let cfg = resolve_config(&inner, &[]).unwrap();
        assert_eq!(cfg.rootdir, inner);
        assert_eq!(cfg.python_classes, pats(&["FromInner"]));
    }

    #[test]
    fn option_like_args_are_ignored_when_locating_config() {
        // findpaths.py::locate_config filters `str(x).startswith("-")`.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "");

        let cfg =
            resolve_config(root, &[PathBuf::from("-q"), PathBuf::from("--tb=short")]).unwrap();
        assert_eq!(cfg.rootdir, root);
    }

    #[test]
    fn setup_py_is_the_rootdir_fallback_when_no_config_qualifies() {
        // findpaths.py::determine_setup: walk ancestors for setup.py.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "setup.py", "");
        let deep = mkdirs(root, "src/pkg");

        let cfg = resolve_config(root, &[deep]).unwrap();
        assert_eq!(cfg.rootdir, root);
        assert_eq!(cfg.config_file, None);
        assert_eq!(cfg.python_files, defaults_owned(DEFAULT_PYTHON_FILES));
    }

    #[test]
    fn pyproject_without_table_still_supplies_rootdir_as_last_resort() {
        // findpaths.py::locate_config: `if found_pyproject_toml is not None: return
        // found_pyproject_toml.parent, found_pyproject_toml, {}`
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pyproject.toml", "[project]\nname = \"x\"\n");
        let deep = mkdirs(root, "pkg");

        let cfg = resolve_config(root, &[deep]).unwrap();
        assert_eq!(cfg.rootdir, root);
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pyproject.toml").as_path())
        );
        assert_eq!(cfg.python_classes, defaults_owned(DEFAULT_PYTHON_CLASSES));
    }

    // -- defaults ------------------------------------------------------------

    #[test]
    fn defaults_apply_when_nothing_is_found() {
        // NOTE: assumes no stray pytest config lives above the system temp dir.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        let deep = mkdirs(root, "a/b");

        let cfg = resolve_config(root, &[deep]).unwrap();
        assert_eq!(cfg.config_file, None);
        assert_eq!(cfg.testpaths, Vec::<String>::new());
        assert_eq!(cfg.python_files, defaults_owned(DEFAULT_PYTHON_FILES));
        assert_eq!(cfg.python_classes, defaults_owned(DEFAULT_PYTHON_CLASSES));
        assert_eq!(
            cfg.python_functions,
            defaults_owned(DEFAULT_PYTHON_FUNCTIONS)
        );
        assert_eq!(cfg.norecursedirs, defaults_owned(DEFAULT_NORECURSEDIRS));
        assert_eq!(cfg.addopts, Vec::<String>::new());
        assert_eq!(cfg.markers, Vec::<String>::new());
    }

    #[test]
    fn norecursedirs_default_is_the_extracted_set() {
        assert_eq!(
            DEFAULT_NORECURSEDIRS,
            &[
                "*.egg",
                ".*",
                "_darcs",
                "build",
                "CVS",
                "dist",
                "node_modules",
                "venv",
                "{arch}"
            ]
        );
    }

    // -- the `dirs != [ancestor]` re-locate branch ---------------------------
    //
    // findpaths.py::determine_setup, the `else:` clause of the setup.py for/else:
    //     if dirs != [ancestor]:
    //         rootdir, inipath, inicfg = locate_config(invocation_dir, dirs)
    // Reached only when the common ancestor itself holds no config and no setup.py
    // exists above it, so the search restarts from each arg dir individually.

    #[test]
    fn relocate_branch_finds_config_under_a_single_arg_dir() {
        // Oracle: rootdir=<tmp>/a, inifile=<tmp>/a/pytest.ini, cfg={'python_classes':'FromA'}
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        let a = mkdirs(root, "a");
        let b = mkdirs(root, "b");
        write(&a, "pytest.ini", "[pytest]\npython_classes = FromA\n");

        let cfg = resolve_config(root, &[a.clone(), b]).unwrap();
        assert_eq!(cfg.rootdir, a);
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(a.join("pytest.ini").as_path())
        );
        assert_eq!(cfg.python_classes, pats(&["FromA"]));
    }

    #[test]
    fn relocate_branch_lets_the_first_arg_win() {
        // `locate_config` iterates args in order and returns on the first qualifying
        // file, so arg order — not alphabetical order — decides.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        let a = mkdirs(root, "a");
        let b = mkdirs(root, "b");
        write(&a, "pytest.ini", "[pytest]\npython_classes = FromA\n");
        write(&b, "pytest.ini", "[pytest]\npython_classes = FromB\n");

        let cfg = resolve_config(root, &[a.clone(), b.clone()]).unwrap();
        assert_eq!(cfg.rootdir, a);
        assert_eq!(cfg.python_classes, pats(&["FromA"]));

        // Reversing the args reverses the winner (oracle-confirmed).
        let cfg = resolve_config(root, &[b.clone(), a]).unwrap();
        assert_eq!(cfg.rootdir, b);
        assert_eq!(cfg.python_classes, pats(&["FromB"]));
    }

    // -- ini value normalisation --------------------------------------------

    #[test]
    fn empty_ini_options_table_is_authoritative() {
        // `result is not None` is the test in load_config_dict_from_file, so an empty
        // [tool.pytest.ini_options] still stops the search.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pyproject.toml", "[tool.pytest.ini_options]\n");
        write_tox(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pyproject.toml").as_path())
        );
        assert_eq!(cfg.python_classes, defaults_owned(DEFAULT_PYTHON_CLASSES));
    }

    #[test]
    fn pytest_ini_with_only_a_foreign_section_is_still_authoritative() {
        // No [pytest] section, but `filepath.name == "pytest.ini"` => return {}.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(root, "pytest.ini", "[flake8]\nmax-line-length = 100\n");
        write_tox(root);

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(
            cfg.config_file.as_deref(),
            Some(root.join("pytest.ini").as_path())
        );
        assert_eq!(cfg.python_classes, defaults_owned(DEFAULT_PYTHON_CLASSES));
    }

    #[test]
    fn ini_values_retain_inline_comments() {
        // pytest constructs `iniconfig.IniConfig(path)`, which pins
        // strip_inline_comments=False, so "#" survives into the value and is then just
        // another shlex token (shlex.split has comments=False).
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pytest.ini",
            "[pytest]\npython_classes = Check # trailing\n",
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.python_classes, pats(&["Check", "#", "trailing"]));
    }

    #[test]
    fn ini_colon_form_parses() {
        // iniconfig::_parseline falls back to `line.split(":", 1)` when there is no "=".
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pytest.ini",
            "[pytest]\npython_classes: Check\npython_files: a_*.py b_*.py\n",
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.python_classes, pats(&["Check"]));
        assert_eq!(cfg.python_files, pats(&["a_*.py", "b_*.py"]));
    }

    #[test]
    fn ini_args_values_are_shell_split_and_markers_are_line_split() {
        // config/__init__.py::_getini: type "args" -> shlex.split, "linelist" -> split("\n").
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pytest.ini",
            concat!(
                "[pytest]\n",
                "python_files = check_*.py chk_*.py\n",
                "python_classes = Check Verify\n",
                "python_functions = check\n",
                "norecursedirs = build dist\n",
                "testpaths = tests more_tests\n",
                "addopts = -q --tb=short\n",
                "markers =\n",
                "    slow: marks tests as slow\n",
                "    smoke\n",
            ),
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.python_files, pats(&["check_*.py", "chk_*.py"]));
        assert_eq!(cfg.python_classes, pats(&["Check", "Verify"]));
        assert_eq!(cfg.python_functions, pats(&["check"]));
        assert_eq!(cfg.norecursedirs, pats(&["build", "dist"]));
        assert_eq!(cfg.testpaths, pats(&["tests", "more_tests"]));
        assert_eq!(cfg.addopts, pats(&["-q", "--tb=short"]));
        assert_eq!(cfg.markers, pats(&["slow: marks tests as slow", "smoke"]));
    }

    #[test]
    fn ini_continuation_lines_join_with_newlines_then_shell_split() {
        // iniconfig `_parse.parse_lines`: indented lines continue the previous value.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pytest.ini",
            "[pytest]\npython_files =\n    a_*.py\n    b_*.py\n",
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.python_files, pats(&["a_*.py", "b_*.py"]));
    }

    #[test]
    fn toml_accepts_both_list_and_string_forms() {
        // findpaths.py::load_config_dict_from_file::make_scalar keeps lists, str()s scalars.
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pyproject.toml",
            concat!(
                "[tool.pytest.ini_options]\n",
                "python_files = [\"a_*.py\", \"b_*.py\"]\n",
                "python_classes = \"Check Verify\"\n",
                "markers = [\"slow\", \"smoke\"]\n",
                "addopts = \"-q -x\"\n",
            ),
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.python_files, pats(&["a_*.py", "b_*.py"]));
        assert_eq!(cfg.python_classes, pats(&["Check", "Verify"]));
        assert_eq!(cfg.markers, pats(&["slow", "smoke"]));
        assert_eq!(cfg.addopts, pats(&["-q", "-x"]));
    }

    #[test]
    fn ini_quoted_args_follow_shlex_posix_rules() {
        let tmp = TempDir::new().unwrap();
        let root = tmp.path();
        write(
            root,
            "pytest.ini",
            "[pytest]\naddopts = -k \"not slow\" --basetemp='my dir'\n",
        );

        let cfg = resolve_config(root, &[root.to_path_buf()]).unwrap();
        assert_eq!(cfg.addopts, pats(&["-k", "not slow", "--basetemp=my dir"]));
    }

    // -- name matching -------------------------------------------------------

    #[test]
    fn default_python_functions_matches_testfoo_by_prefix() {
        // THE Phase 0 fact: `_matches_prefix_or_glob_option` tries `startswith` first.
        let defaults = defaults_owned(DEFAULT_PYTHON_FUNCTIONS);
        assert!(matches_name_pattern("testfoo", &defaults));
        assert!(matches_name_pattern("test_proper", &defaults));
        assert!(!matches_name_pattern("checkfoo", &defaults));
        assert!(!matches_name_pattern("helper", &defaults));
    }

    #[test]
    fn glob_bearing_pattern_uses_fnmatch_and_rejects_testfoo() {
        let globbed = pats(&["test_*"]);
        assert!(!matches_name_pattern("testfoo", &globbed));
        assert!(matches_name_pattern("test_foo", &globbed));
    }

    #[test]
    fn glob_pattern_is_anchored_at_both_ends() {
        // fnmatch.translate wraps in (?s:...)\Z — a full match, not a search.
        assert!(!matches_name_pattern("xtest_foo", &pats(&["test_*"])));
        assert!(!matches_name_pattern("check_foo_x", &pats(&["check_?oo"])));
        assert!(matches_name_pattern("check_foo", &pats(&["check_?oo"])));
        assert!(matches_name_pattern("checkX", &pats(&["check[XY]"])));
        assert!(!matches_name_pattern("checkZ", &pats(&["check[XY]"])));
        assert!(matches_name_pattern("checkZ", &pats(&["check[!XY]"])));
    }

    #[test]
    fn default_python_classes_matches_by_prefix() {
        let defaults = defaults_owned(DEFAULT_PYTHON_CLASSES);
        assert!(matches_name_pattern("TestBox", &defaults));
        assert!(matches_name_pattern("Test", &defaults));
        assert!(!matches_name_pattern("MyTest", &defaults));
        assert!(!matches_name_pattern("test_lower", &defaults));
    }

    #[test]
    fn prefix_match_is_case_sensitive_on_every_platform() {
        // `name.startswith(option)` never normcases.
        assert!(!matches_name_pattern(
            "TESTFOO",
            &defaults_owned(DEFAULT_PYTHON_FUNCTIONS)
        ));
    }

    // -- file matching -------------------------------------------------------

    #[test]
    fn default_python_files_accepts_and_rejects_per_fnmatch() {
        let defaults = defaults_owned(DEFAULT_PYTHON_FILES);
        assert!(matches_file_pattern("test_foo.py", &defaults));
        assert!(matches_file_pattern("foo_test.py", &defaults));
        assert!(matches_file_pattern("test_.py", &defaults));
        assert!(!matches_file_pattern("testfoo.py", &defaults));
        assert!(!matches_file_pattern("conftest.py", &defaults));
        assert!(!matches_file_pattern("helpers.py", &defaults));
        assert!(!matches_file_pattern("test_foo.txt", &defaults));
    }

    #[test]
    fn brace_patterns_are_literal_not_alternations() {
        // Python fnmatch has no {a,b} support: DEFAULT_NORECURSEDIRS contains "{arch}".
        assert!(matches_file_pattern("{arch}", &pats(&["{arch}"])));
        assert!(!matches_file_pattern("arch", &pats(&["{arch}"])));
    }

    #[cfg(windows)]
    #[test]
    fn fnmatch_is_case_insensitive_on_windows() {
        // fnmatch.fnmatch normcases both sides; ntpath.normcase case-folds on Windows.
        assert!(matches_file_pattern("TEST_FOO.PY", &pats(&["test_*.py"])));
        assert!(matches_name_pattern("TEST_FOO", &pats(&["test_*"])));
    }

    #[cfg(windows)]
    #[test]
    fn normcase_kelvin_sign_is_an_accepted_divergence() {
        // PINS A KNOWN, ACCEPTED DIVERGENCE — not a claim of pytest parity.
        //
        // `ntpath.normcase` is `_LCMapStringEx(_LOCALE_NAME_INVARIANT, _LCMAP_LOWERCASE, ...)`,
        // which leaves U+212A KELVIN SIGN, U+1E9E CAPITAL SHARP S and U+0130 CAPITAL I
        // WITH DOT untouched. Rust's `to_lowercase` (== Python `str.lower`) folds all
        // three. Verified against CPython 3.14.2:
        //
        //     os.path.normcase("test_\u{212a}.py") == "test_\u{212a}.py"   (unchanged)
        //     "test_\u{212a}.py".lower()           == "test_k.py"
        //     fnmatch.fnmatch("test_\u{212a}.py", "test_k*.py") is False
        //
        // So real pytest would NOT collect this file and we WOULD. Task 5's oracle diff
        // should waive this case rather than treat it as a defect. ASCII names — every
        // realistic module/class/function name — are unaffected.
        assert!(
            matches_file_pattern("test_\u{212a}.py", &pats(&["test_k*.py"])),
            "expected our to_lowercase-based normcase to match; real pytest returns false"
        );
        // Same divergence on the name-matching path's glob branch.
        assert!(matches_name_pattern("test_\u{212a}", &pats(&["test_k*"])));
        // The prefix path never normcases, so it agrees with pytest here.
        assert!(!matches_name_pattern("test_\u{212a}", &pats(&["test_k"])));
    }

    #[cfg(not(windows))]
    #[test]
    fn fnmatch_is_case_sensitive_off_windows() {
        assert!(!matches_file_pattern("TEST_FOO.PY", &pats(&["test_*.py"])));
        assert!(!matches_name_pattern("TEST_FOO", &pats(&["test_*"])));
    }
}
