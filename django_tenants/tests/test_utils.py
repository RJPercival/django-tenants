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

    def test_returns_list_with_database_aliases(self):
        """
        Should return a list containing all tenant database aliases.

        After Phase 5, the test settings include multiple databases
        (default, replica, other), so this verifies the function detects them all.
        """
        from django_tenants.utils import get_tenant_database_aliases

        result = get_tenant_database_aliases()

        # Should return a list
        self.assertIsInstance(result, list)

        # Should contain multiple elements (we have 3 in test settings)
        self.assertGreaterEqual(len(result), 1)

        # Should include the default database
        expected = get_tenant_database_alias()
        self.assertIn(expected, result)

        # In test settings, should have all three tenant databases
        self.assertEqual(len(result), 3)
        self.assertIn('default', result)
        self.assertIn('replica', result)
        self.assertIn('other', result)

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

    def test_scans_databases_not_tenant_db_alias(self):
        """
        Should scan DATABASES setting directly, not TENANT_DB_ALIAS.

        The function now scans settings.DATABASES for all databases using
        the django-tenants engine, making TENANT_DB_ALIAS obsolete for this purpose.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear cache to pick up the current settings
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result = get_tenant_database_aliases()

        # Should return databases with django-tenants engine from current settings
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        # In the test environment, 'default' should be using django-tenants engine
        self.assertIn('default', result)


class MultiDatabaseDetectionTestCase(TenantTestCase):
    """
    Tests for multi-database detection in get_tenant_database_aliases().

    This function should scan settings.DATABASES and return all databases
    that use the django-tenants engine.
    """

    @override_settings(DATABASES={
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'db1',
        },
        'other': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'db2',
        },
    })
    def test_detects_multiple_tenant_databases(self):
        """
        Should detect all databases using the django-tenants engine.

        When multiple databases are configured with the django-tenants engine,
        get_tenant_database_aliases() should return all of them.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear cache to pick up the new settings
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result = get_tenant_database_aliases()

        # Should return both databases
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn('default', result)
        self.assertIn('other', result)

    @override_settings(DATABASES={
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'tenant_db',
        },
        'analytics': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'analytics_db',
        },
    })
    def test_excludes_non_tenant_databases(self):
        """
        Should only include databases using the django-tenants engine.

        Databases using standard Django engines (not django-tenants) should
        be excluded from the result.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear cache to pick up the new settings
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result = get_tenant_database_aliases()

        # Should only include 'default', not 'analytics'
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIn('default', result)
        self.assertNotIn('analytics', result)

    @override_settings(DATABASES={
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'db1',
        },
        'replica': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'db1_replica',
        },
        'analytics': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'analytics',
        },
    })
    def test_detects_read_replicas(self):
        """
        Should detect read replicas as tenant databases.

        Read replicas using the django-tenants engine should be included,
        allowing tenant schemas to be activated on them for read operations.
        """
        from django_tenants.utils import get_tenant_database_aliases

        # Clear cache to pick up the new settings
        if hasattr(get_tenant_database_aliases, 'cache_clear'):
            get_tenant_database_aliases.cache_clear()

        result = get_tenant_database_aliases()

        # Should include both tenant databases, but not analytics
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn('default', result)
        self.assertIn('replica', result)
        self.assertNotIn('analytics', result)
