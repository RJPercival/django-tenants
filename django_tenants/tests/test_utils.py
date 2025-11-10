import unittest
from django.test import RequestFactory

from django_tenants import utils
from django_tenants.middleware import TenantMainMiddleware
from django_tenants.test.cases import TenantTestCase
from django.core.management.commands.migrate import Command as MigrateCommand
from django.test.utils import override_settings

from django_tenants.utils import get_tenant, get_tenant_database_alias


class CustomMigrateCommand(MigrateCommand):
    pass


class ConfigStringParsingTestCase(TenantTestCase):
    def test_static_string(self):
        self.assertEqual(
            utils.parse_tenant_config_path("foo"),
            "foo/{}".format(self.tenant.schema_name),
        )

    def test_format_string(self):
        self.assertEqual(
            utils.parse_tenant_config_path("foo/%s/bar"),
            "foo/{}/bar".format(self.tenant.schema_name),
        )

        # Preserve trailing slash
        self.assertEqual(
            utils.parse_tenant_config_path("foo/%s/bar/"),
            "foo/{}/bar/".format(self.tenant.schema_name),
        )

    def test_get_tenant_base_migrate_command_class_default(self):
        self.assertEqual(
            utils.get_tenant_base_migrate_command_class(),
            MigrateCommand,
        )

    def test_get_tenant_base_migrate_command_class_custom(self):
        command_path = 'django_tenants.tests.test_utils.CustomMigrateCommand'
        with override_settings(TENANT_BASE_MIGRATE_COMMAND=command_path):
            self.assertEqual(
                utils.get_tenant_base_migrate_command_class(),
                CustomMigrateCommand,
            )

    def test_get_tenant(self):
        tenant_domain = 'tenant.test.com'
        factory = RequestFactory()
        tm = TenantMainMiddleware(lambda r: r)
        request = factory.get('/any/request/', HTTP_HOST=tenant_domain)
        tm.process_request(request)
        self.assertEqual(get_tenant(request).schema_name, 'test')


class GetTenantDatabaseAliasesTestCase(TenantTestCase):
    """
    Tests for get_tenant_database_aliases() function.

    This function should return a list of all database aliases that use the
    django-tenants engine. In the pre-refactor version, it simply returns
    a list containing the single result from get_tenant_database_alias().
    """

    def test_returns_list_with_single_database_alias(self):
        """
        Should return a list containing the result of get_tenant_database_alias().

        In the pre-refactor version, this enables multi-database code patterns
        while maintaining single-database behavior.
        """
        from django_tenants.utils import get_tenant_database_aliases

        result = get_tenant_database_aliases()

        # Should return a list
        self.assertIsInstance(result, list)

        # Should contain exactly one element
        self.assertEqual(len(result), 1)

        # Should match the result of get_tenant_database_alias()
        expected = get_tenant_database_alias()
        self.assertEqual(result[0], expected)

    def test_result_is_cached(self):
        """
        Should cache the result for performance.

        Calling the function multiple times should return the same list instance,
        indicating that the result has been cached.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear any existing cache
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result1 = get_tenant_database_aliases()
        result2 = get_tenant_database_aliases()

        # Should return the same cached object
        self.assertIs(result1, result2)

    @override_settings(TENANT_DB_ALIAS='custom_db')
    def test_respects_tenant_db_alias_setting(self):
        """
        Should respect the TENANT_DB_ALIAS setting when configured.

        When TENANT_DB_ALIAS is set to a custom value, get_tenant_database_aliases()
        should return a list containing that value.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear cache to pick up the new setting
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result = get_tenant_database_aliases()

        self.assertEqual(result, ['custom_db'])
