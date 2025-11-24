=============================
Using pytest with django-tenants
=============================

Django-tenants provides simple pytest fixtures that offer equivalent functionality to ``TenantTestCase`` and ``FastTenantTestCase``.

Installation
============

To use pytest with django-tenants, install the optional pytest dependencies:

.. code-block:: bash

    pip install django-tenants[pytest]

This installs:

* pytest >= 7.0
* pytest-django >= 4.0

The pytest plugin is automatically discovered and loaded when you run pytest.

Basic Usage
===========

The ``tenant`` Fixture
----------------------

The main fixture is ``tenant``, which creates a test tenant, runs migrations on its schema, and activates it for use in tests:

.. code-block:: python

    import pytest

    # Mark test to use all databases (required for tenant operations)
    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')

    def test_something(tenant):
        # Tenant is automatically created and activated
        assert tenant.schema_name == 'test'
        # Your test code here

**Important:** Tests that use tenant fixtures must be marked with ``@pytest.mark.django_db(transaction=False, databases='__all__')`` because:

* ``transaction=False``: Tenant operations require actual database commits (like Django's ``TransactionTestCase``)
* ``databases='__all__'``: Tenant operations affect all configured tenant databases

**Note:** Cleanup is automatic! pytest-django's transaction rollback will:

* Delete the tenant record
* Delete the domain record
* Drop the schema (PostgreSQL DDL is transactional)

The ``settings`` fixture automatically restores ``ALLOWED_HOSTS``.

The ``tenant_domain`` Fixture
------------------------------

Returns the primary domain associated with the test tenant:

.. code-block:: python

    def test_domain(tenant, tenant_domain):
        assert tenant_domain.domain == 'tenant.test.com'
        assert tenant_domain.tenant == tenant

The ``tenant_client`` Fixture
------------------------------

Provides a tenant-aware test client:

.. code-block:: python

    def test_view(tenant_client):
        response = tenant_client.get('/path/')
        assert response.status_code == 200

By default, this returns a ``TenantClient`` instance (wrapper around Django's test ``Client``).

Fixture Scopes
==============

Function Scope (Default)
-------------------------

By default, the ``tenant`` fixture is function-scoped, meaning a fresh tenant is created for each test function:

.. code-block:: python

    def test_one(tenant):
        # Gets a fresh tenant
        pass

    def test_two(tenant):
        # Gets a different fresh tenant
        pass

This is equivalent to ``TenantTestCase`` behavior - slowest but most isolated.

Session Scope (Fast Tests)
---------------------------

For faster tests (equivalent to ``FastTenantTestCase``), override the fixture to use session scope in your ``conftest.py``:

.. code-block:: python

    # conftest.py
    import pytest

    @pytest.fixture(scope='session')
    def tenant(tenant):
        """Override to use session scope for faster tests."""
        return tenant

Now the tenant will be created once and reused across all tests:

.. code-block:: python

    def test_one(tenant):
        # Tenant created once and reused
        pass

    def test_two(tenant):
        # Same tenant instance
        pass

.. note::

   With session-scoped fixtures, data from previous tests may be visible. However,
   pytest-django's transaction rollback still occurs between tests, so each test
   gets a clean database state.

Class Scope
-----------

For per-class tenants, override to class scope:

.. code-block:: python

    # conftest.py
    @pytest.fixture(scope='class')
    def tenant(tenant):
        """Override to use class scope."""
        return tenant

Customization
=============

Via Fixture Overrides
----------------------

Override helper fixtures in your ``conftest.py`` to customize tenant properties:

.. code-block:: python

    # conftest.py
    import pytest

    @pytest.fixture
    def tenant_schema_name():
        """Customize the default schema name."""
        return 'my_custom_schema'

    @pytest.fixture
    def tenant_domain_name():
        """Customize the default domain."""
        return 'my-tenant.example.com'

Custom Tenant Setup
-------------------

Override the tenant fixture after creation to set additional fields:

.. code-block:: python

    # conftest.py
    @pytest.fixture
    def tenant(tenant):
        """Customize tenant after creation."""
        tenant.custom_field = 'value'
        tenant.save()
        return tenant

Per-Directory Configuration
----------------------------

You can have different configurations for different test directories by placing a ``conftest.py`` in each:

.. code-block:: text

    tests/
    ├── fast_tests/
    │   ├── conftest.py  # Session-scoped fixture
    │   └── test_*.py
    └── isolated_tests/
        ├── conftest.py  # Function-scoped fixture (or no override)
        └── test_*.py

Custom Test Clients
====================

Django Test Client
------------------

The default ``tenant_client`` fixture returns a ``TenantClient``:

.. code-block:: python

    def test_with_django_client(tenant_client):
        response = tenant_client.get('/path/')
        assert response.status_code == 200

django-webtest Integration
---------------------------

To use ``django-webtest``'s ``DjangoTestApp`` with tenant context, use the ``@pytest.mark.tenant_client`` marker:

.. code-block:: python

    # conftest.py
    import pytest
    from django_webtest import DjangoTestApp

    @pytest.fixture
    def my_app():
        return DjangoTestApp()

    # test file
    @pytest.mark.tenant_client('my_app')
    def test_with_webtest(tenant_client):
        # tenant_client now wraps your DjangoTestApp with tenant context
        response = tenant_client.get('/path/')
        assert response.status == '200 OK'

The fixture automatically detects the client type and wraps it with ``TenantDjangoTestApp``.

Migration from TestCase Classes
================================

From TenantTestCase
-------------------

**Before:**

.. code-block:: python

    from django_tenants.test.cases import TenantTestCase
    from django_tenants.test.client import TenantClient

    class MyTest(TenantTestCase):
        def setUp(self):
            super().setUp()
            self.c = TenantClient(self.tenant)

        def test_something(self):
            response = self.c.get('/path/')
            self.assertEqual(response.status_code, 200)

**After:**

.. code-block:: python

    import pytest

    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')

    def test_something(tenant_client):
        response = tenant_client.get('/path/')
        assert response.status_code == 200

From FastTenantTestCase
------------------------

**Before:**

.. code-block:: python

    from django_tenants.test.cases import FastTenantTestCase

    class MyFastTest(FastTenantTestCase):
        def test_one(self):
            pass

        def test_two(self):
            pass

**After:**

.. code-block:: python

    # conftest.py
    import pytest

    @pytest.fixture(scope='session')
    def tenant(tenant):
        return tenant

    # test file
    import pytest

    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')

    def test_one(tenant):
        pass

    def test_two(tenant):
        pass

Custom Setup Methods
--------------------

**Before:**

.. code-block:: python

    class MyTest(TenantTestCase):
        @classmethod
        def setup_tenant(cls, tenant):
            tenant.required_field = "value"

        @classmethod
        def setup_domain(cls, domain):
            domain.ssl = True

**After:**

.. code-block:: python

    # conftest.py
    @pytest.fixture
    def tenant(tenant):
        tenant.required_field = "value"
        tenant.save()
        return tenant

    @pytest.fixture
    def tenant_domain(tenant_domain):
        tenant_domain.ssl = True
        tenant_domain.save()
        return tenant_domain

Complete Example
================

Here's a complete example showing various pytest fixture features:

.. code-block:: python

    # conftest.py
    import pytest
    from django_webtest import DjangoTestApp

    # Use session scope for fast tests
    @pytest.fixture(scope='session')
    def tenant(tenant):
        """Override to use session scope for speed."""
        return tenant

    @pytest.fixture
    def tenant_schema_name():
        """Customize default schema name."""
        return 'test_schema'

    @pytest.fixture
    def webtest_app():
        """Provide django-webtest app."""
        return DjangoTestApp()


    # test_tenants.py
    import pytest

    # Apply to all tests in this module
    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')


    def test_tenant_created(tenant):
        """Verify tenant is created with correct properties."""
        assert tenant.schema_name == 'test_schema'
        assert tenant.pk is not None


    def test_tenant_domain(tenant_domain):
        """Verify tenant has domain."""
        assert tenant_domain.domain == 'tenant.test.com'
        assert tenant_domain.pk is not None


    def test_with_client(tenant_client):
        """Test using default TenantClient."""
        response = tenant_client.get('/api/users/')
        assert response.status_code == 200


    @pytest.mark.tenant_client('webtest_app')
    def test_with_webtest(tenant_client):
        """Test using django-webtest."""
        response = tenant_client.get('/')
        assert response.status == '200 OK'


Best Practices
==============

1. **Always use the correct marker**: Include ``@pytest.mark.django_db(transaction=False, databases='__all__')`` on all tests using tenant fixtures.

2. **Choose the right scope**:
   - Use function scope (default) for tests that need full isolation
   - Use session scope for large test suites where speed is critical
   - Use class scope if you need something in between

3. **Override fixtures in conftest.py**: Keep customization centralized rather than scattered across test files.

4. **Use session scope sparingly**: Session-scoped tenants are fast but you should understand the trade-offs.

5. **Organize by scope**: Consider organizing fast (session-scoped) and slow (function-scoped) tests into separate directories with different ``conftest.py`` files.

Troubleshooting
===============

Tests fail with "cannot truncate a table referenced in a foreign key constraint"
---------------------------------------------------------------------------------

Make sure you're using ``transaction=False`` in your ``django_db`` marker:

.. code-block:: python

    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')

"ImportError: No module named 'dts_test_project.settings'"
-----------------------------------------------------------

Configure pytest to find your Django settings in ``pyproject.toml``:

.. code-block:: toml

    [tool.pytest.ini_options]
    DJANGO_SETTINGS_MODULE = "your_project.settings"
    pythonpath = ["."]

Schema not cleaned up properly
-------------------------------

The fixture relies on pytest-django's transaction rollback to drop schemas. If you see orphaned schemas:

1. Make sure ``transaction=False`` is set in your ``django_db`` marker
2. Verify PostgreSQL DDL rollback is working (it should be transactional)
3. Check that tests aren't committing transactions explicitly

See Also
========

* :doc:`test` - Original Django test case documentation
* `pytest documentation <https://docs.pytest.org/>`_
* `pytest-django documentation <https://pytest-django.readthedocs.io/>`_
