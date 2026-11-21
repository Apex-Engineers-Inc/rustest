# Why automated testing?

If you have never written a test before, start here. This page is about what automated
testing buys you and why it is worth the effort.

## The problem: bugs happen

When you write code, bugs will happen. The question is not whether, but when and how many.

Maybe you:
- Added a new feature and accidentally broke an existing one
- Fixed a bug in one place, but created a new bug somewhere else
- Changed a function and forgot it was used in 5 different places
- Deployed code that worked on your machine, but crashed in production

**This is normal.** Even experienced developers make mistakes. The question is: how do you catch them?

## The old way: manual testing

The traditional approach is manual testing:

1. Write some code
2. Run your program
3. Click through the UI or run some commands
4. Check if everything works
5. Repeat... and repeat... and repeat...

This works, but it has problems:

- **It's slow.** Testing takes time, and you have to do it over and over
- **It's boring.** Clicking the same buttons gets tedious fast
- **It's error-prone.** You might forget to test something important
- **It doesn't scale.** As your code grows, manual testing becomes impossible
- **It's not repeatable.** Six months later, will you remember all the edge cases?

## The better way: automated testing

Your computer can do the checking for you. You write **test code** that exercises your
**real code** and reports whether it behaved.

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
1 passed in 0.40s
```

That took less than half a second, with no clicking and no manual checking.

## What automated tests give you

### Confidence to change code

With tests, you can refactor code and immediately know if you broke something:

```python
from types import SimpleNamespace

def register_user(email, password):
    return SimpleNamespace(email=email, is_active=True)

def test_user_registration():
    user = register_user("alice@example.com", "password123")
    assert user.email == "alice@example.com"
    assert user.is_active is True
```

Now you can safely change your registration logic. If the test still passes, you didn't break anything.

### Bugs caught before your users see them

Tests catch bugs during development, not in production:

```python
from rustest import raises

def test_divide_by_zero():
    with raises(ZeroDivisionError):
        result = 10 / 0
```

This test *expects* an error. If your code handles it properly, the test passes. If not, the
test fails and you fix it before shipping.

### Documentation that never lies

Tests show exactly how your code should be used:

```python
from types import SimpleNamespace

def send_email(to, subject, body):
    return SimpleNamespace(success=True)

def test_send_email():
    # This test shows how to use send_email()
    result = send_email(
        to="user@example.com",
        subject="Welcome!",
        body="Thanks for signing up"
    )
    assert result.success is True
```

Comments can become outdated. Tests are **executable documentation**: if they pass, they're accurate.

### A fast feedback loop

Instead of manually testing everything, you get feedback in a second or two:

```bash
$ rustest
================================== FAILURES ===================================
______________________ test_login_with_invalid_password _______________________
Traceback (most recent call last):
  File "/path/to/test_login.py", line 10, in test_login_with_invalid_password
    assert result.message == "Invalid password"
AssertionError: assert 'User not found' == 'Invalid password'

  - Invalid password
  + User not found
=========================== short test summary info ===========================
FAILED test_login.py::test_login_with_invalid_password

1 failed, 4 passed in 0.33s
```

You immediately see what broke and where. Note the last two lines of the error: rustest
rewrites your assertion so a string mismatch comes back as a **diff** (`-` is what you
expected, `+` is what you got) rather than as a bare `AssertionError`. The other four tests
still ran, because one failure does not stop the suite.

### Fewer late-night surprises

Knowing your code is tested means:
- Fewer production bugs
- Easier to add new features
- Safer to refactor old code
- Less stress when deploying

## The developer workflow

Here's how testing fits into your development:

1. **Write a test** that describes what you want your code to do
2. **Run the test.** It fails, because your code doesn't exist yet
3. **Write the code** to make the test pass
4. **Run the test again.** It passes
5. **Refactor** if needed, with the test there to catch mistakes

This is called **Test-Driven Development (TDD)**, and many developers like it because:
- You write better code (more modular, easier to test)
- You think about edge cases upfront
- You get instant feedback

You don't have to use TDD. Writing tests *after* your code is still worth doing.

## Real-world example

Imagine you're building a shopping cart:

```python
from rustest import fixture

class ShoppingCart:
    def __init__(self):
        self.lines = []
        self.discount = 0.0

    def add_item(self, name, price, quantity=1):
        self.lines.append((name, price, quantity))

    def remove_item(self, name):
        self.lines = [line for line in self.lines if line[0] != name]

    def apply_discount(self, fraction):
        self.discount = fraction

    @property
    def total(self):
        subtotal = sum(price * quantity for _, price, quantity in self.lines)
        return subtotal * (1 - self.discount)

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

## Common concerns

### "Writing tests takes too long"

At first, yes. You will get faster. And weigh it against the alternative:
- How long does manual testing take?
- How long does fixing production bugs take?
- How long does it take to track down a bug you introduced 2 weeks ago?

**Tests save time in the long run.**

### "My code is simple, I don't need tests"

Even simple code can have bugs. And simple code grows into complex code. Starting with tests is easier than adding them later.

### "I'll write tests later"

We all say this. It rarely happens. The best time to write tests is **now**, when the code is fresh in your mind.

## What's next?

Write one:

[Write Your First Test](intro-first-test.md)

Or read the fundamentals first:

[Testing Basics](intro-testing-basics.md)
