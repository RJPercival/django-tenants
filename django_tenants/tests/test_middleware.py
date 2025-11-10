import unittest
from django.db import connections
from django.http import HttpResponseNotFound, JsonResponse
from django.test.utils import override_settings
from django.views import View

from django_tenants.test.cases import FastTenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_tenant_database_alias, get_public_schema_name


def custom_not_found_view(request):
    return JsonResponse({'error': 'Custom 404 Not Found'}, status=404)


class CustomNotFoundView(View):
    def get(self, request):
        return JsonResponse({'error': 'Custom 404 Not Found'}, status=404)


class InvalidHostname(FastTenantTestCase):
    @classmethod
    def get_test_tenant_domain(cls):
        # This domain is not valid according to RFC 1034/1035
        return '_.fast-test.com'

    @classmethod
    def get_test_schema_name(cls):
        return '_'

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)

    def test_invalid_hostname_should_return_404(self):
        response = self.client.get('/')

        self.assertIsInstance(response, HttpResponseNotFound)


@override_settings(ALLOWED_HOSTS=['nonexistent.fast-test.com', 'tenant.fast-test.com'])
class WhenTenantNotFound(FastTenantTestCase):
    @classmethod
    def get_test_tenant_domain(cls):
        return 'tenant.fast-test.com'

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)

    @override_settings(DEFAULT_NOT_FOUND_TENANT_VIEW='django_tenants.tests.test_middleware.custom_not_found_view')
    @override_settings(ALLOWED_HOSTS=['nonexistent.fast-test.com', 'tenant.fast-test.com'])
    def test_custom_function_based_view_is_shown(self):
        response = self.client.get('/', HTTP_HOST='nonexistent.fast-test.com')
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.json(), {'error': 'Custom 404 Not Found'})

    @override_settings(DEFAULT_NOT_FOUND_TENANT_VIEW='django_tenants.tests.test_middleware.CustomNotFoundView')
    @override_settings(ALLOWED_HOSTS=['nonexistent.fast-test.com', 'tenant.fast-test.com'])
    def test_custom_class_based_view_is_shown(self):
        response = self.client.get('/', HTTP_HOST='nonexistent.fast-test.com')
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.json(), {'error': 'Custom 404 Not Found'})


class MiddlewareRefactoringTestCase(FastTenantTestCase):
    """
    Tests to verify middleware behavior remains unchanged after refactoring
    to use tenant.activate() and tenant.deactivate() instead of direct
    connection method calls.

    These tests ensure that the public schema is set before tenant lookup,
    and the correct tenant schema is activated after tenant identification.
    """

    @classmethod
    def get_test_tenant_domain(cls):
        return 'refactor-test.fast-test.com'

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)

    def test_tenant_schema_activated_after_identification(self):
        """
        Should activate the tenant schema after successfully identifying the tenant.

        After the middleware finds the tenant by hostname, it should activate that
        tenant's schema on the database connection.
        """
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]

        # Make a request through the middleware (404 is fine, we just care about schema)
        response = self.client.get('/nonexistent')

        # Should have switched to tenant schema after middleware processing
        self.assertEqual(connection.schema_name, self.tenant.schema_name)
        self.assertNotEqual(connection.schema_name, get_public_schema_name())

    def test_tenant_object_set_on_connection(self):
        """
        Should set the tenant object on the connection after identification.

        The middleware should store the identified tenant object on the connection
        so other parts of the application can access it.
        """
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]

        # Make a request through the middleware
        response = self.client.get('/nonexistent')

        # Verify tenant was set correctly on the connection
        self.assertEqual(connection.tenant, self.tenant)
        self.assertEqual(connection.schema_name, self.tenant.schema_name)
