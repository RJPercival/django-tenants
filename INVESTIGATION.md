# Investigation: Test Hanging Issue

## What We KNOW (with evidence)

### Observation 1: Tests hang when run without the autocommit fix
**Evidence**: Test process stuck at "Creating test database" for 30+ seconds (timestamp: 2025-11-11T00:33:40 to 2025-11-11T00:34:12)
```
Creating test database for alias 'default'...
Creating test database for alias 'other'...
[hangs here]
```

### Observation 2: PostgreSQL shows connection in "idle in transaction" state during hang
**Evidence**: Query output from pg_stat_activity:
```
pid  |           datname           |        state        |      query           | wait_event
-----+-----------------------------+---------------------+----------------------+-------------
45614| test_dts_test_project       | idle in transaction | CREATE SCHEMA "test" | ClientRead
45623| test_dts_test_project       | active              | CREATE SCHEMA "test" | transactionid
```
- PID 45614: Has executed CREATE SCHEMA but transaction not committed, sitting idle
- PID 45623: Trying to execute CREATE SCHEMA but blocked waiting for transaction lock held by 45614

### Observation 3: Killing the blocking connection allows tests to proceed
**Evidence**: After `pg_terminate_backend(45614)`, test 08dbcb completed (took 1342s total waiting time)

### Observation 4: Tests pass successfully with autocommit fix
**Evidence**: Full test suite (38 tests) passed in 8.551s with the autocommit fix applied
```
Ran 38 tests in 8.551s
OK
```

### Observation 5: Issue #1 (missing super() calls) is separate and resolved
**Evidence**:
- Commit 68a1f00 added missing `super().setUpClass()` calls
- This fixed the `ConnectionDoesNotExist: The connection '_' doesn't exist` error
- Tests using `databases = '__all__'` now work correctly

### Observation 6: CREATE SCHEMA executed in TenantMixin.save()
**Evidence**: Code in django_tenants/models.py:131
```python
if has_schema and is_new and self.auto_create_schema:
    try:
        self.create_schema(check_if_exists=True, verbosity=verbosity)
```

### Observation 7: create_schema() is called from TenantTestCase.setUpClass()
**Evidence**: Code in django_tenants/test/cases.py:49
```python
cls.tenant.save(verbosity=cls.get_verbosity())
```

### Observation 8: Django's TestCase.setUpClass() enters atomic blocks
**Evidence**: Django source code
```python
@classmethod
def setUpClass(cls):
    super().setUpClass()
    if not (cls._databases_support_transactions() and cls._databases_support_savepoints()):
        return
    cls.cls_atomics = cls._enter_atomics()  # <-- Creates atomic blocks
```

## ROOT CAUSE IDENTIFIED

### The Problem: Self-Deadlock from Duplicate Database Aliases

**What happens**:
1. `get_tenant_database_aliases()` returns `['default', 'replica', 'other']`
2. Settings show 'replica' points to SAME physical database as 'default':
   ```python
   'replica': {
       # Read replica - same database as default (for testing multi-database support)
       'NAME': os.environ.get('DATABASE_DB', 'dts_test_project'),  # SAME as 'default'!
   }
   ```
3. `create_schema()` loops through all aliases:
   - Iteration 1: Connect via 'default' → `test_dts_test_project` → `CREATE SCHEMA "test"` → holds lock, transaction uncommitted (in atomic block)
   - Iteration 2: Connect via 'replica' → `test_dts_test_project` (SAME DB!) → `CREATE SCHEMA "test"` → **BLOCKS** waiting for lock from iteration 1
4. Self-deadlock: Process is waiting for itself!

**PIDs 45614 and 45623**: Both from the SAME test process, connecting via different aliases to the same database

**Why atomic blocks matter**: Without immediate commit, the first connection holds the lock while the second connection tries to acquire it

### HYPOTHESIS CONFIRMED

**Test**: Removed 'replica' database from settings.DATABASES and re-ran test

**Result**:
- Tests completed in **0.118s** (vs 1342s / 22+ minutes with replica)
- **No deadlock**
- Tests ran successfully (2 tests passed before tearDownClass error)

**Conclusion**: The self-deadlock is definitively caused by having multiple database aliases pointing to the same physical database.

## Questions Remaining

### Question 1: Why does autocommit fix solve the problem?
**Answer**: Autocommit causes each CREATE SCHEMA to commit immediately, so:
1. First iteration (default): CREATE SCHEMA → commits immediately
2. Second iteration (replica): `schema_exists()` now returns True → skips CREATE SCHEMA
3. No deadlock because second iteration doesn't attempt CREATE

### Question 2: Can this happen in production?
- Is this specific to test framework atomic blocks?
- Can it happen if someone creates a tenant inside a transaction block in production code?
- **Answer**: YES - any code that calls `tenant.save()` inside a transaction block with duplicate database aliases will deadlock

## Proposed Fix

### ~~Option 1: Autocommit During Schema Creation~~ (INVALID)

**PROBLEM**: Cannot call `connection.set_autocommit(True)` while already inside a transaction (raises `psycopg.ProgrammingError`). Since we're already in an atomic block from Django's test framework, this approach won't work.

### Option 1: Deduplicate Physical Databases (RECOMMENDED)

Track which physical databases have been modified and skip duplicates.

**Implementation**:
```python
def create_schema(self, check_if_exists=False, sync_schema=True, verbosity=1):
    # ... existing validation ...

    # Track unique physical databases to avoid duplicate operations
    processed_databases = set()

    for db_alias in get_tenant_database_aliases():
        connection = connections[db_alias]

        # Create unique identifier for physical database
        db_key = (
            connection.settings_dict.get('HOST', 'localhost'),
            connection.settings_dict.get('PORT', 5432),
            connection.settings_dict.get('NAME', '')
        )

        # Skip if we've already processed this physical database
        if db_key in processed_databases:
            continue
        processed_databases.add(db_key)

        # Check and create schema
        if not schema_exists(self.schema_name, database=db_alias):
            cursor = connection.cursor()
            cursor.execute('CREATE SCHEMA "%s"' % self.schema_name)
```

**Pros**:
- Maintains transaction semantics
- Only creates schema once per physical database
- Prevents self-deadlock by skipping duplicate operations
- Works for both test and production scenarios

**Cons**:
- Slightly more complex implementation
- Requires comparing database connection parameters
- Still has transaction isolation issue, but only ONE connection attempts CREATE per physical DB

### Option 3: Fix the Test Configuration

The 'replica' database already has `'TEST': {'MIRROR': 'default'}` which should make Django use the same test database. The issue is that `get_tenant_database_aliases()` still returns both aliases.

**Pros**:
- Might be the "correct" fix if replica mirroring should be invisible

**Cons**:
- Doesn't solve the problem for production code with duplicate aliases
- Users might legitimately want multiple aliases to same database

## Recommended Approach

Implement **Option 1 (Deduplicate Physical Databases)** because:
1. It prevents the self-deadlock by ensuring only ONE connection per physical database attempts CREATE SCHEMA
2. Maintains transaction semantics (doesn't break out of atomic blocks)
3. Works for both test and production scenarios with duplicate database aliases
4. Clean implementation that compares HOST+PORT+NAME to identify unique physical databases
5. The same deduplication logic should be applied to `_drop_schema()` for consistency
