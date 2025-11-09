//! Mark expression parser and evaluator.
//!
//! This module implements a simple parser for pytest-style mark expressions,
//! supporting boolean operations (and, or, not) and parentheses for grouping.
//!
//! Examples:
//! - "slow" - test must have the "slow" mark
//! - "not slow" - test must not have the "slow" mark
//! - "slow and integration" - test must have both marks
//! - "slow or fast" - test must have either mark
//! - "(slow or fast) and not integration" - complex expression with grouping

use crate::model::Mark;

/// A mark expression that can be evaluated against a list of marks.
#[derive(Debug, Clone, PartialEq)]
pub enum MarkExpr {
    /// A single mark name (e.g., "slow")
    Name(String),
    /// Logical NOT (e.g., "not slow")
    Not(Box<MarkExpr>),
    /// Logical AND (e.g., "slow and fast")
    And(Box<MarkExpr>, Box<MarkExpr>),
    /// Logical OR (e.g., "slow or fast")
    Or(Box<MarkExpr>, Box<MarkExpr>),
}

impl MarkExpr {
    /// Parse a mark expression from a string.
    pub fn parse(input: &str) -> Result<Self, String> {
        let mut parser = Parser::new(input);
        let expr = parser.parse_or()?;
        if parser.current().is_some() {
            return Err(format!(
                "Unexpected token after expression: {:?}",
                parser.current()
            ));
        }
        Ok(expr)
    }

    /// Evaluate this expression against a list of marks.
    pub fn matches(&self, marks: &[Mark]) -> bool {
        match self {
            MarkExpr::Name(name) => marks.iter().any(|m| &m.name == name),
            MarkExpr::Not(expr) => !expr.matches(marks),
            MarkExpr::And(left, right) => left.matches(marks) && right.matches(marks),
            MarkExpr::Or(left, right) => left.matches(marks) || right.matches(marks),
        }
    }
}

/// Tokens for the mark expression parser.
#[derive(Debug, Clone, PartialEq)]
enum Token {
    Name(String),
    Not,
    And,
    Or,
    LParen,
    RParen,
}

/// A simple lexer for mark expressions.
struct Lexer {
    input: Vec<char>,
    pos: usize,
}

impl Lexer {
    fn new(input: &str) -> Self {
        Self {
            input: input.chars().collect(),
            pos: 0,
        }
    }

    fn skip_whitespace(&mut self) {
        while self.pos < self.input.len() && self.input[self.pos].is_whitespace() {
            self.pos += 1;
        }
    }

    fn read_name(&mut self) -> String {
        let start = self.pos;
        while self.pos < self.input.len() {
            let ch = self.input[self.pos];
            if ch.is_alphanumeric() || ch == '_' || ch == '-' {
                self.pos += 1;
            } else {
                break;
            }
        }
        self.input[start..self.pos].iter().collect()
    }

    fn next_token(&mut self) -> Option<Token> {
        self.skip_whitespace();
        if self.pos >= self.input.len() {
            return None;
        }

        let ch = self.input[self.pos];
        match ch {
            '(' => {
                self.pos += 1;
                Some(Token::LParen)
            }
            ')' => {
                self.pos += 1;
                Some(Token::RParen)
            }
            _ if ch.is_alphabetic() || ch == '_' => {
                let name = self.read_name();
                Some(match name.as_str() {
                    "not" => Token::Not,
                    "and" => Token::And,
                    "or" => Token::Or,
                    _ => Token::Name(name),
                })
            }
            _ => {
                self.pos += 1;
                None // Skip unknown characters
            }
        }
    }

    fn tokenize(&mut self) -> Vec<Token> {
        let mut tokens = Vec::new();
        while let Some(token) = self.next_token() {
            tokens.push(token);
        }
        tokens
    }
}

/// A recursive descent parser for mark expressions.
struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    fn new(input: &str) -> Self {
        let mut lexer = Lexer::new(input);
        let tokens = lexer.tokenize();
        Self { tokens, pos: 0 }
    }

    fn current(&self) -> Option<&Token> {
        self.tokens.get(self.pos)
    }

    fn advance(&mut self) -> Option<Token> {
        if self.pos < self.tokens.len() {
            let token = self.tokens[self.pos].clone();
            self.pos += 1;
            Some(token)
        } else {
            None
        }
    }

    /// Parse an OR expression (lowest precedence).
    fn parse_or(&mut self) -> Result<MarkExpr, String> {
        let mut left = self.parse_and()?;
        while matches!(self.current(), Some(Token::Or)) {
            self.advance();
            let right = self.parse_and()?;
            left = MarkExpr::Or(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    /// Parse an AND expression (medium precedence).
    fn parse_and(&mut self) -> Result<MarkExpr, String> {
        let mut left = self.parse_not()?;
        while matches!(self.current(), Some(Token::And)) {
            self.advance();
            let right = self.parse_not()?;
            left = MarkExpr::And(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    /// Parse a NOT expression (high precedence).
    fn parse_not(&mut self) -> Result<MarkExpr, String> {
        if matches!(self.current(), Some(Token::Not)) {
            self.advance();
            let expr = self.parse_not()?;
            Ok(MarkExpr::Not(Box::new(expr)))
        } else {
            self.parse_primary()
        }
    }

    /// Parse a primary expression (name or parenthesized expression).
    fn parse_primary(&mut self) -> Result<MarkExpr, String> {
        match self.advance() {
            Some(Token::Name(name)) => Ok(MarkExpr::Name(name)),
            Some(Token::LParen) => {
                let expr = self.parse_or()?;
                match self.advance() {
                    Some(Token::RParen) => Ok(expr),
                    _ => Err("Expected ')'".to_string()),
                }
            }
            Some(token) => Err(format!("Unexpected token: {:?}", token)),
            None => Err("Unexpected end of input".to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyList};

    fn create_mark(name: &str) -> Mark {
        Python::with_gil(|py| {
            Mark::new(
                name.to_string(),
                PyList::empty(py).unbind(),
                PyDict::new(py).unbind(),
            )
        })
    }

    #[test]
    fn test_parse_simple_name() {
        let expr = MarkExpr::parse("slow").unwrap();
        assert_eq!(expr, MarkExpr::Name("slow".to_string()));
    }

    #[test]
    fn test_parse_not() {
        let expr = MarkExpr::parse("not slow").unwrap();
        assert_eq!(
            expr,
            MarkExpr::Not(Box::new(MarkExpr::Name("slow".to_string())))
        );
    }

    #[test]
    fn test_parse_and() {
        let expr = MarkExpr::parse("slow and fast").unwrap();
        assert_eq!(
            expr,
            MarkExpr::And(
                Box::new(MarkExpr::Name("slow".to_string())),
                Box::new(MarkExpr::Name("fast".to_string()))
            )
        );
    }

    #[test]
    fn test_parse_or() {
        let expr = MarkExpr::parse("slow or fast").unwrap();
        assert_eq!(
            expr,
            MarkExpr::Or(
                Box::new(MarkExpr::Name("slow".to_string())),
                Box::new(MarkExpr::Name("fast".to_string()))
            )
        );
    }

    #[test]
    fn test_parse_complex() {
        let expr = MarkExpr::parse("(slow or fast) and not integration").unwrap();
        assert_eq!(
            expr,
            MarkExpr::And(
                Box::new(MarkExpr::Or(
                    Box::new(MarkExpr::Name("slow".to_string())),
                    Box::new(MarkExpr::Name("fast".to_string()))
                )),
                Box::new(MarkExpr::Not(Box::new(MarkExpr::Name(
                    "integration".to_string()
                ))))
            )
        );
    }

    #[test]
    fn test_matches_simple() {
        let expr = MarkExpr::parse("slow").unwrap();
        let marks = vec![create_mark("slow"), create_mark("integration")];
        assert!(expr.matches(&marks));

        let marks = vec![create_mark("fast")];
        assert!(!expr.matches(&marks));
    }

    #[test]
    fn test_matches_not() {
        let expr = MarkExpr::parse("not slow").unwrap();
        let marks = vec![create_mark("fast")];
        assert!(expr.matches(&marks));

        let marks = vec![create_mark("slow")];
        assert!(!expr.matches(&marks));
    }

    #[test]
    fn test_matches_and() {
        let expr = MarkExpr::parse("slow and integration").unwrap();
        let marks = vec![create_mark("slow"), create_mark("integration")];
        assert!(expr.matches(&marks));

        let marks = vec![create_mark("slow")];
        assert!(!expr.matches(&marks));
    }

    #[test]
    fn test_matches_or() {
        let expr = MarkExpr::parse("slow or fast").unwrap();
        let marks = vec![create_mark("slow")];
        assert!(expr.matches(&marks));

        let marks = vec![create_mark("fast")];
        assert!(expr.matches(&marks));

        let marks = vec![create_mark("integration")];
        assert!(!expr.matches(&marks));
    }
}
