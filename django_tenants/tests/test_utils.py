import unittest
from django.test import RequestFactory

from django_tenants import utils
from django_tenants.middleware import TenantMainMiddleware
from django_tenants.test.cases import TenantTestCase
from django.core.management.commands.migrate import Command as MigrateCommand
from django.test.utils import override_settings

from django_tenants.utils import get_tenant, get_primary_tenant_database


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
    Tests for get_all_tenant_databases() function.

    This function should return a list of all database aliases that use the
    django-tenants engine. In the pre-refactor version, it simply returns
    a list containing the single result from get_primary_tenant_database().
    """

    def test_returns_list_with_database_aliases(self):
        """
        Should return a list containing all tenant database aliases.

        Test settings include multiple databases (default, replica, other)
        for testing multi-database support.
        """
        from django_tenants.utils import get_all_tenant_databases

        result = get_all_tenant_databases()

        # Should return a list
        self.assertIsInstance(result, list)

        # Should contain multiple elements (we have 3 in test settings)
        self.assertGreaterEqual(len(result), 1)

        # Should include the default database
        expected = get_primary_tenant_database()
        self.assertIn(expected, result)

        # In test settings, should have all three tenant databases
        self.assertEqual(len(result), 3)
        self.assertIn('default', result)
        self.assertIn('replica', result)
        self.assertIn('other', result)

    def test_scans_databases_not_tenant_db_alias(self):
        """
        Should scan DATABASES setting directly, not TENANT_DB_ALIAS.

        The function now scans settings.DATABASES for all databases using
        the django-tenants engine, making TENANT_DB_ALIAS obsolete for this purpose.
        """
        from django_tenants.utils import get_all_tenant_databases

        # Clear cache to pick up the current settings
        if hasattr(get_all_tenant_databases, 'cache_clear'):
            get_all_tenant_databases.cache_clear()

        result = get_all_tenant_databases()

        # Should return databases with django-tenants engine from current settings
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        # In the test environment, 'default' should be using django-tenants engine
        self.assertIn('default', result)


class MultiDatabaseDetectionTestCase(TenantTestCase):
    """
    Tests for multi-database detection in get_all_tenant_databases().

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
        get_all_tenant_databases() should return all of them.
        """
        from django_tenants.utils import get_all_tenant_databases

        # Clear cache to pick up the new settings
        if hasattr(get_all_tenant_databases, 'cache_clear'):
            get_all_tenant_databases.cache_clear()

        result = get_all_tenant_databases()

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
        from django_tenants.utils import get_all_tenant_databases

        # Clear cache to pick up the new settings
        if hasattr(get_all_tenant_databases, 'cache_clear'):
            get_all_tenant_databases.cache_clear()

        result = get_all_tenant_databases()

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
        from django_tenants.utils import get_all_tenant_databases

        # Clear cache to pick up the new settings
        if hasattr(get_all_tenant_databases, 'cache_clear'):
            get_all_tenant_databases.cache_clear()

        result = get_all_tenant_databases()

        # Should include both tenant databases, but not analytics
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn('default', result)
        self.assertIn('replica', result)
        self.assertNotIn('analytics', result)


class SchemaExistsTestCase(TenantTestCase):
    """
    Tests for schema_exists() function with multi-database support.

    The schema_exists() function should check if a schema exists on a specific
    database, allowing per-database schema verification in multi-database setups.
    """

    def test_checks_schema_on_default_database_by_default(self):
        """
        Should check the default tenant database when no database parameter is provided.

        When called without specifying a database, schema_exists() should use
        the default tenant database (from get_primary_tenant_database()).
        """
        from django_tenants.utils import schema_exists

        # The test tenant should exist on the default database
        self.assertTrue(schema_exists(self.tenant.schema_name))

        # A non-existent schema should return False
        self.assertFalse(schema_exists('nonexistent_schema_12345'))

    def test_checks_schema_on_specific_database(self):
        """
        Should check for schema existence on the specified database.

        When the database parameter is provided, schema_exists() should check
        the specified database for the schema.
        """
        from django_tenants.utils import schema_exists, get_all_tenant_databases
        from django.conf import settings

        # Test across all tenant databases
        for db_alias in get_all_tenant_databases():
            # Skip databases that are TEST.MIRROR - they share the same physical database
            # as another alias, so they don't have their own separate schema creation
            db_config = settings.DATABASES.get(db_alias, {})
            if db_config.get('TEST', {}).get('MIRROR'):
                # For mirrored databases, schema_exists should work because it queries
                # the same physical database, but we skip explicit testing here since
                # the mirrored database is tested via its parent
                continue

            # The test tenant should exist on non-mirrored databases
            self.assertTrue(
                schema_exists(self.tenant.schema_name, database=db_alias),
                f"Test tenant schema should exist on {db_alias}"
            )

            # A non-existent schema should return False on all databases
            self.assertFalse(
                schema_exists('nonexistent_schema_12345', database=db_alias),
                f"Non-existent schema should not exist on {db_alias}"
            )

    def test_returns_false_for_dropped_schema(self):
        """
        Should return False after a schema is dropped from a database.

        After dropping a schema from a specific database, schema_exists()
        should return False for that database.
        """
        from django_tenants.utils import schema_exists, get_all_tenant_databases
        from django.db import connections
        from django.conf import settings

        # Create a test schema on all non-mirrored databases
        test_schema_name = 'test_dropped_schema'

        # Only work with non-mirrored databases to avoid deadlocks
        non_mirrored_dbs = []
        for db_alias in get_all_tenant_databases():
            db_config = settings.DATABASES.get(db_alias, {})
            if not db_config.get('TEST', {}).get('MIRROR'):
                non_mirrored_dbs.append(db_alias)

        for db_alias in non_mirrored_dbs:
            cursor = connections[db_alias].cursor()
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{test_schema_name}"')
            cursor.close()

        try:
            # Verify schema exists on all non-mirrored databases
            for db_alias in non_mirrored_dbs:
                self.assertTrue(
                    schema_exists(test_schema_name, database=db_alias),
                    f"Schema should exist on {db_alias} before drop"
                )

            # Drop schema from all non-mirrored databases
            for db_alias in non_mirrored_dbs:
                cursor = connections[db_alias].cursor()
                cursor.execute(f'DROP SCHEMA IF EXISTS "{test_schema_name}" CASCADE')
                cursor.close()

            # Verify schema no longer exists on any non-mirrored database
            for db_alias in non_mirrored_dbs:
                self.assertFalse(
                    schema_exists(test_schema_name, database=db_alias),
                    f"Schema should not exist on {db_alias} after drop"
                )

        finally:
            # Cleanup: ensure schema is dropped
            for db_alias in non_mirrored_dbs:
                try:
                    cursor = connections[db_alias].cursor()
                    cursor.execute(f'DROP SCHEMA IF EXISTS "{test_schema_name}" CASCADE')
                    cursor.close()
                except Exception:
                    pass

    def test_case_insensitive_schema_check(self):
        """
        Should perform case-insensitive schema name checking.

        PostgreSQL schema names are case-insensitive, so schema_exists()
        should find schemas regardless of the case used in the query.
        """
        from django_tenants.utils import schema_exists

        # Test with different case variations of the tenant schema name
        schema_name = self.tenant.schema_name

        # Should find schema with exact case
        self.assertTrue(schema_exists(schema_name))

        # Should find schema with uppercase
        self.assertTrue(schema_exists(schema_name.upper()))

        # Should find schema with lowercase
        self.assertTrue(schema_exists(schema_name.lower()))

        # Should find schema with mixed case
        self.assertTrue(schema_exists(schema_name.title()))
