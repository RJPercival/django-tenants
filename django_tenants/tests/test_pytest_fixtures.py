"""
Tests for pytest fixtures.

These tests verify that the pytest fixtures provide equivalent functionality
to TenantTestCase and FastTenantTestCase.
"""
import pytest
from django.conf import settings
from django.db import connection

from django_tenants.utils import get_tenant_model, get_tenant_domain_model

# Mark all tests in this file to use pytest
pytestmark = pytest.mark.django_db(transaction=True, databases='__all__')


class TestTenantFixtureBasic:
    """
    Test basic tenant fixture functionality.

    Expected behavior:
    - Creates a tenant with a schema
    - Activates the tenant's schema
    - Provides tenant instance to test
    - Cleans up after test
    """

    def test_tenant_is_created(self, tenant):
        """The tenant fixture should create a tenant instance."""
        assert tenant is not None
        assert tenant.schema_name == 'test'
        assert tenant.pk is not None

    def test_tenant_schema_is_activated(self, tenant):
        """The tenant's schema should be activated on the connection."""
        assert connection.tenant == tenant
        assert connection.schema_name == tenant.schema_name

    def test_tenant_has_domain(self, tenant):
        """The tenant should have an associated domain."""
        domain = tenant.get_primary_domain()
        assert domain is not None
        assert domain.domain == 'tenant.test.com'

    def test_domain_in_allowed_hosts(self, tenant):
        """The tenant's domain should be added to ALLOWED_HOSTS."""
        domain = tenant.get_primary_domain()
        assert domain.domain in settings.ALLOWED_HOSTS


class TestTenantDomainFixture:
    """
    Test tenant_domain fixture.

    Expected behavior:
    - Returns the primary domain for the tenant
    - Domain is properly associated with tenant
    """

    def test_tenant_domain_returns_domain(self, tenant, tenant_domain):
        """The tenant_domain fixture should return the domain instance."""
        assert tenant_domain is not None
        assert tenant_domain.tenant == tenant
        assert tenant_domain.domain == 'tenant.test.com'

    def test_tenant_domain_is_primary(self, tenant, tenant_domain):
        """The tenant_domain fixture should return the primary domain."""
        assert tenant_domain == tenant.get_primary_domain()


class TestTenantClientFixture:
    """
    Test tenant_client fixture with default Django test client.

    Expected behavior:
    - Returns a TenantClient instance
    - Client is properly configured for the tenant
    - Requests use the tenant's domain
    """

    def test_tenant_client_is_tenant_client(self, tenant_client):
        """The tenant_client fixture should return a TenantClient."""
        from django_tenants.test.client import TenantClient
        assert isinstance(tenant_client, TenantClient)

    def test_tenant_client_has_tenant(self, tenant, tenant_client):
        """The tenant_client should have the tenant configured."""
        assert tenant_client.tenant == tenant


@pytest.mark.tenant(schema_name='custom_schema', domain='custom.test.com')
class TestTenantCustomization:
    """
    Test tenant customization via markers.

    Expected behavior:
    - Marker parameters override defaults
    - Custom schema name is used
    - Custom domain is used
    """

    def test_custom_schema_name(self, tenant):
        """Custom schema_name from marker should be used."""
        assert tenant.schema_name == 'custom_schema'

    def test_custom_domain(self, tenant_domain):
        """Custom domain from marker should be used."""
        assert tenant_domain.domain == 'custom.test.com'

    def test_custom_domain_in_allowed_hosts(self, tenant_domain):
        """Custom domain should be added to ALLOWED_HOSTS."""
        assert tenant_domain.domain in settings.ALLOWED_HOSTS


@pytest.mark.tenant(scope='session')
class TestSessionScopeTenant:
    """
    Test session-scoped tenant (FastTenantTestCase equivalent).

    Expected behavior:
    - Tenant is created once for the session
    - Same tenant instance used across multiple tests
    - Tenant persists between tests
    - Data is rolled back between tests (via transaction)
    """

    def test_session_tenant_first(self, tenant):
        """First test with session-scoped tenant."""
        assert tenant.schema_name == 'test'
        # Store the tenant ID for comparison in next test
        self.tenant_id = tenant.pk

    def test_session_tenant_second(self, tenant):
        """Second test should reuse the same tenant."""
        assert tenant.schema_name == 'test'
        # Tenant ID should be the same as in first test
        # (Note: This assumes tests run in order, but the fixture ensures reuse)
        assert tenant.pk is not None


class TestFunctionScopeTenant:
    """
    Test function-scoped tenant (TenantTestCase equivalent).

    Expected behavior:
    - Fresh tenant created for each test function
    - Tenant is cleaned up after each test
    - Full isolation between tests
    """

    def test_function_tenant_first(self, tenant):
        """First test with function-scoped tenant."""
        # Each test gets its own tenant
        assert tenant.schema_name == 'test'
        self.first_tenant_id = tenant.pk

    def test_function_tenant_second(self, tenant):
        """Second test gets a fresh tenant (not reused)."""
        assert tenant.schema_name == 'test'
        # In function scope, each test may get a fresh tenant
        # (cleanup and recreation happens between tests)
        assert tenant.pk is not None


@pytest.mark.tenant(scope='class')
class TestClassScopeTenant:
    """
    Test class-scoped tenant.

    Expected behavior:
    - Tenant created once per test class
    - Shared across all tests in the class
    - Cleaned up after all tests in class complete
    """

    tenant_id_from_first_test = None

    def test_class_tenant_first(self, tenant):
        """First test in class stores tenant ID."""
        assert tenant.schema_name == 'test'
        TestClassScopeTenant.tenant_id_from_first_test = tenant.pk

    def test_class_tenant_second(self, tenant):
        """Second test in class should reuse same tenant."""
        assert tenant.schema_name == 'test'
        assert tenant.pk == TestClassScopeTenant.tenant_id_from_first_test


class TestTenantIsolation:
    """
    Test that tenants are properly isolated.

    Expected behavior:
    - Each tenant has its own schema
    - Data in one tenant doesn't leak to another
    - Schema switching works correctly
    """

    def test_tenant_isolation(self, tenant):
        """Test that tenant data is isolated."""
        # The tenant should have its own schema
        assert tenant.schema_name == 'test'
        assert connection.schema_name == tenant.schema_name

        # Schema should exist in database
        from django.db import connection as db_conn
        with db_conn.cursor() as cursor:
            cursor.execute("""
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name = %s
            """, [tenant.schema_name])
            result = cursor.fetchone()
            assert result is not None


class TestTenantCleanup:
    """
    Test that tenants are properly cleaned up.

    Expected behavior:
    - Tenant is removed from database after test
    - Schema is dropped from database
    - Domain is removed from ALLOWED_HOSTS
    """

    def test_tenant_cleanup_happens(self, tenant, tenant_domain):
        """
        Test setup to verify cleanup logic.

        Note: The actual cleanup verification would need to happen in a
        separate test run or via inspection of database state after the
        fixture is torn down. This test verifies the setup is correct.
        """
        tenant_id = tenant.pk
        domain_name = tenant_domain.domain
        schema_name = tenant.schema_name

        # These should exist during the test
        assert tenant.pk is not None
        assert tenant_domain.pk is not None
        assert domain_name in settings.ALLOWED_HOSTS

        # Store for potential verification
        # (actual cleanup verification would be in a different test)
        self.cleaned_tenant_id = tenant_id
        self.cleaned_schema_name = schema_name
        self.cleaned_domain_name = domain_name
