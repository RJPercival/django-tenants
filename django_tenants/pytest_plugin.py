"""
Pytest plugin for django-tenants.

This module provides pytest fixtures for testing Django applications that use
django-tenants. It offers simple, pytest-idiomatic equivalents to TenantTestCase
and FastTenantTestCase.

Usage:
    def test_something(tenant, tenant_client):
        response = tenant_client.get('/path/')
        assert response.status_code == 200

    # For session-scoped tenant (like FastTenantTestCase), override in conftest.py:
    @pytest.fixture(scope='session')
    def tenant(tenant):
        return tenant
"""

from collections.abc import Iterator
from typing import Any

import pytest


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "tenant_client(fixture_name): Specify custom client/app fixture to wrap with tenant context"
    )


@pytest.fixture(scope="session")
def django_db_modify_db_settings() -> None:
    """
    Ensure pytest-django uses all databases for tenant tests.

    This is required because tenant operations inherently affect all
    configured tenant databases.
    """
    pass


@pytest.fixture
def tenant_schema_name() -> str:
    """
    Default schema name for test tenants.

    Override this fixture in your conftest.py to change the default:

        @pytest.fixture
        def tenant_schema_name():
            return 'my_custom_schema'
    """
    return "test"


@pytest.fixture
def tenant_domain_name() -> str:
    """
    Default domain name for test tenants.

    Override this fixture in your conftest.py to change the default:

        @pytest.fixture
        def tenant_domain_name():
            return 'my-tenant.test.com'
    """
    return "tenant.test.com"


def _sync_shared_schema() -> None:
    """Migrate the public/shared schema."""
    from django.core.management import call_command
    from django_tenants.utils import get_public_schema_name

    call_command(
        "migrate_schemas",
        schema_name=get_public_schema_name(),
        interactive=False,
        verbosity=0,
    )


@pytest.fixture
def tenant(
    settings: Any, tenant_schema_name: str, tenant_domain_name: str
) -> Iterator[Any]:
    """
    Pytest fixture providing a tenant instance with activated schema.

    This fixture creates a test tenant, runs migrations on its schema, and
    activates it for use in tests. The tenant is automatically cleaned up
    via transaction rollback after the test completes.

    By default, this fixture is function-scoped (like TenantTestCase), meaning
    a fresh tenant is created for each test function. For session-scoped behavior
    (like FastTenantTestCase), override this fixture in your conftest.py:

        @pytest.fixture(scope='session')
        def tenant(tenant):
            return tenant

    Customization:
        Override helper fixtures in conftest.py to customize:

            @pytest.fixture
            def tenant_schema_name():
                return 'my_schema'

            @pytest.fixture
            def tenant_domain_name():
                return 'my-tenant.test.com'

        Or override the tenant fixture after creation:

            @pytest.fixture
            def tenant(tenant):
                tenant.custom_field = 'value'
                tenant.save()
                return tenant

    Example:
        def test_something(tenant):
            # Tenant is automatically created and activated
            assert tenant.schema_name == 'test'
            # Your test code here

    Returns:
        Tenant model instance with an activated schema

    Note:
        Cleanup is automatic via pytest-django's transaction rollback.
        The transaction rollback will:
        - Delete the tenant record
        - Delete the domain record
        - Drop the schema (PostgreSQL DDL is transactional)
        The settings fixture will restore ALLOWED_HOSTS.
    """
    from django_tenants.utils import get_tenant_domain_model, get_tenant_model

    # Ensure public schema is migrated (idempotent)
    _sync_shared_schema()

    # Add domain to ALLOWED_HOSTS (settings fixture will restore)
    settings.ALLOWED_HOSTS += [tenant_domain_name]

    # Create tenant
    tenant_model = get_tenant_model()
    tenant = tenant_model(schema_name=tenant_schema_name)
    tenant.save(verbosity=0)

    # Create domain
    domain_model = get_tenant_domain_model()
    domain = domain_model(tenant=tenant, domain=tenant_domain_name)
    domain.save()

    # Activate tenant using context manager (deactivates on exit)
    with tenant:
        yield tenant

    # No explicit cleanup needed:
    # - Transaction rollback deletes tenant/domain records
    # - Transaction rollback drops the schema
    # - settings fixture restores ALLOWED_HOSTS


@pytest.fixture
def tenant_domain(tenant: Any) -> Any:
    """
    Pytest fixture providing the domain instance for the test tenant.

    This fixture depends on the 'tenant' fixture and returns the primary
    domain associated with the test tenant.

    Example:
        def test_domain(tenant_domain):
            assert tenant_domain.domain == 'tenant.test.com'

    Returns:
        TenantDomain model instance
    """
    return tenant.get_primary_domain()


@pytest.fixture
def tenant_client(request: Any, tenant: Any) -> Any:
    """
    Pytest fixture providing a tenant-aware test client.

    This fixture automatically wraps either Django's test Client or
    django-webtest's DjangoTestApp with tenant context. By default, it
    returns a TenantClient instance, but you can specify a custom client
    fixture to wrap using the @pytest.mark.tenant_client marker.

    Usage with default client:
        def test_view(tenant_client):
            response = tenant_client.get('/path/')
            assert response.status_code == 200

    Usage with custom DjangoTestApp:
        # In conftest.py
        @pytest.fixture
        def my_app():
            return MyCustomDjangoTestApp()

        # In test file
        @pytest.mark.tenant_client('my_app')
        def test_with_webtest(tenant_client):
            response = tenant_client.get('/path/')
            assert response.status == '200 OK'

    The fixture auto-detects the type of client and wraps it appropriately:
    - DjangoTestApp → TenantDjangoTestApp
    - Other → TenantClient

    Args:
        request: pytest request object
        tenant: The tenant fixture

    Returns:
        Tenant-aware test client instance
    """
    from django_tenants.test.client import TenantClient

    # Check for custom client fixture marker
    marker = request.node.get_closest_marker("tenant_client")

    if marker and marker.args:
        fixture_name = marker.args[0]
        custom_client = request.getfixturevalue(fixture_name)

        # Auto-detect client type and wrap appropriately
        # Check if it's a DjangoTestApp (duck typing - has .get and .post methods)
        if hasattr(custom_client, "get") and hasattr(custom_client, "app"):
            # It's likely a DjangoTestApp
            from django_tenants.test.webtest import TenantDjangoTestApp

            return TenantDjangoTestApp(custom_client, tenant)
        else:
            # For other clients, we can't easily wrap them
            # Return as-is and let the user handle tenant context
            return custom_client

    # Default: return TenantClient
    return TenantClient(tenant)
