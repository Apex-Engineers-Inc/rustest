Each test body runs in its **own** `contextvars.Context`, and a fixture's writes still
reach the tests it serves. Two halves of one rule, and they pull in opposite
directions — which is why both are in one case.

`PytestAsyncioFunction.runtest` (l. 465-473) builds `contextvars.copy_context()` per
item and hands it to `_synchronize_coroutine` (l. 708-723) as
`runner.run(coro, context=context)`. Without it, every test sharing a runner shares
one context — the runner's own — so a `ContextVar` set in one test is still set in the
next. Measured before the fix on exactly this ini (the acceptance shape): pytest
printed `unset` for the second test, rustest printed `from-test-one`.

Isolating the test body alone then breaks the other direction, because a fixture's
writes land in the runner's context and a fresh copy of the *caller's* context does
not contain them. The oracle solves this with `_apply_contextvar_changes` (l. 385-414),
which replays a fixture's changes into the caller's context — its own comment notes a
fixture author cannot do this themselves, "because they have no way to capture the
Context in which the setup function was run". Ported alongside; measured going from
`from-fixture` to `unset` and back.

The shape matters because `ContextVar` is where async libraries keep request state:
structlog's bound context, SQLAlchemy's async session registry, OpenTelemetry's
current span. A leak travels *forward*, so the symptom lands on a test that did
nothing wrong.
