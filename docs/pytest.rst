=============================
Using pytest with django-tenants
=============================

Django-tenants provides pytest fixtures that offer equivalent functionality to ``TenantTestCase`` and ``FastTenantTestCase``, with a more pytest-idiomatic API.

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

The tenant fixture supports different scopes to control the tenant lifecycle:

Function Scope (Default)
-------------------------

Fresh tenant created for each test function. Slowest but most isolated:

.. code-block:: python

    def test_one(tenant):
        # Gets a fresh tenant
        pass

    def test_two(tenant):
        # Gets a different fresh tenant
        pass

This is equivalent to ``TenantTestCase`` behavior.

Class Scope
-----------

Tenant shared across all tests in a class:

.. code-block:: python

    @pytest.mark.tenant(scope='class')
    class TestMyFeature:
        def test_one(self, tenant):
            # Tests in this class share the same tenant
            pass

        def test_two(self, tenant):
            # Same tenant as test_one
            pass

Session Scope
-------------

Tenant reused across the entire test session (fastest):

.. code-block:: python

    @pytest.mark.tenant(scope='session')
    class TestFastSuite:
        def test_one(self, tenant):
            # Tenant created once and reused
            pass

        def test_two(self, tenant):
            # Same tenant instance
            pass

This is equivalent to ``FastTenantTestCase`` behavior. The tenant schema is created once and reused, making tests much faster. However:

* Data from previous tests may be visible (though transactions roll back between tests)
* Tests are not fully encapsulated

Customization
=============

Via Markers
-----------

Customize tenant properties using the ``@pytest.mark.tenant`` marker:

.. code-block:: python

    @pytest.mark.tenant(
        scope='session',
        schema_name='custom_schema',
        domain='custom.test.com'
    )
    def test_custom(tenant):
        assert tenant.schema_name == 'custom_schema'

    @pytest.mark.tenant(schema_name='another', domain='another.test.com')
    class TestAnother:
        def test_something(self, tenant):
            assert tenant.schema_name == 'another'

Via Fixture Overrides
----------------------

Override default fixtures in your ``conftest.py``:

.. code-block:: python

    # conftest.py
    import pytest

    @pytest.fixture
    def tenant_schema_name():
        return 'my_default_schema'

    @pytest.fixture
    def tenant_domain_name():
        return 'my-default.test.com'

    # For complex customization, override after creation
    @pytest.fixture
    def tenant(tenant):
        tenant.custom_field = 'value'
        tenant.save()
        return tenant

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

Custom DjangoTestApp Subclasses
--------------------------------

If you have a custom ``DjangoTestApp`` subclass, the fixture will wrap it automatically:

.. code-block:: python

    # conftest.py
    from django_webtest import DjangoTestApp

    class MyCustomApp(DjangoTestApp):
        def __init__(self):
            super().__init__()
            # Your custom initialization

    @pytest.fixture
    def custom_app():
        return MyCustomApp()

    # test file
    @pytest.mark.tenant_client('custom_app')
    def test_with_custom_app(tenant_client):
        # Your custom app is now tenant-aware
        response = tenant_client.get('/path/')

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
            # Schema reused
            pass

        def test_two(self):
            # Same schema
            pass

**After:**

.. code-block:: python

    import pytest

    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')

    @pytest.mark.tenant(scope='session')
    class TestFast:
        def test_one(self, tenant):
            pass

        def test_two(self, tenant):
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

    @pytest.fixture
    def tenant_schema_name():
        \"\"\"Override default schema name for all tests.\"\"\"
        return 'test_schema'

    @pytest.fixture
    def webtest_app():
        \"\"\"Provide a django-webtest app for integration tests.\"\"\"
        return DjangoTestApp()


    # test_tenants.py
    import pytest

    # Apply to all tests in this module
    pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')


    class TestBasicTenant:
        \"\"\"Tests with function-scoped tenants (default).\"\"\"

        def test_tenant_created(self, tenant):
            assert tenant.schema_name == 'test_schema'

        def test_tenant_domain(self, tenant_domain):
            assert tenant_domain.domain == 'tenant.test.com'


    @pytest.mark.tenant(scope='class', schema_name='shared')
    class TestSharedTenant:
        \"\"\"Tests sharing a tenant across the class.\"\"\"

        def test_first(self, tenant):
            assert tenant.schema_name == 'shared'

        def test_second(self, tenant):
            # Same tenant as test_first
            assert tenant.schema_name == 'shared'


    @pytest.mark.tenant(scope='session')
    class TestFastTenants:
        \"\"\"Fast tests reusing tenant across session.\"\"\"

        def test_one(self, tenant):
            pass

        def test_two(self, tenant):
            pass


    @pytest.mark.tenant(schema_name='custom', domain='custom.test.com')
    def test_custom_tenant(tenant, tenant_domain):
        \"\"\"Test with custom tenant configuration.\"\"\"
        assert tenant.schema_name == 'custom'
        assert tenant_domain.domain == 'custom.test.com'


    @pytest.mark.tenant_client('webtest_app')
    def test_with_webtest(tenant_client):
        \"\"\"Test using django-webtest integration.\"\"\"
        response = tenant_client.get('/')
        assert response.status == '200 OK'


    def test_with_default_client(tenant_client):
        \"\"\"Test using default TenantClient.\"\"\"
        response = tenant_client.get('/api/users/')
        assert response.status_code == 200

Best Practices
==============

1. **Always use the correct marker**: Include ``@pytest.mark.django_db(transaction=False, databases='__all__')`` on all tests using tenant fixtures.

2. **Choose the right scope**:
   - Use function scope for tests that need full isolation
   - Use class scope for related tests that can share state
   - Use session scope for large test suites where speed is critical

3. **Override fixtures in conftest.py**: Keep customization in ``conftest.py`` rather than using markers everywhere.

4. **Use session scope sparingly**: Session-scoped tenants are fast but less isolated. Use for stable, read-heavy tests.

5. **Clean up properly**: The fixtures handle cleanup automatically, but if you create additional tenants in tests, clean them up manually.

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

Session-scoped tenants not cleaned up
--------------------------------------

Session-scoped tenants are cleaned up at the end of the test session. If pytest crashes, you may need to manually drop test schemas.

See Also
========

* :doc:`test` - Original Django test case documentation
* `pytest documentation <https://docs.pytest.org/>`_
* `pytest-django documentation <https://pytest-django.readthedocs.io/>`_
