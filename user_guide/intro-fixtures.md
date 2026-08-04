# Making Tests Reusable with Fixtures

As you write more tests, you'll notice yourself copying the same setup code over and over. Fixtures solve this problem by letting you **define setup once and reuse it everywhere**.

## The Problem: Repetitive Setup

Imagine you're testing a shopping cart:

```python
class ShoppingCart:
    def __init__(self):
        self.lines = []

    def add_item(self, name, price):
        self.lines.append((name, price))

    def remove_item(self, name):
        self.lines = [line for line in self.lines if line[0] != name]

    @property
    def total(self):
        return sum(price for _, price in self.lines)

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

Every test builds its own `ShoppingCart()`. Three copies of one line today, thirty tomorrow.

## The Solution: Fixtures

A **fixture** is a reusable piece of setup code:

```python
from rustest import fixture

class ShoppingCart:
    def __init__(self):
        self.lines = []

    def add_item(self, name, price):
        self.lines.append((name, price))

    def remove_item(self, name):
        self.lines = [line for line in self.lines if line[0] != name]

    @property
    def total(self):
        return sum(price for _, price in self.lines)

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

The setup exists in one place now.

## How Fixtures Work

When you run a test that uses a fixture:

1. **Rustest sees** the test needs the `cart` fixture
2. **Rustest calls** the `cart()` function
3. **Rustest passes** the result to your test function
4. **Your test runs** with the cart

The parameter name is the whole wiring mechanism, which is why it has to match the fixture's name.

## Fixture Benefits

### Less code duplication

Define setup once, use it everywhere:

```python
from rustest import fixture

class Database:
    def __init__(self):
        self.rows = {}

    def connect(self):
        self.connected = True

    def insert(self, table, row):
        self.rows.setdefault(table, []).append(row)

    def count(self, table):
        return len(self.rows.get(table, []))

    def query(self, table):
        return self.rows.get(table, [])

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

### Easier maintenance

Change setup in one place, all tests update:

<!--rustest.mark.skip-->
```python
from rustest import fixture

@fixture
def database():
    # Changed from SQLite to PostgreSQL?
    # Update it here, and all tests still work!
    db = PostgresDatabase()
    db.connect("test_db")
    return db
```

### Clearer tests

Tests focus on what they're testing, not setup details:

<!--rustest.mark.skip-->
```python
def test_user_login(database, user):
    # The test is clear: we're testing login
    result = login(user.email, user.password)
    assert result.success is True
```

## Real-World Example: Testing an API

Here is the same idea against an API client:

```python
from types import SimpleNamespace
from rustest import fixture

class APIClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def authenticate(self, token):
        self.token = token

    def get(self, path):
        return {"name": "Alice"}

    def post(self, path, payload):
        return {"id": 1, **payload}

    def delete(self, path):
        return SimpleNamespace(success=True)

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

Every test gets a fresh, authenticated API client without any setup code.

## Cleanup with Yield Fixtures

Sometimes you need to clean up after tests (close files, disconnect from databases, and so on). Use `yield`:

```python
from rustest import fixture

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

Cleanup happens whether the test passed or failed.

## Built-in Fixtures

Rustest provides useful fixtures out of the box. Three you'll reach for constantly:

### tmp_path: Temporary Directory

```python
def test_create_file(tmp_path):
    # tmp_path is a Path object to a temporary directory
    file = tmp_path / "test.txt"
    file.write_text("hello world")

    assert file.read_text() == "hello world"
    # The whole temporary tree is removed when the run ends
```

Each test gets its own directory, named after the test, so a failing run leaves something
readable behind until the session finishes.

### monkeypatch: Modify Things Temporarily

```python
import os

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

There are more: `tmp_path_factory`, `tmpdir`, `tmpdir_factory`, `capfd`, `caplog`, `cache`,
`mocker`, `pytestconfig` and `recwarn`. The [fixtures guide](fixtures.md) covers them.

## Fixtures Can Use Other Fixtures

Fixtures can depend on other fixtures:

```python
from types import SimpleNamespace
from rustest import fixture

class Database:
    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def create_user(self, email):
        return SimpleNamespace(email=email)

    def create_post(self, author, title):
        return SimpleNamespace(author=author, title=title)

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

Rustest resolves the dependencies and runs the fixtures in the order they require.

## Common Patterns

### Fixture for Test Data

```python
from rustest import fixture

class Database:
    def __init__(self):
        self.rows = {}

    def import_users(self, users):
        self.rows.setdefault("users", []).extend(users)

    def count(self, table):
        return len(self.rows.get(table, []))

@fixture
def database():
    return Database()

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
from types import SimpleNamespace
from rustest import fixture

def create_app(config):
    return SimpleNamespace(is_debug=config["debug"])

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
from types import SimpleNamespace

# The collaborator your code calls out to
emails = SimpleNamespace(send=lambda to, subject, body: None)

def signup(email, password):
    emails.send(to=email, subject="Welcome!", body="Thanks for signing up")

@fixture
def mock_email_service(monkeypatch):
    sent_emails = []

    def fake_send_email(to, subject, body):
        sent_emails.append({"to": to, "subject": subject})

    monkeypatch.setattr(emails, "send", fake_send_email)
    return sent_emails

def test_signup_sends_email(mock_email_service):
    signup("alice@example.com", "password")
    assert len(mock_email_service) == 1
    assert mock_email_service[0]["subject"] == "Welcome!"
```

## When to Use Fixtures

Use fixtures when you:

- Have the same setup in multiple tests
- Need to clean up resources (files, connections, and the like)
- Want to share test data across tests
- Need complex setup that would clutter your tests

Skip them when:

- The setup is used in only one test (just put it in the test)
- The fixture would be more confusing than helpful

## What's Next?

Now that setup lives in one place, put the same test through many inputs:

[Testing Multiple Cases (Parametrization)](intro-parametrization.md)

Or see how to organize larger test suites:

[Organizing Your Tests](intro-organizing.md)

For scopes, autouse, `conftest.py` and the rest of the fixture machinery:

[Fixtures Guide](fixtures.md)
