# Why Automated Testing?

Welcome! If you're new to testing, you're in the right place. Let's talk about why automated testing exists and why it's worth your time.

## The Problem: Bugs Happen

When you write code, bugs will happen. It's not a matter of "if"—it's "when" and "how many."

Maybe you:
- Added a new feature and accidentally broke an existing one
- Fixed a bug in one place, but created a new bug somewhere else
- Changed a function and forgot it was used in 5 different places
- Deployed code that worked on your machine, but crashed in production

**This is normal.** Even experienced developers make mistakes. The question is: how do you catch them?

## The Old Way: Manual Testing

The traditional approach is manual testing:

1. Write some code
2. Run your program
3. Click through the UI or run some commands
4. Check if everything works
5. Repeat... and repeat... and repeat...

This works, but it has problems:

- ⏰ **It's slow** — Testing takes time, and you have to do it over and over
- 😴 **It's boring** — Clicking the same buttons gets tedious fast
- 🧠 **It's error-prone** — You might forget to test something important
- 📈 **It doesn't scale** — As your code grows, manual testing becomes impossible
- 🚫 **It's not repeatable** — Six months later, will you remember all the edge cases?

## The Better Way: Automated Testing

What if your computer could test your code for you?

That's what automated testing does. You write **test code** that checks your **real code** automatically.

```python
# Your real code
def add(a, b):
    return a + b

# Your test code
def test_add():
    result = add(2, 3)
    assert result == 5  # Check that it worked!
```

Now you can run this test anytime:

```bash
$ rustest
✓

✓ 1/1 1 passing (1ms)
```

**In one second, your computer verified your code works.** No clicking, no manual checking—just instant feedback.

## What Automated Tests Give You

### 🛡️ Confidence to Change Code

With tests, you can refactor code and immediately know if you broke something:

```python
def test_user_registration():
    user = register_user("alice@example.com", "password123")
    assert user.email == "alice@example.com"
    assert user.is_active is True
```

Now you can safely change your registration logic. If the test still passes, you didn't break anything!

### 🐛 Catch Bugs Before Your Users Do

Tests catch bugs during development, not in production:

```python
def test_divide_by_zero():
    with raises(ZeroDivisionError):
        result = 10 / 0
```

This test *expects* an error. If your code handles it properly, great! If not, the test fails and you fix it before shipping.

### 📚 Documentation That Never Lies

Tests show exactly how your code should be used:

```python
def test_send_email():
    # This test shows how to use send_email()
    result = send_email(
        to="user@example.com",
        subject="Welcome!",
        body="Thanks for signing up"
    )
    assert result.success is True
```

Comments can become outdated. Tests are **executable documentation**—if they pass, they're accurate.

### 🏃 Fast Feedback Loop

Instead of manually testing everything, you get instant feedback:

```bash
$ rustest
✓✓✓✗✓

FAILURES
test_login_with_invalid_password (test_login.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError
  Expected: "Invalid password"
  Received: "User not found"

✗ 5/5 4 passing, 1 failed (15ms)
```

You immediately see what broke and where.

### 😴 Sleep Better at Night

Knowing your code is tested means:
- Fewer production bugs
- Easier to add new features
- Safer to refactor old code
- Less stress when deploying

## The Developer Workflow

Here's how testing fits into your development:

1. **Write a test** that describes what you want your code to do
2. **Run the test** — it fails (your code doesn't exist yet!)
3. **Write the code** to make the test pass
4. **Run the test again** — it passes! ✓
5. **Refactor** if needed — the test ensures you don't break anything

This is called **Test-Driven Development (TDD)**, and many developers love it because:
- You write better code (more modular, easier to test)
- You think about edge cases upfront
- You get instant feedback

But you don't have to use TDD. Even writing tests *after* your code is incredibly valuable.

## Real-World Example

Imagine you're building a shopping cart:

```python
from rustest import fixture

@fixture
def cart():
    return ShoppingCart()

def test_add_item_to_cart(cart):
    cart.add_item("Apple", price=1.50, quantity=3)
    assert cart.total == 4.50

def test_remove_item_from_cart(cart):
    cart.add_item("Apple", price=1.50, quantity=3)
    cart.remove_item("Apple")
    assert cart.total == 0.00

def test_cart_applies_discount(cart):
    cart.add_item("Laptop", price=1000.00)
    cart.apply_discount(0.10)  # 10% off
    assert cart.total == 900.00
```

Now:
- When you change the discount logic, these tests tell you if you broke anything
- When you add a new feature (gift cards?), you can write tests first
- When a bug is reported, you write a test that reproduces it, then fix it

## Common Concerns

### "Writing tests takes too long"

At first, yes. But you'll get faster. And consider the alternative:
- How long does manual testing take?
- How long does fixing production bugs take?
- How long does it take to track down a bug you introduced 2 weeks ago?

**Tests save time in the long run.**

### "My code is simple, I don't need tests"

Even simple code can have bugs. And simple code grows into complex code. Starting with tests is easier than adding them later.

### "I'll write tests later"

We all say this. It rarely happens. The best time to write tests is **now**, when the code is fresh in your mind.

## What's Next?

Ready to write your first test? Let's do it!

[:octicons-arrow-right-24: Write Your First Test](first-test.md){ .md-button .md-button--primary }

Or if you want to understand the fundamentals first:

[:octicons-arrow-right-24: Testing Basics](testing-basics.md)
