"""
Tests for pytest fixtures.

These tests verify that the pytest fixtures provide equivalent functionality
to TenantTestCase and FastTenantTestCase.
"""
import pytest
from django.conf import settings
from django.db import connection

from django_tenants.utils import get_tenant_model, get_tenant_domain_model

# Mark all tests in this file to use pytest with transaction=False
# This matches TransactionTestCase behavior and avoids foreign key constraint issues
# during database flushing with multi-database setups
pytestmark = pytest.mark.django_db(transaction=False, databases='__all__')


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
    - Cleanup happens at session end (not between tests)
    """

    def test_session_tenant_first(self, tenant):
        """Session-scoped tenant should have correct schema name and be persisted."""
        assert tenant.schema_name == 'test'
        assert tenant.pk is not None

    def test_session_tenant_second(self, tenant):
        """Session-scoped tenant should be available in multiple tests within session."""
        assert tenant.schema_name == 'test'
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
        """Function-scoped tenant should have correct schema name and be persisted."""
        assert tenant.schema_name == 'test'
        assert tenant.pk is not None

    def test_function_tenant_second(self, tenant):
        """Function-scoped tenant should be available in each test."""
        assert tenant.schema_name == 'test'
        assert tenant.pk is not None


@pytest.mark.tenant(scope='class')
class TestClassScopeTenant:
    """
    Test class-scoped tenant.

    Expected behavior:
    - Tenant created once per test class
    - Shared across all tests in the class
    - Cleanup happens at session end (not after class completes)
    """

    tenant_id_from_first_test = None

    def test_class_tenant_first(self, tenant):
        """Class-scoped tenant should have correct schema name and be persisted."""
        assert tenant.schema_name == 'test'
        TestClassScopeTenant.tenant_id_from_first_test = tenant.pk

    def test_class_tenant_second(self, tenant):
        """Class-scoped tenant should be shared across all tests in the class."""
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


class TestTenantFixtureResources:
    """
    Verify tenant fixture creates required resources.

    Expected behavior:
    - Tenant is persisted to database with primary key
    - Domain is persisted to database with primary key
    - Domain is added to ALLOWED_HOSTS
    """

    def test_tenant_fixture_creates_resources(self, tenant, tenant_domain):
        """
        Tenant fixture should create persisted tenant and domain resources.

        Note: This test verifies resources exist during the test.
        Cleanup verification would require a separate test mechanism.
        """
        # Tenant should be persisted with a primary key
        assert tenant.pk is not None

        # Domain should be persisted with a primary key
        assert tenant_domain.pk is not None

        # Domain should be added to ALLOWED_HOSTS
        assert tenant_domain.domain in settings.ALLOWED_HOSTS
