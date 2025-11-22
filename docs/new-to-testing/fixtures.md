# Making Tests Reusable with Fixtures

As you write more tests, you'll notice yourself copying the same setup code over and over. Fixtures solve this problem by letting you **define setup once and reuse it everywhere**.

## The Problem: Repetitive Setup

Imagine you're testing a shopping cart:

```python
def test_add_item():
    cart = ShoppingCart()  # Same setup
    cart.add_item("Apple", 1.50)
    assert cart.total == 1.50

def test_remove_item():
    cart = ShoppingCart()  # Same setup again
    cart.add_item("Apple", 1.50)
    cart.remove_item("Apple")
    assert cart.total == 0.00

def test_multiple_items():
    cart = ShoppingCart()  # And again...
    cart.add_item("Apple", 1.50)
    cart.add_item("Banana", 0.75)
    assert cart.total == 2.25
```

See the pattern? Every test creates a `ShoppingCart()`. This is repetitive and annoying.

## The Solution: Fixtures

A **fixture** is a reusable piece of setup code:

```python
from rustest import fixture

@fixture
def cart():
    return ShoppingCart()

def test_add_item(cart):
    cart.add_item("Apple", 1.50)
    assert cart.total == 1.50

def test_remove_item(cart):
    cart.add_item("Apple", 1.50)
    cart.remove_item("Apple")
    assert cart.total == 0.00

def test_multiple_items(cart):
    cart.add_item("Apple", 1.50)
    cart.add_item("Banana", 0.75)
    assert cart.total == 2.25
```

**What happened?**

1. We defined `cart` as a fixture using `@fixture`
2. Each test function accepts `cart` as a parameter
3. Rustest automatically **calls the fixture** and **passes the result** to your test

No more repetitive setup! 🎉

## How Fixtures Work

When you run a test that uses a fixture:

1. **Rustest sees** the test needs the `cart` fixture
2. **Rustest calls** the `cart()` function
3. **Rustest passes** the result to your test function
4. **Your test runs** with the cart

It's like automatic dependency injection!

## Fixture Benefits

### ✅ Less Code Duplication

Define setup once, use it everywhere:

```python
@fixture
def database():
    db = Database()
    db.connect()
    return db

# Now every test can use database without repeating setup
def test_insert_user(database):
    database.insert("users", {"name": "Alice"})
    assert database.count("users") == 1

def test_query_users(database):
    database.insert("users", {"name": "Alice"})
    users = database.query("users")
    assert len(users) == 1
```

### ✅ Easier Maintenance

Change setup in one place, all tests update:

```python
@fixture
def database():
    # Changed from SQLite to PostgreSQL?
    # Update it here, and all tests still work!
    db = PostgresDatabase()
    db.connect("test_db")
    return db
```

### ✅ Clearer Tests

Tests focus on what they're testing, not setup details:

```python
def test_user_login(database, user):
    # The test is clear: we're testing login
    result = login(user.email, user.password)
    assert result.success is True
```

## Real-World Example: Testing an API

Let's test an API client:

```python
from rustest import fixture

@fixture
def api_client():
    client = APIClient("https://api.example.com")
    client.authenticate("test_token")
    return client

def test_get_user(api_client):
    user = api_client.get("/users/1")
    assert user["name"] == "Alice"

def test_create_post(api_client):
    post = api_client.post("/posts", {"title": "Hello World"})
    assert post["id"] is not None

def test_delete_resource(api_client):
    result = api_client.delete("/posts/123")
    assert result.success is True
```

Every test gets a fresh, authenticated API client without any setup code!

## Cleanup with Yield Fixtures

Sometimes you need to clean up after tests (close files, disconnect from databases, etc.). Use `yield`:

```python
@fixture
def temp_file():
    # SETUP: Create a file
    file = open("test.txt", "w")
    file.write("test data")
    file.close()

    # PROVIDE: Give the filename to the test
    yield "test.txt"

    # CLEANUP: Delete the file after the test
    import os
    os.remove("test.txt")

def test_read_file(temp_file):
    with open(temp_file, "r") as f:
        content = f.read()
    assert content == "test data"
    # After this test, temp_file is automatically deleted!
```

**How it works:**

1. Code before `yield` runs **before the test**
2. The value after `yield` is **passed to the test**
3. Code after `yield` runs **after the test** (cleanup)

This ensures cleanup always happens, even if the test fails!

## Built-in Fixtures

Rustest provides useful fixtures out of the box:

### tmp_path: Temporary Directory

```python
def test_create_file(tmp_path):
    # tmp_path is a Path object to a temporary directory
    file = tmp_path / "test.txt"
    file.write_text("hello world")

    assert file.read_text() == "hello world"
    # Directory is automatically cleaned up after the test!
```

### monkeypatch: Modify Things Temporarily

```python
def test_with_env_var(monkeypatch):
    # Set an environment variable just for this test
    monkeypatch.setenv("API_KEY", "test_key_123")

    # Your code that reads API_KEY will see "test_key_123"
    assert os.getenv("API_KEY") == "test_key_123"
    # After the test, the environment is restored!
```

### capsys: Capture Printed Output

```python
def test_print_message(capsys):
    print("Hello, World!")

    captured = capsys.readouterr()
    assert captured.out == "Hello, World!\n"
```

## Fixtures Can Use Other Fixtures

Fixtures can depend on other fixtures:

```python
@fixture
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()

@fixture
def user(database):
    # This fixture uses the database fixture!
    user = database.create_user("alice@example.com")
    return user

def test_user_posts(database, user):
    # This test uses both fixtures
    post = database.create_post(user, "Hello World")
    assert post.author == user
```

Rustest automatically resolves dependencies and runs fixtures in the right order.

## Common Patterns

### Fixture for Test Data

```python
@fixture
def sample_users():
    return [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
    ]

def test_import_users(sample_users, database):
    database.import_users(sample_users)
    assert database.count("users") == 2
```

### Fixture for Configuration

```python
@fixture
def test_config():
    return {
        "debug": True,
        "database_url": "sqlite:///test.db",
        "api_key": "test_key",
    }

def test_app_startup(test_config):
    app = create_app(test_config)
    assert app.is_debug is True
```

### Fixture for Mocks

```python
from rustest import fixture

@fixture
def mock_email_service(monkeypatch):
    sent_emails = []

    def fake_send_email(to, subject, body):
        sent_emails.append({"to": to, "subject": subject})

    monkeypatch.setattr("email.send", fake_send_email)
    return sent_emails

def test_signup_sends_email(mock_email_service):
    signup("alice@example.com", "password")
    assert len(mock_email_service) == 1
    assert mock_email_service[0]["subject"] == "Welcome!"
```

## When to Use Fixtures

Use fixtures when you:

- ✅ Have the same setup in multiple tests
- ✅ Need to clean up resources (files, connections, etc.)
- ✅ Want to share test data across tests
- ✅ Need complex setup that would clutter your tests

Don't use fixtures when:

- ❌ The setup is used in only one test (just put it in the test)
- ❌ The fixture would be more confusing than helpful

## What's Next?

Fixtures make your tests cleaner and more maintainable. Next, learn how to test the same logic with many different inputs:

[:octicons-arrow-right-24: Testing Multiple Cases (Parametrization)](parametrization.md){ .md-button .md-button--primary }

Or explore how to organize larger test suites:

[:octicons-arrow-right-24: Organizing Your Tests](organizing.md){ .md-button }

Want to dive deeper into fixtures?

[:octicons-arrow-right-24: Advanced Fixtures Guide](../guide/fixtures.md){ .md-button }
