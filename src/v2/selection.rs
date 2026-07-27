//! `-k` and `-m` selection: pytest's match-expression language, ported.
//!
//! Two things live here, and pytest keeps them in two files too:
//!
//! * the **expression language** — a scanner and a recursive-descent parser over the
//!   grammar in `_pytest/mark/expression.py`, plus an evaluator;
//! * the **matchers** — what an identifier *means* for `-k` (a case-insensitive substring
//!   of one of the test's node names) versus `-m` (the name of a mark the test carries,
//!   optionally with keyword arguments), ported from
//!   `_pytest/mark/__init__.py::KeywordMatcher` / `MarkMatcher`.
//!
//! The grammar, verbatim from `expression.py`'s module docstring (pytest 8.4.2):
//!
//! ```text
//! expression: expr? EOF
//! expr:       and_expr ('or' and_expr)*
//! and_expr:   not_expr ('and' not_expr)*
//! not_expr:   'not' not_expr | '(' expr ')' | ident kwargs?
//!
//! ident:      (\w|:|\+|-|\.|\[|\]|\\|/)+
//! kwargs:     ('(' name '=' value ( ', ' name '=' value )*  ')')
//! name:       a valid ident, but not a reserved keyword
//! value:      (unescaped) string literal | (-)?[0-9]+ | 'False' | 'True' | 'None'
//! ```
//!
//! # Why a port and not a reuse
//!
//! `src/mark_expr.rs` is v1's evaluator and understands `and`/`or`/`not` over bare mark
//! names only — no parentheses, no `-k` substring semantics, no keyword arguments, and no
//! error messages that match pytest's. `-k`/`-m` errors are **usage errors (exit 4)** whose
//! wording users grep for, so the port reproduces pytest's `ParseError` text and its
//! 1-based columns exactly rather than inventing a second dialect.
//!
//! # Where selection happens
//!
//! pytest deselects in `pytest_collection_modifyitems`, i.e. **after** collection and
//! **before** any test runs (`_pytest/mark/__init__.py` l. 283-284: `deselect_by_keyword`
//! then `deselect_by_mark`, in that order). v2 does the same: the manifest is complete
//! first, then filtered, then dispatched. Two consequences that are behaviour, not detail:
//!
//! * a file that fails to import still produces a collection error, and therefore still
//!   exits 2, however aggressively `-k` deselects — selection cannot hide a broken import;
//! * `deselected` is counted, because zero tests *after* selection is exit 5 while zero
//!   tests *before* it is also exit 5 but for a different reason, and the summary line has
//!   to be able to say which.
//!
//! # Character indices, not byte indices
//!
//! `ParseError.column` is 1-based and counts *characters*, because pytest's `pos` indexes a
//! `str`. The scanner therefore works over a `Vec<char>`; using byte offsets would report
//! the wrong column for any expression containing a non-ASCII identifier, and node ids may
//! legally contain any character Python accepts in a name.

use std::collections::HashMap;

use serde_json::Value;

use crate::v2::manifest::{CollectedTest, MarkSpec};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A syntax error in a match expression.  Port of `expression.py::ParseError`, whose
/// `__str__` is `f"at column {self.column}: {self.message}"`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    /// 1-based **character** column.
    pub column: usize,
    pub message: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "at column {}: {}", self.column, self.message)
    }
}

impl std::error::Error for ParseError {}

/// Everything selection can refuse to do.  Both variants are pytest **usage errors**
/// (`UsageError` → `ExitCode.USAGE_ERROR`, 4), which is why they are one type: the caller
/// only ever has to decide "usage" versus "internal", exactly as it does for config.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectionError {
    /// `_pytest/mark/__init__.py::_parse_expression` — `f"{exc_message}: {expr}: {e}"`,
    /// where `exc_message` is `"Wrong expression passed to '-k'"` or `...'-m'`.
    Parse {
        flag: &'static str,
        expression: String,
        error: ParseError,
    },
    /// Raised while *evaluating*, not while parsing: `KeywordMatcher.__call__` refuses
    /// keyword arguments.  Kept distinct so the message is pytest's verbatim.
    Usage(String),
}

impl std::fmt::Display for SelectionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SelectionError::Parse {
                flag,
                expression,
                error,
            } => write!(
                f,
                "Wrong expression passed to '{flag}': {expression}: {error}"
            ),
            SelectionError::Usage(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for SelectionError {}

// ---------------------------------------------------------------------------
// Scanner — `expression.py::Scanner`
// ---------------------------------------------------------------------------

/// `expression.py::TokenType`.  The string payloads are the enum *values*, which
/// [`Scanner::reject`] splices into its message, so they are part of the error contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TokenType {
    LParen,
    RParen,
    Or,
    And,
    Not,
    Ident,
    Eof,
    Equal,
    String,
    Comma,
}

impl TokenType {
    fn value(self) -> &'static str {
        match self {
            TokenType::LParen => "left parenthesis",
            TokenType::RParen => "right parenthesis",
            TokenType::Or => "or",
            TokenType::And => "and",
            TokenType::Not => "not",
            TokenType::Ident => "identifier",
            TokenType::Eof => "end of input",
            TokenType::Equal => "=",
            TokenType::String => "string literal",
            TokenType::Comma => ",",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Token {
    kind: TokenType,
    value: String,
    /// 0-based character position, as pytest's `Token.pos` is.
    pos: usize,
}

/// `\w` for a Python `str` pattern, which is Unicode-aware.
///
/// CPython's `\w` is "word characters": alphanumerics plus the underscore, widened to
/// Unicode.  `char::is_alphanumeric` covers the alphabetic and numeric categories;
/// `'_'` is spelled out because it is connector punctuation, not alphanumeric.  Test
/// function names may be written in any script Python accepts (`def test_測試():`), so an
/// ASCII-only class would refuse to lex a legal `-k` argument.
fn is_word_char(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

/// The `ident` character class: `(\w|:|\+|-|\.|\[|\]|\\|/)+`.
///
/// (pytest writes the group as `(:?...)`, a typo for the non-capturing `(?:...)`; it is
/// harmless there because `:` is already one of the alternatives, and the effective class
/// is the one below.)
fn is_ident_char(ch: char) -> bool {
    is_word_char(ch) || matches!(ch, ':' | '+' | '-' | '.' | '[' | ']' | '\\' | '/')
}

struct Scanner {
    tokens: Vec<Token>,
    index: usize,
}

impl Scanner {
    fn new(input: &str) -> Result<Self, ParseError> {
        Ok(Self {
            tokens: lex(input)?,
            index: 0,
        })
    }

    fn current(&self) -> &Token {
        // `lex` always ends with EOF and `accept` never advances past it, so this is
        // total by construction.
        &self.tokens[self.index]
    }

    /// `Scanner.accept` — consume and return the token when it has `kind`, else `None`.
    /// EOF is never consumed, matching `if token.type is not TokenType.EOF`.
    fn accept(&mut self, kind: TokenType) -> Option<Token> {
        if self.current().kind != kind {
            return None;
        }
        let token = self.current().clone();
        if token.kind != TokenType::Eof {
            self.index += 1;
        }
        Some(token)
    }

    /// `Scanner.accept(..., reject=True)`.
    fn expect(&mut self, kind: TokenType) -> Result<Token, ParseError> {
        match self.accept(kind) {
            Some(token) => Ok(token),
            None => Err(self.reject(&[kind])),
        }
    }

    /// `Scanner.reject` — the exact wording, including the ` OR ` join and the 1-based
    /// column.
    fn reject(&self, expected: &[TokenType]) -> ParseError {
        let names: Vec<&str> = expected.iter().map(|kind| kind.value()).collect();
        ParseError {
            column: self.current().pos + 1,
            message: format!(
                "expected {}; got {}",
                names.join(" OR "),
                self.current().kind.value()
            ),
        }
    }
}

/// `Scanner.lex`, character for character.
fn lex(input: &str) -> Result<Vec<Token>, ParseError> {
    let chars: Vec<char> = input.chars().collect();
    // pytest's `input.find("\\")` scans the *whole* input, not the string literal it is
    // inside; computed once here for the same reason.  See the STRING branch.
    let first_backslash = chars.iter().position(|ch| *ch == '\\');
    let mut tokens = Vec::new();
    let mut pos = 0usize;

    while pos < chars.len() {
        let ch = chars[pos];
        if ch == ' ' || ch == '\t' {
            pos += 1;
        } else if let Some(kind) = match ch {
            '(' => Some(TokenType::LParen),
            ')' => Some(TokenType::RParen),
            '=' => Some(TokenType::Equal),
            ',' => Some(TokenType::Comma),
            _ => None,
        } {
            tokens.push(Token {
                kind,
                value: ch.to_string(),
                pos,
            });
            pos += 1;
        } else if ch == '\'' || ch == '"' {
            let Some(offset) = chars[pos + 1..].iter().position(|c| *c == ch) else {
                return Err(ParseError {
                    column: pos + 1,
                    message: format!("closing quote \"{ch}\" is missing"),
                });
            };
            let end = pos + 1 + offset;
            // Ported verbatim, quirk included: pytest looks for a backslash **anywhere in
            // the input**, not inside the literal it just scanned, so `-m 'a\b or "x"'`
            // reports the error at the backslash in `a\b` even though that is a perfectly
            // legal ident character.  Reproducing it matters because the column is part of
            // the message users read.
            if let Some(backslash) = first_backslash {
                return Err(ParseError {
                    column: backslash + 1,
                    message: r#"escaping with "\" not supported in marker expression"#.to_string(),
                });
            }
            let value: String = chars[pos..=end].iter().collect();
            tokens.push(Token {
                kind: TokenType::String,
                value,
                pos,
            });
            pos = end + 1;
        } else if is_ident_char(ch) {
            let mut end = pos;
            while end < chars.len() && is_ident_char(chars[end]) {
                end += 1;
            }
            let value: String = chars[pos..end].iter().collect();
            let kind = match value.as_str() {
                "or" => TokenType::Or,
                "and" => TokenType::And,
                "not" => TokenType::Not,
                _ => TokenType::Ident,
            };
            tokens.push(Token { kind, value, pos });
            pos = end;
        } else {
            return Err(ParseError {
                column: pos + 1,
                message: format!("unexpected character \"{ch}\""),
            });
        }
    }

    tokens.push(Token {
        kind: TokenType::Eof,
        value: String::new(),
        pos,
    });
    Ok(tokens)
}

// ---------------------------------------------------------------------------
// Parser — `expression.py::expression/expr/and_expr/not_expr`
// ---------------------------------------------------------------------------

/// A keyword-argument value: `value: (unescaped) string literal | (-)?[0-9]+ | 'False' |
/// 'True' | 'None'`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KwValue {
    Str(String),
    Int(i64),
    Bool(bool),
    None,
}

impl KwValue {
    /// The JSON value this compares equal to, so [`MarkMatcher`] can test it against a
    /// [`MarkSpec`]'s kwargs without a second representation.
    fn as_json(&self) -> Value {
        match self {
            KwValue::Str(text) => Value::String(text.clone()),
            KwValue::Int(number) => Value::Number((*number).into()),
            KwValue::Bool(flag) => Value::Bool(*flag),
            KwValue::None => Value::Null,
        }
    }
}

/// The parsed expression tree.  pytest compiles to a Python AST and `eval`s it; the shapes
/// are the same, minus the `$` ident prefix, which exists only to keep `True`/`False`/`None`
/// out of Python's own literal namespace and has no analogue here.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Node {
    /// The empty expression.  `expression.py::expression`: `ast.Constant(False)` — an
    /// empty `-m` deselects **everything** rather than matching everything.
    Const(bool),
    Ident(String),
    Call {
        name: String,
        kwargs: Vec<(String, KwValue)>,
    },
    Not(Box<Node>),
    And(Box<Node>, Box<Node>),
    Or(Box<Node>, Box<Node>),
}

/// A compiled `-k`/`-m` expression.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expression {
    root: Node,
}

impl Expression {
    /// `Expression.compile`.
    pub fn compile(input: &str) -> Result<Self, ParseError> {
        let mut scanner = Scanner::new(input)?;
        let root = if scanner.accept(TokenType::Eof).is_some() {
            Node::Const(false)
        } else {
            let node = parse_expr(&mut scanner)?;
            scanner.expect(TokenType::Eof)?;
            node
        };
        Ok(Self { root })
    }

    /// `Expression.evaluate`.  The matcher may refuse (see [`SelectionError::Usage`]).
    pub fn evaluate(&self, matcher: &dyn Matcher) -> Result<bool, SelectionError> {
        evaluate(&self.root, matcher)
    }
}

fn parse_expr(scanner: &mut Scanner) -> Result<Node, ParseError> {
    let mut node = parse_and_expr(scanner)?;
    while scanner.accept(TokenType::Or).is_some() {
        let rhs = parse_and_expr(scanner)?;
        node = Node::Or(Box::new(node), Box::new(rhs));
    }
    Ok(node)
}

fn parse_and_expr(scanner: &mut Scanner) -> Result<Node, ParseError> {
    let mut node = parse_not_expr(scanner)?;
    while scanner.accept(TokenType::And).is_some() {
        let rhs = parse_not_expr(scanner)?;
        node = Node::And(Box::new(node), Box::new(rhs));
    }
    Ok(node)
}

fn parse_not_expr(scanner: &mut Scanner) -> Result<Node, ParseError> {
    if scanner.accept(TokenType::Not).is_some() {
        return Ok(Node::Not(Box::new(parse_not_expr(scanner)?)));
    }
    if scanner.accept(TokenType::LParen).is_some() {
        let node = parse_expr(scanner)?;
        scanner.expect(TokenType::RParen)?;
        return Ok(node);
    }
    if let Some(ident) = scanner.accept(TokenType::Ident) {
        if scanner.accept(TokenType::LParen).is_some() {
            let kwargs = parse_all_kwargs(scanner)?;
            scanner.expect(TokenType::RParen)?;
            return Ok(Node::Call {
                name: ident.value,
                kwargs,
            });
        }
        return Ok(Node::Ident(ident.value));
    }
    Err(scanner.reject(&[TokenType::Not, TokenType::LParen, TokenType::Ident]))
}

fn parse_all_kwargs(scanner: &mut Scanner) -> Result<Vec<(String, KwValue)>, ParseError> {
    let mut kwargs = vec![parse_single_kwarg(scanner)?];
    while scanner.accept(TokenType::Comma).is_some() {
        kwargs.push(parse_single_kwarg(scanner)?);
    }
    Ok(kwargs)
}

/// `str.isidentifier()`.
///
/// CPython tests XID_Start / XID_Continue; approximated here as alphabetic-or-underscore
/// followed by alphanumeric-or-underscore, which agrees on every character reachable
/// through the `ident` class above (that class admits no other connector punctuation).
fn is_python_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        None => false,
        Some(first) if !(first.is_alphabetic() || first == '_') => false,
        Some(_) => chars.all(is_word_char),
    }
}

/// `keyword.iskeyword` for Python 3.12-3.14 (`keyword.kwlist`).  Soft keywords
/// (`match`, `case`, `type`, `_`) are deliberately absent: `iskeyword` returns False for
/// them, so `-m "mark(match='x')"` is legal and must stay legal.
const PYTHON_KEYWORDS: [&str; 35] = [
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import",
    "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
    "with", "yield",
];

fn parse_single_kwarg(scanner: &mut Scanner) -> Result<(String, KwValue), ParseError> {
    let name = scanner.expect(TokenType::Ident)?;
    if !is_python_identifier(&name.value) {
        return Err(ParseError {
            column: name.pos + 1,
            message: format!("not a valid python identifier {}", name.value),
        });
    }
    if PYTHON_KEYWORDS.contains(&name.value.as_str()) {
        return Err(ParseError {
            column: name.pos + 1,
            message: format!("unexpected reserved python keyword `{}`", name.value),
        });
    }
    scanner.expect(TokenType::Equal)?;

    if let Some(literal) = scanner.accept(TokenType::String) {
        // `value_token.value[1:-1]` — strip the quotes the scanner kept.
        let text: String = literal.value.chars().skip(1).collect();
        let mut text = text;
        let _ = text.pop();
        return Ok((name.value, KwValue::Str(text)));
    }

    let token = scanner.expect(TokenType::Ident)?;
    let number = &token.value;
    let digits = number.strip_prefix('-').unwrap_or(number);
    // `str.isdigit()` is true only for a non-empty run of digit characters.
    if !digits.is_empty() && digits.chars().all(|ch| ch.is_ascii_digit()) {
        let parsed = number.parse::<i64>().map_err(|_| ParseError {
            column: token.pos + 1,
            message: format!("unexpected character/s \"{number}\""),
        })?;
        return Ok((name.value, KwValue::Int(parsed)));
    }
    match number.as_str() {
        "True" => Ok((name.value, KwValue::Bool(true))),
        "False" => Ok((name.value, KwValue::Bool(false))),
        "None" => Ok((name.value, KwValue::None)),
        _ => Err(ParseError {
            column: token.pos + 1,
            message: format!("unexpected character/s \"{number}\""),
        }),
    }
}

// ---------------------------------------------------------------------------
// Evaluation
// ---------------------------------------------------------------------------

/// What an identifier means.  `expression.py::MatcherCall`.
pub trait Matcher {
    fn matches(&self, name: &str, kwargs: &[(String, KwValue)]) -> Result<bool, SelectionError>;
}

/// Short-circuiting `and`/`or`, exactly as Python's `BoolOp` evaluates them — which is
/// observable, because a matcher may raise.
fn evaluate(node: &Node, matcher: &dyn Matcher) -> Result<bool, SelectionError> {
    match node {
        Node::Const(value) => Ok(*value),
        Node::Ident(name) => matcher.matches(name, &[]),
        Node::Call { name, kwargs } => matcher.matches(name, kwargs),
        Node::Not(inner) => Ok(!evaluate(inner, matcher)?),
        Node::And(lhs, rhs) => {
            if evaluate(lhs, matcher)? {
                evaluate(rhs, matcher)
            } else {
                Ok(false)
            }
        }
        Node::Or(lhs, rhs) => {
            if evaluate(lhs, matcher)? {
                Ok(true)
            } else {
                evaluate(rhs, matcher)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Matchers
// ---------------------------------------------------------------------------

/// `-k`: a case-insensitive substring of one of the test's **node names**.
///
/// Port of `_pytest/mark/__init__.py::KeywordMatcher`, whose `from_item` walks
/// `item.listchain()` and keeps `node.name` for every node except the `Session` and the
/// root `Directory` (`isinstance(node.parent, Session)`).  Reconstructed here from the
/// manifest entry, because v2 has no live node tree:
///
/// | pytest node | manifest source | probed with |
/// |---|---|---|
/// | intermediate `Dir`/`Package` | each directory component of `path` | `-k alpha` |
/// | `Module` | the file's basename, **with** `.py` | `-k test_first.py` |
/// | `Class` (and any enclosing class) | the `qualname` segments before the last | `-k TestBox` |
/// | `Function` | the last `qualname` segment **plus** `[param_id]` | `-k "test_param[1]"` |
/// | markers | `mark.name` for each mark (`mapped_names.update(mark.name for mark in item.iter_markers())`) | `-k slow`, `-k smoke` on a class-marked method |
///
/// The root directory is skipped because its `parent` is the `Session`; every *deeper*
/// directory contributes its own basename, which the `-k alpha` row proves is not an
/// accident of that tree.
///
/// **Two documented gaps**, both needing a wire field v2 does not have: names assigned
/// directly onto the test function (`mapped_names.update(function_obj.__dict__)`) and
/// `item.listextrakeywords()`.  Both are rare and neither can be reconstructed from a
/// manifest entry — the manifest is data, and these are live-object introspection.
///
/// **A third gap is shared with [`MarkMatcher`] and is worth naming here too**, because the
/// mark names in this set are the reason it bites `-k` as well as `-m`: pytest's
/// `iter_markers()` yields a `parametrize` mark, so *both* `-m parametrize` and
/// `-k parametrize` select every parametrized case.  v2's collector consumes `@parametrize`
/// into `param_id` and records no mark of that name, so both select nothing.  Probed on a
/// two-case tree: pytest `2/3 tests collected (1 deselected)`, v2 nothing.  Fixing it means
/// deciding whether a synthesised `parametrize` mark belongs on the wire — recorded, not
/// guessed.
#[derive(Debug, Clone)]
pub struct KeywordMatcher {
    /// Lower-cased once at construction: the matcher is applied per identifier per test,
    /// and `KeywordMatcher.__call__` lower-cases both sides on every call.
    names: Vec<String>,
}

impl KeywordMatcher {
    pub fn from_test(test: &CollectedTest) -> Self {
        let mut names: Vec<String> = Vec::new();

        let segments: Vec<&str> = test.path.split('/').filter(|s| !s.is_empty()).collect();
        // Every component of the path contributes a node name: the directories as
        // `Dir`/`Package` nodes, the last component as the `Module`.
        for segment in &segments {
            names.push(segment.to_lowercase());
        }

        // `qualname` is the dotted path inside the module — "TestBox.test_method" or just
        // "test_top".  Everything before the last segment is a Class node; deriving them
        // from `qualname` rather than from `class_name` is what keeps nested classes
        // right, since `class_name` records only the innermost one.
        let parts: Vec<&str> = test.qualname.split('.').collect();
        if let Some((last, classes)) = parts.split_last() {
            for class in classes {
                names.push(class.to_lowercase());
            }
            // The `Function` node's name carries the parametrize suffix: pytest's node is
            // literally named `test_param[1]`, which is why `-k "test_param[1]"` selects a
            // single case.
            let function = match &test.param_id {
                Some(param) => format!("{last}[{param}]"),
                None => (*last).to_string(),
            };
            names.push(function.to_lowercase());
        }

        for mark in &test.marks {
            names.push(mark.name.to_lowercase());
        }

        Self { names }
    }
}

impl Matcher for KeywordMatcher {
    fn matches(&self, name: &str, kwargs: &[(String, KwValue)]) -> Result<bool, SelectionError> {
        if !kwargs.is_empty() {
            // `KeywordMatcher.__call__`: `raise UsageError("Keyword expressions do not
            // support call parameters.")`.  Raised while *evaluating*, so an expression
            // nothing is evaluated against (an empty tree) is never rejected — matching
            // pytest, where `deselect_by_keyword` loops over zero items.
            return Err(SelectionError::Usage(
                "Keyword expressions do not support call parameters.".to_string(),
            ));
        }
        let needle = name.to_lowercase();
        Ok(self.names.iter().any(|name| name.contains(&needle)))
    }
}

/// `-m`: the name of a mark the test carries, with optional keyword-argument constraints.
///
/// Port of `_pytest/mark/__init__.py::MarkMatcher`.  A name with no kwargs matches when the
/// test carries a mark of that name at all; with kwargs, **at least one** mark of that name
/// must satisfy **every** constraint (`all(mark.kwargs.get(k, NOT_SET) == v ...)`), where a
/// missing key never compares equal — including to `None`, which is why the sentinel exists
/// and a plain `.get(k)` returning `null` would be wrong.
#[derive(Debug, Clone)]
pub struct MarkMatcher<'a> {
    by_name: HashMap<&'a str, Vec<&'a MarkSpec>>,
}

impl<'a> MarkMatcher<'a> {
    pub fn from_marks(marks: &'a [MarkSpec]) -> Self {
        let mut by_name: HashMap<&'a str, Vec<&'a MarkSpec>> = HashMap::new();
        for mark in marks {
            by_name.entry(mark.name.as_str()).or_default().push(mark);
        }
        Self { by_name }
    }
}

impl Matcher for MarkMatcher<'_> {
    fn matches(&self, name: &str, kwargs: &[(String, KwValue)]) -> Result<bool, SelectionError> {
        let Some(marks) = self.by_name.get(name) else {
            return Ok(false);
        };
        Ok(marks.iter().any(|mark| {
            kwargs.iter().all(|(key, expected)| {
                mark.kwargs
                    .get(key)
                    .is_some_and(|actual| *actual == expected.as_json())
            })
        }))
    }
}

// ---------------------------------------------------------------------------
// The deselection pass
// ---------------------------------------------------------------------------

/// What selection left behind.
#[derive(Debug, Clone, PartialEq)]
pub struct Selection {
    /// The surviving tests, in manifest order.
    pub kept: Vec<CollectedTest>,
    /// How many were removed — pytest's `N deselected`.
    pub deselected: usize,
}

/// Apply `-k` then `-m`, in `pytest_collection_modifyitems`' order.
///
/// Both arguments are the raw option values.  Two emptiness rules, and they differ, which
/// is pytest's asymmetry and not a slip:
///
/// * `-k` is `config.option.keyword.lstrip()`, and a falsy result **skips filtering**, so
///   `-k "   "` selects everything (probed: 7 of 7 collected);
/// * `-m` is `config.option.markexpr` with no strip at all, so `-m "   "` is truthy,
///   compiles to the empty expression, evaluates to `False`, and deselects **everything**
///   (probed: exit 5, `1 deselected`).  Only `-m ""` skips filtering.
pub fn deselect(
    tests: Vec<CollectedTest>,
    keyword: Option<&str>,
    mark: Option<&str>,
) -> Result<Selection, SelectionError> {
    let mask = select_mask(&tests, keyword, mark)?;
    let deselected = mask.iter().filter(|keep| !**keep).count();
    let kept = tests
        .into_iter()
        .zip(&mask)
        .filter_map(|(test, keep)| keep.then_some(test))
        .collect();
    Ok(Selection { kept, deselected })
}

/// The same decision as [`deselect`], expressed as one flag per input test.
///
/// The execute half needs this shape rather than a filtered `Vec`: it carries a parallel
/// array saying which *file* each test came from (so a test is dispatched to the worker
/// that collected it), and a filtered list would have to be re-joined to it by id — which
/// is wrong the moment a file is named twice on the command line and the same id appears
/// twice in the manifest.
pub fn select_mask(
    tests: &[CollectedTest],
    keyword: Option<&str>,
    mark: Option<&str>,
) -> Result<Vec<bool>, SelectionError> {
    let mut keep = vec![true; tests.len()];

    if let Some(raw) = keyword {
        // `config.option.keyword.lstrip()` — and a falsy result skips filtering entirely.
        let expression = raw.trim_start();
        if !expression.is_empty() {
            let compiled = compile(expression, "-k")?;
            for (test, keep) in tests.iter().zip(keep.iter_mut()) {
                if *keep && !compiled.evaluate(&KeywordMatcher::from_test(test))? {
                    *keep = false;
                }
            }
        }
    }

    if let Some(expression) = mark {
        if !expression.is_empty() {
            let compiled = compile(expression, "-m")?;
            for (test, keep) in tests.iter().zip(keep.iter_mut()) {
                if *keep && !compiled.evaluate(&MarkMatcher::from_marks(&test.marks))? {
                    *keep = false;
                }
            }
        }
    }

    Ok(keep)
}

fn compile(expression: &str, flag: &'static str) -> Result<Expression, SelectionError> {
    Expression::compile(expression).map_err(|error| SelectionError::Parse {
        flag,
        expression: expression.to_string(),
        error,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map};

    // --- helpers ----------------------------------------------------------

    fn mark(name: &str) -> MarkSpec {
        MarkSpec {
            name: name.to_string(),
            args: Vec::new(),
            kwargs: Map::new(),
        }
    }

    fn mark_with(name: &str, pairs: &[(&str, Value)]) -> MarkSpec {
        let mut kwargs = Map::new();
        for (key, value) in pairs {
            kwargs.insert((*key).to_string(), value.clone());
        }
        MarkSpec {
            name: name.to_string(),
            args: Vec::new(),
            kwargs,
        }
    }

    fn test_case(path: &str, qualname: &str) -> CollectedTest {
        CollectedTest {
            id: format!("{path}::{}", qualname.replace('.', "::")),
            path: path.to_string(),
            qualname: qualname.to_string(),
            class_name: None,
            param_id: None,
            marks: Vec::new(),
            fixtures: Vec::new(),
            tier: crate::v2::manifest::Tier::Dynamic,
        }
    }

    /// A matcher over a fixed name set, for the pure grammar tests.
    struct Names(Vec<String>);

    impl Matcher for Names {
        fn matches(
            &self,
            name: &str,
            _kwargs: &[(String, KwValue)],
        ) -> Result<bool, SelectionError> {
            Ok(self.0.iter().any(|known| known == name))
        }
    }

    fn eval_with(expression: &str, names: &[&str]) -> bool {
        let matcher = Names(names.iter().map(|n| (*n).to_string()).collect());
        Expression::compile(expression)
            .expect("expression compiles")
            .evaluate(&matcher)
            .expect("expression evaluates")
    }

    fn parse_error(expression: &str) -> ParseError {
        Expression::compile(expression).expect_err("expression is invalid")
    }

    // --- grammar ----------------------------------------------------------

    /// `expression: expr? EOF` with the empty branch: `ast.Constant(False)`.  This is not
    /// a degenerate case — `-m "   "` reaches it, and pytest deselects the whole suite
    /// (probed: exit 5, `1 deselected`).  A `Const(true)` here would silently select
    /// everything instead.
    #[test]
    fn the_empty_expression_is_false() {
        assert!(!eval_with("", &["slow"]));
        assert!(!eval_with("   ", &["slow"]));
        assert!(!eval_with("\t", &["slow"]));
    }

    #[test]
    fn a_bare_identifier_defers_to_the_matcher() {
        assert!(eval_with("slow", &["slow"]));
        assert!(!eval_with("slow", &["fast"]));
    }

    #[test]
    fn boolean_operators_follow_the_usual_semantics() {
        assert!(eval_with("a or b", &["b"]));
        assert!(!eval_with("a or b", &["c"]));
        assert!(eval_with("a and b", &["a", "b"]));
        assert!(!eval_with("a and b", &["a"]));
        assert!(eval_with("not a", &["b"]));
        assert!(!eval_with("not a", &["a"]));
    }

    /// `and` binds tighter than `or` — the grammar's `expr: and_expr ('or' and_expr)*`.
    /// Flattening the two into one precedence level would make `a or b and c` select `b`
    /// alone, which is a different suite.
    #[test]
    fn and_binds_tighter_than_or() {
        assert!(eval_with("a or b and c", &["a"]));
        assert!(!eval_with("a or b and c", &["b"]));
        assert!(eval_with("a or b and c", &["b", "c"]));
    }

    /// `not` is the innermost, and `not not x` must round-trip — `not_expr: 'not'
    /// not_expr` recurses into itself, not into `expr`.
    ///
    /// `not a or b` over `[a, b]` is the row that actually distinguishes the two: correct
    /// is `(not a) or b` = true, while a `not` that swallowed the rest of the expression
    /// would give `not (a or b)` = false.  Every *other* shape here agrees under both
    /// readings — `not a and b` evaluates the same either way for both name sets — which a
    /// mutation run proved by surviving the first version of this test.
    #[test]
    fn not_is_right_recursive_and_binds_tightest() {
        assert!(eval_with("not not a", &["a"]));
        assert!(!eval_with("not a and b", &["a", "b"]));
        assert!(eval_with("not a and b", &["b"]));
        assert!(eval_with("not a or b", &["a", "b"]));
        assert!(!eval_with("not a or b", &["a"]));
    }

    #[test]
    fn parentheses_override_precedence() {
        assert!(eval_with("(a or b) and c", &["b", "c"]));
        assert!(!eval_with("(a or b) and c", &["b"]));
        assert!(!eval_with("not (a or b)", &["a"]));
        assert!(eval_with("not (a or b)", &["c"]));
    }

    /// The `ident` class is far wider than `\w`: node ids contain `[`, `]`, `.`, `/` and
    /// `-`, and `-k` is matched against them, so every one of those has to lex as part of
    /// a single identifier rather than as punctuation.
    #[test]
    fn the_ident_class_admits_nodeid_punctuation() {
        assert!(eval_with("test_param[1]", &["test_param[1]"]));
        assert!(eval_with("a/b/test_x.py", &["a/b/test_x.py"]));
        assert!(eval_with("x-1", &["x-1"]));
        assert!(eval_with("mod::cls", &["mod::cls"]));
        assert!(eval_with("a+b", &["a+b"]));
    }

    /// Unicode `\w`.  `def test_測試():` is legal Python, so an ASCII-only ident class
    /// would refuse a legal `-k` argument with `unexpected character`.
    #[test]
    fn identifiers_may_be_non_ascii() {
        assert!(eval_with("test_測試", &["test_測試"]));
    }

    #[test]
    fn keywords_are_only_keywords_when_they_stand_alone() {
        // "nothing" starts with "not" but lexes as one identifier: the regex is greedy.
        assert!(eval_with("nothing", &["nothing"]));
        assert!(eval_with("android", &["android"]));
        assert!(eval_with("origin", &["origin"]));
    }

    // --- parse errors: wording and 1-based columns -------------------------

    #[test]
    fn an_unexpected_character_reports_its_column() {
        assert_eq!(
            parse_error("a & b"),
            ParseError {
                column: 3,
                message: "unexpected character \"&\"".to_string(),
            }
        );
    }

    /// The exact string pytest printed in the probe: `ERROR: Wrong expression passed to
    /// '-k': and and: at column 1: expected not OR left parenthesis OR identifier; got and`.
    #[test]
    fn a_leading_operator_reproduces_pytests_reject_message() {
        assert_eq!(
            parse_error("and and").to_string(),
            "at column 1: expected not OR left parenthesis OR identifier; got and"
        );
        assert_eq!(
            SelectionError::Parse {
                flag: "-k",
                expression: "and and".to_string(),
                error: parse_error("and and"),
            }
            .to_string(),
            "Wrong expression passed to '-k': and and: at column 1: expected not OR left parenthesis OR identifier; got and"
        );
    }

    /// Probed verbatim: `-m 'not not not ('` -> `at column 14: expected not OR left
    /// parenthesis OR identifier; got end of input`.  Column 14 is one past the input,
    /// which is where the EOF token sits.
    #[test]
    fn a_trailing_open_paren_points_at_end_of_input() {
        assert_eq!(
            parse_error("not not not (").to_string(),
            "at column 14: expected not OR left parenthesis OR identifier; got end of input"
        );
    }

    #[test]
    fn an_unclosed_paren_is_rejected_at_the_eof_token() {
        assert_eq!(
            parse_error("(a").to_string(),
            "at column 3: expected right parenthesis; got end of input"
        );
    }

    #[test]
    fn trailing_junk_after_a_complete_expression_is_rejected() {
        assert_eq!(
            parse_error("a b").to_string(),
            "at column 3: expected end of input; got identifier"
        );
    }

    #[test]
    fn a_missing_closing_quote_names_the_quote_character() {
        assert_eq!(
            parse_error("m(k='x)"),
            ParseError {
                column: 5,
                message: "closing quote \"'\" is missing".to_string(),
            }
        );
    }

    /// pytest scans the **whole input** for a backslash, not the literal it is inside.
    /// Ported verbatim: the column points at the backslash in the *identifier*, which is
    /// well before the string literal that triggered the check.
    #[test]
    fn the_backslash_check_scans_the_whole_input_not_the_literal() {
        assert_eq!(
            parse_error(r#"a\b or m(k='x')"#),
            ParseError {
                column: 2,
                message: r#"escaping with "\" not supported in marker expression"#.to_string(),
            }
        );
        // ... and with no string literal anywhere, the same backslash is a perfectly
        // ordinary ident character, because the check lives in the STRING branch.
        assert!(eval_with(r"a\b", &[r"a\b"]));
    }

    #[test]
    fn a_reserved_keyword_cannot_be_a_kwarg_name() {
        assert_eq!(
            parse_error("m(class='x')").to_string(),
            "at column 3: unexpected reserved python keyword `class`"
        );
        // A *soft* keyword is not reserved: `keyword.iskeyword("match")` is False.
        assert!(Expression::compile("m(match='x')").is_ok());
    }

    #[test]
    fn a_kwarg_name_must_be_a_python_identifier() {
        assert_eq!(
            parse_error("m(1a='x')").to_string(),
            "at column 3: not a valid python identifier 1a"
        );
        assert_eq!(
            parse_error("m(a.b='x')").to_string(),
            "at column 3: not a valid python identifier a.b"
        );
    }

    #[test]
    fn an_unquoted_non_literal_kwarg_value_is_rejected() {
        assert_eq!(
            parse_error("m(k=wat)").to_string(),
            r#"at column 5: unexpected character/s "wat""#
        );
    }

    #[test]
    fn kwarg_values_cover_every_documented_literal() {
        let compiled =
            Expression::compile("m(s='x', i=3, n=-4, t=True, f=False, z=None)").expect("compiles");
        let Node::Call { name, kwargs } = &compiled.root else {
            panic!("expected a call node, got {:?}", compiled.root);
        };
        assert_eq!(name, "m");
        assert_eq!(
            kwargs,
            &vec![
                ("s".to_string(), KwValue::Str("x".to_string())),
                ("i".to_string(), KwValue::Int(3)),
                ("n".to_string(), KwValue::Int(-4)),
                ("t".to_string(), KwValue::Bool(true)),
                ("f".to_string(), KwValue::Bool(false)),
                ("z".to_string(), KwValue::None),
            ]
        );
    }

    /// A double-quoted literal is the same token type as a single-quoted one, and the
    /// quotes are stripped from **both** ends — `value_token.value[1:-1]`.
    #[test]
    fn string_literals_accept_either_quote_and_lose_both() {
        let compiled = Expression::compile(r#"m(k="wide")"#).expect("compiles");
        let Node::Call { kwargs, .. } = &compiled.root else {
            panic!("expected a call node");
        };
        assert_eq!(kwargs[0].1, KwValue::Str("wide".to_string()));
    }

    // --- KeywordMatcher ---------------------------------------------------

    /// Every row here is a probed pytest answer (see the Task 4 report's `-k` table).
    #[test]
    fn keyword_matching_reproduces_every_probed_node_name() {
        let mut test = test_case("alpha/test_first.py", "test_one");
        test.marks = vec![mark("slow")];
        let matcher = KeywordMatcher::from_test(&test);

        for hit in [
            "one",           // the function name
            "alpha",         // an intermediate directory
            "test_first",    // the module, without the suffix
            "test_first.py", // the module, with it
            "slow",          // a mark name
            "ONE",           // case-insensitive on the needle
        ] {
            assert!(
                matcher.matches(hit, &[]).expect("no kwargs"),
                "expected {hit:?} to match"
            );
        }
        assert!(!matcher.matches("beta", &[]).expect("no kwargs"));
        assert!(!matcher.matches("two", &[]).expect("no kwargs"));
    }

    /// Case-insensitivity runs in both directions: the *names* are lower-cased too, which
    /// a needle-only `to_lowercase` would miss.  Probed: `-k upper` selects `test_UPPER`.
    ///
    /// The **path** carries a capital too, and deliberately: with an all-lowercase tree,
    /// dropping `to_lowercase` from the path branch changes nothing at all, which a mutation
    /// run proved by surviving the first version of this test.
    #[test]
    fn keyword_matching_lowercases_the_names_as_well_as_the_needle() {
        let mut test = test_case("Alpha/test_second.py", "test_UPPER");
        test.marks = vec![mark("Slow")];
        let matcher = KeywordMatcher::from_test(&test);

        for needle in ["upper", "UPPER", "alpha", "ALPHA", "slow", "SLOW"] {
            assert!(
                matcher.matches(needle, &[]).expect("no kwargs"),
                "expected {needle:?} to match"
            );
        }
    }

    /// The `Function` node's name carries the parametrize suffix, which is what makes
    /// `-k "test_param[1]"` address a single case (probed).
    #[test]
    fn the_function_name_carries_the_param_suffix() {
        let mut test = test_case("test_second.py", "test_param");
        test.param_id = Some("1".to_string());
        let matcher = KeywordMatcher::from_test(&test);

        assert!(matcher.matches("test_param[1]", &[]).expect("no kwargs"));
        assert!(matcher.matches("param", &[]).expect("no kwargs"));
        assert!(matcher.matches("1", &[]).expect("no kwargs"));
        assert!(!matcher.matches("test_param[2]", &[]).expect("no kwargs"));
    }

    /// Class nodes come from `qualname`, not from `class_name`, so an enclosing class of a
    /// nested class is a name too.
    #[test]
    fn every_enclosing_class_is_a_name() {
        let mut test = test_case("test_x.py", "Outer.Inner.test_m");
        test.class_name = Some("Inner".to_string());
        let matcher = KeywordMatcher::from_test(&test);

        assert!(matcher.matches("Outer", &[]).expect("no kwargs"));
        assert!(matcher.matches("Inner", &[]).expect("no kwargs"));
        assert!(matcher.matches("test_m", &[]).expect("no kwargs"));
        // The dotted qualname itself is not a node name.
        assert!(!matcher.matches("Outer.Inner", &[]).expect("no kwargs"));
    }

    /// `KeywordMatcher.__call__` raises `UsageError` for any kwargs, whatever the item.
    #[test]
    fn keyword_expressions_refuse_call_parameters() {
        let test = test_case("test_x.py", "test_one");
        let matcher = KeywordMatcher::from_test(&test);
        assert_eq!(
            matcher.matches("one", &[("k".to_string(), KwValue::Int(1))]),
            Err(SelectionError::Usage(
                "Keyword expressions do not support call parameters.".to_string()
            ))
        );
    }

    // --- MarkMatcher ------------------------------------------------------

    #[test]
    fn mark_matching_is_by_name_and_is_exact() {
        let marks = vec![mark("slow")];
        let matcher = MarkMatcher::from_marks(&marks);
        assert!(matcher.matches("slow", &[]).expect("bare"));
        // Substring semantics belong to `-k`; `-m slo` selects nothing.
        assert!(!matcher.matches("slo", &[]).expect("bare"));
        assert!(!matcher.matches("Slow", &[]).expect("bare"));
    }

    /// Probed: `-m "net(scope='wide')"` selects, `-m "net(scope='narrow')"` does not.
    #[test]
    fn mark_kwargs_must_all_match_on_one_mark() {
        let marks = vec![mark_with(
            "net",
            &[("scope", json!("wide")), ("retries", json!(3))],
        )];
        let matcher = MarkMatcher::from_marks(&marks);

        let matches = |pairs: Vec<(String, KwValue)>| matcher.matches("net", &pairs).expect("ok");

        assert!(matches(vec![(
            "scope".to_string(),
            KwValue::Str("wide".into())
        )]));
        assert!(matches(vec![("retries".to_string(), KwValue::Int(3))]));
        assert!(matches(vec![
            ("scope".to_string(), KwValue::Str("wide".into())),
            ("retries".to_string(), KwValue::Int(3)),
        ]));
        assert!(!matches(vec![(
            "scope".to_string(),
            KwValue::Str("narrow".into())
        )]));
        assert!(!matches(vec![("retries".to_string(), KwValue::Int(4))]));
        // One wrong constraint fails the whole mark, even with another that matches.
        assert!(!matches(vec![
            ("scope".to_string(), KwValue::Str("wide".into())),
            ("retries".to_string(), KwValue::Int(4)),
        ]));
    }

    /// `mark.kwargs.get(k, NOT_SET) == v` — an **absent** key never compares equal, and
    /// the case that proves the sentinel is load-bearing is `k=None` against a mark that
    /// simply does not have `k`: a `.get(k)` yielding JSON `null` would say yes.
    #[test]
    fn an_absent_kwarg_never_matches_not_even_none() {
        let marks = vec![mark_with("net", &[("scope", json!("wide"))])];
        let matcher = MarkMatcher::from_marks(&marks);
        assert!(!matcher
            .matches("net", &[("retries".to_string(), KwValue::None)])
            .expect("ok"));
        // Present *and* null does match.
        let explicit = vec![mark_with("net", &[("retries", Value::Null)])];
        assert!(MarkMatcher::from_marks(&explicit)
            .matches("net", &[("retries".to_string(), KwValue::None)])
            .expect("ok"));
    }

    /// `from_markers` builds a *list* per name, and the constraint is satisfied if **any**
    /// of them matches — two `@pytest.mark.net(...)` decorators on one test are two marks.
    #[test]
    fn any_mark_of_the_name_may_satisfy_the_kwargs() {
        let marks = vec![
            mark_with("net", &[("scope", json!("narrow"))]),
            mark_with("net", &[("scope", json!("wide"))]),
        ];
        let matcher = MarkMatcher::from_marks(&marks);
        assert!(matcher
            .matches("net", &[("scope".to_string(), KwValue::Str("wide".into()))])
            .expect("ok"));
        assert!(!matcher
            .matches(
                "net",
                &[("scope".to_string(), KwValue::Str("other".into()))]
            )
            .expect("ok"));
    }

    #[test]
    fn a_mark_expression_never_refuses_call_parameters() {
        let marks = vec![mark("net")];
        assert!(MarkMatcher::from_marks(&marks)
            .matches("net", &[("k".to_string(), KwValue::Int(1))])
            .is_ok());
    }

    // --- deselect ---------------------------------------------------------

    fn corpus() -> Vec<CollectedTest> {
        let mut slow = test_case("alpha/test_first.py", "test_one");
        slow.marks = vec![mark("slow")];
        let plain = test_case("alpha/test_first.py", "test_two");
        let mut smoke = test_case("beta/test_second.py", "TestBox.test_method");
        smoke.class_name = Some("TestBox".to_string());
        smoke.marks = vec![mark("smoke")];
        vec![slow, plain, smoke]
    }

    fn ids(selection: &Selection) -> Vec<&str> {
        selection.kept.iter().map(|test| test.id.as_str()).collect()
    }

    #[test]
    fn no_expressions_keeps_everything_and_deselects_nothing() {
        let selection = deselect(corpus(), None, None).expect("selects");
        assert_eq!(selection.kept.len(), 3);
        assert_eq!(selection.deselected, 0);
    }

    #[test]
    fn keyword_selection_keeps_manifest_order() {
        let selection = deselect(corpus(), Some("test_"), None).expect("selects");
        assert_eq!(
            ids(&selection),
            vec![
                "alpha/test_first.py::test_one",
                "alpha/test_first.py::test_two",
                "beta/test_second.py::TestBox::test_method",
            ]
        );
        assert_eq!(selection.deselected, 0);
    }

    #[test]
    fn deselected_counts_what_was_removed() {
        let selection = deselect(corpus(), Some("one"), None).expect("selects");
        assert_eq!(ids(&selection), vec!["alpha/test_first.py::test_one"]);
        assert_eq!(selection.deselected, 2);
    }

    /// `-k` and `-m` compose, and the count is the **total** removed by both passes — not
    /// the second pass's alone, which is what a naive `before - after` on the last stage
    /// would report.
    #[test]
    fn keyword_and_mark_expressions_compose_and_the_count_is_cumulative() {
        let selection = deselect(corpus(), Some("test_"), Some("slow")).expect("selects");
        assert_eq!(ids(&selection), vec!["alpha/test_first.py::test_one"]);
        assert_eq!(selection.deselected, 2);

        let selection = deselect(corpus(), Some("one"), Some("smoke")).expect("selects");
        assert!(selection.kept.is_empty());
        assert_eq!(selection.deselected, 3);
    }

    /// The `-k` / `-m` emptiness asymmetry, both halves probed against pytest.
    #[test]
    fn empty_k_selects_everything_and_empty_m_selects_nothing() {
        let all = deselect(corpus(), Some("   "), None).expect("selects");
        assert_eq!(all.kept.len(), 3);
        assert_eq!(all.deselected, 0);

        let none = deselect(corpus(), None, Some("   ")).expect("selects");
        assert!(none.kept.is_empty());
        assert_eq!(none.deselected, 3);

        // `-m ""` is falsy and skips filtering entirely.
        let skipped = deselect(corpus(), None, Some("")).expect("selects");
        assert_eq!(skipped.kept.len(), 3);
        assert_eq!(skipped.deselected, 0);
    }

    #[test]
    fn a_bad_expression_names_the_flag_that_carried_it() {
        let err = deselect(corpus(), Some("and and"), None).expect_err("invalid");
        assert!(err
            .to_string()
            .starts_with("Wrong expression passed to '-k'"));

        let err = deselect(corpus(), None, Some("m(")).expect_err("invalid");
        assert!(err
            .to_string()
            .starts_with("Wrong expression passed to '-m'"));
    }

    /// `-k "x(y=1)"` is a *runtime* refusal, so it needs an item to fire — with nothing
    /// collected there is nothing to evaluate against and pytest exits 5 rather than 4.
    #[test]
    fn keyword_call_parameters_only_fail_when_there_is_something_to_match() {
        assert!(deselect(Vec::new(), Some("x(y=1)"), None).is_ok());
        assert_eq!(
            deselect(corpus(), Some("x(y=1)"), None).expect_err("usage"),
            SelectionError::Usage(
                "Keyword expressions do not support call parameters.".to_string()
            )
        );
    }

    /// Short-circuiting is observable through the error: `-k "one and x(y=1)"` never
    /// evaluates the right-hand side for a test whose name has no `one`, so the corpus
    /// entry that *does* match is the one that raises.
    #[test]
    fn boolean_short_circuit_decides_whether_a_refusal_is_reached() {
        let just_two = vec![test_case("test_x.py", "test_two")];
        assert!(deselect(just_two, Some("one and x(y=1)"), None).is_ok());

        let just_one = vec![test_case("test_x.py", "test_one")];
        assert!(deselect(just_one, Some("one and x(y=1)"), None).is_err());
    }
}
