"""
Pytest plugin for django-tenants.

This module provides pytest fixtures for testing Django applications that use
django-tenants. It offers equivalents to TenantTestCase and FastTenantTestCase
with a more pytest-idiomatic API.

Usage:
    # In your conftest.py or test file
    @pytest.mark.tenant(scope='session')
    def test_something(tenant, tenant_client):
        response = tenant_client.get('/path/')
        assert response.status_code == 200
"""

import pytest

# Defer Django imports until fixtures are used to avoid Django setup issues
# during pytest plugin loading


# Registry to track created tenants for session/class scoped fixtures
_tenant_registry = {}


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "tenant(scope=None, schema_name=None, domain=None): Configure tenant fixture behavior"
    )
    config.addinivalue_line(
        "markers",
        "tenant_client(fixture_name): Specify custom client/app fixture to wrap with tenant context"
    )


@pytest.fixture(scope='session')
def django_db_modify_db_settings():
    """
    Ensure pytest-django uses all databases for tenant tests.

    This is required because tenant operations inherently affect all
    configured tenant databases.
    """
    pass


@pytest.fixture
def tenant_schema_name():
    """
    Default schema name for test tenants.

    Override this fixture in your conftest.py to change the default:

        @pytest.fixture
        def tenant_schema_name():
            return 'my_custom_schema'
    """
    return 'test'


@pytest.fixture
def tenant_domain_name():
    """
    Default domain name for test tenants.

    Override this fixture in your conftest.py to change the default:

        @pytest.fixture
        def tenant_domain_name():
            return 'my-tenant.test.com'
    """
    return 'tenant.test.com'


def _get_tenant_marker_config(request):
    """Extract configuration from @pytest.mark.tenant marker."""
    marker = request.node.get_closest_marker('tenant')
    if marker:
        return {
            'scope': marker.kwargs.get('scope'),
            'schema_name': marker.kwargs.get('schema_name'),
            'domain': marker.kwargs.get('domain'),
        }
    return {}


def _get_effective_scope(request):
    """
    Determine the effective scope for the tenant fixture.

    Priority:
    1. @pytest.mark.tenant(scope='...') marker
    2. Default to 'function'
    """
    marker_config = _get_tenant_marker_config(request)
    return marker_config.get('scope', 'function')


def _sync_shared_schema():
    """Migrate the public/shared schema."""
    from django.core.management import call_command
    from django_tenants.utils import get_public_schema_name

    call_command(
        'migrate_schemas',
        schema_name=get_public_schema_name(),
        interactive=False,
        verbosity=0
    )


def _add_domain_to_allowed_hosts(domain):
    """Add domain to ALLOWED_HOSTS if not already present."""
    from django.conf import settings

    if domain not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS += [domain]


def _remove_domain_from_allowed_hosts(domain):
    """Remove domain from ALLOWED_HOSTS."""
    from django.conf import settings

    if domain in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS.remove(domain)


def _create_tenant_and_domain(schema_name, domain_name):
    """
    Create a tenant and its associated domain.

    Returns:
        tuple: (tenant, domain) instances
    """
    from django_tenants.utils import get_tenant_model, get_tenant_domain_model

    # Sync shared apps first
    _sync_shared_schema()

    # Add domain to ALLOWED_HOSTS
    _add_domain_to_allowed_hosts(domain_name)

    # Ensure all databases are on public schema before creating tenant
    # (required for multi-database validation in save())
    get_tenant_model().deactivate()

    # Create tenant
    tenant_model = get_tenant_model()
    tenant = tenant_model(schema_name=schema_name)
    tenant.save(verbosity=0)

    # Create domain
    domain_model = get_tenant_domain_model()
    domain = domain_model(tenant=tenant, domain=domain_name)
    domain.save()

    return tenant, domain


def _cleanup_tenant_and_domain(tenant, domain, domain_name):
    """Clean up tenant, domain, and ALLOWED_HOSTS."""
    if domain:
        domain.delete()
    if tenant:
        tenant.delete(force_drop=True)
    _remove_domain_from_allowed_hosts(domain_name)


@pytest.fixture
def _tenant_impl(
    request,
    django_db_blocker,
    django_db_setup,
    tenant_schema_name,
    tenant_domain_name
):
    """
    Internal implementation of tenant fixture with dynamic scope.

    This fixture handles the creation, activation, and cleanup of test tenants.
    The scope is determined by the @pytest.mark.tenant(scope='...') marker.
    """
    from django.db import connection
    from django_tenants.utils import get_tenant_model

    # Get configuration from marker
    marker_config = _get_tenant_marker_config(request)
    schema_name = marker_config.get('schema_name', tenant_schema_name)
    domain_name = marker_config.get('domain', tenant_domain_name)
    scope = _get_effective_scope(request)

    # Create a unique key for this tenant configuration
    registry_key = (scope, schema_name, domain_name)

    # For session/class scope, try to reuse existing tenant
    if scope in ('session', 'class'):
        if registry_key in _tenant_registry:
            tenant = _tenant_registry[registry_key]
            connection.set_tenant(tenant)
            return tenant

    # For session scope, check if tenant already exists in database
    if scope == 'session':
        tenant_model = get_tenant_model()
        existing_tenant = tenant_model.objects.filter(schema_name=schema_name).first()
        if existing_tenant:
            _add_domain_to_allowed_hosts(domain_name)
            connection.set_tenant(existing_tenant)
            _tenant_registry[registry_key] = existing_tenant
            return existing_tenant

    # Create new tenant
    with django_db_blocker.unblock():
        tenant, domain = _create_tenant_and_domain(schema_name, domain_name)
        connection.set_tenant(tenant)

        # Store in registry for session/class scope
        if scope in ('session', 'class'):
            _tenant_registry[registry_key] = tenant

    # Cleanup function
    def cleanup():
        # Only cleanup for function scope
        # Session/class scope tenants are cleaned up by _tenant_cleanup fixture
        if scope == 'function':
            with django_db_blocker.unblock():
                _cleanup_tenant_and_domain(tenant, domain, domain_name)

    request.addfinalizer(cleanup)

    return tenant


@pytest.fixture(scope='session', autouse=True)
def _tenant_cleanup():
    """
    Session-scoped fixture to clean up all session/class-scoped tenants.

    This runs at the end of the test session to ensure all reusable tenants
    are properly cleaned up.
    """
    yield

    # Cleanup all registered tenants
    from django_tenants.utils import get_tenant_domain_model

    for (scope, schema_name, domain_name), tenant in _tenant_registry.items():
        domain_model = get_tenant_domain_model()
        domain = domain_model.objects.filter(tenant=tenant).first()
        _cleanup_tenant_and_domain(tenant, domain, domain_name)

    _tenant_registry.clear()


@pytest.fixture
def tenant(request, _tenant_impl):
    """
    Pytest fixture providing a tenant instance with activated schema.

    This fixture creates a test tenant, runs migrations on its schema, and
    activates it for use in tests. The tenant's lifecycle (function, class,
    or session scope) is controlled via the @pytest.mark.tenant marker.

    Scope Options:
        - 'function' (default): Fresh tenant for each test function (slowest, most isolated)
        - 'class': Tenant shared across test class (equivalent to TenantTestCase)
        - 'session': Tenant reused across entire test session (equivalent to FastTenantTestCase)

    Customization:
        You can customize tenant properties via marker:
            @pytest.mark.tenant(scope='session', schema_name='custom', domain='custom.test.com')

        Or by overriding helper fixtures in conftest.py:
            @pytest.fixture
            def tenant_schema_name():
                return 'my_schema'

        Or by overriding the tenant fixture after creation:
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

        @pytest.mark.tenant(scope='session')
        class TestFast:
            # All tests in this class share the same tenant (fast)
            def test_one(self, tenant):
                pass

            def test_two(self, tenant):
                pass

    Returns:
        Tenant model instance with an activated schema
    """
    return _tenant_impl


@pytest.fixture
def tenant_domain(tenant):
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
def tenant_client(request, tenant):
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
    marker = request.node.get_closest_marker('tenant_client')

    if marker and marker.args:
        fixture_name = marker.args[0]
        custom_client = request.getfixturevalue(fixture_name)

        # Auto-detect client type and wrap appropriately
        # Check if it's a DjangoTestApp (duck typing - has .get and .post methods)
        if hasattr(custom_client, 'get') and hasattr(custom_client, 'app'):
            # It's likely a DjangoTestApp
            from django_tenants.test.webtest import TenantDjangoTestApp
            return TenantDjangoTestApp(custom_client, tenant)
        else:
            # For other clients, we can't easily wrap them
            # Return as-is and let the user handle tenant context
            return custom_client

    # Default: return TenantClient
    return TenantClient(tenant)
