"""
Tests for multi-database migration support.

These tests verify that tenant migrations run correctly across multiple databases.
"""
import unittest
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, override_settings

from django_tenants.migration_executors.base import run_migrations
from django_tenants.migration_executors.standard import StandardExecutor
from django_tenants.signals import schema_migrated, schema_pre_migration
from django_tenants.test.cases import TenantTestCase
from django_tenants.tests.testcases import BaseTestCase
from django_tenants.utils import (
    get_public_schema_name,
    get_tenant_database_alias,
    get_tenant_database_aliases,
    get_tenant_domain_model,
    get_tenant_model,
    schema_exists,
)


class MultiDatabaseMigrationTestCase(BaseTestCase):
    """
    Test multi-database migration support.

    Tests run_migrations() and executors with multiple tenant databases.
    """

    # Use all databases for these tests
    databases = '__all__'

    def setUp(self):
        super().setUp()
        self.sync_shared()
        self.tenant = get_tenant_model()(schema_name='test_migration')
        self.tenant.save()
        domain = get_tenant_domain_model()(
            tenant=self.tenant,
            domain='test-migration.example.com'
        )
        domain.save()

    def tearDown(self):
        # Clean up tenant
        get_tenant_model().deactivate()
        self.tenant.delete(force_drop=True)
        super().tearDown()

    def test_run_migrations_uses_database_from_options(self):
        """
        run_migrations() should use the database specified in options.
        Note: run_migrations() operates on ONE database at a time.
        Multi-database iteration happens at the executor level.
        """
        # Run migrations with specific database
        options = {
            'database': 'other',  # Specific database from test settings
            'verbosity': 0,
            'interactive': False,
        }

        # Mock to track which database was migrated
        with mock.patch('django_tenants.migration_executors.base.get_tenant_base_migrate_command_class') as mock_migrate:
            mock_command_instance = mock.Mock()
            mock_migrate.return_value.return_value = mock_command_instance

            run_migrations([], options, 'test', 'test_migration')

            # Should have been called once only
            mock_command_instance.execute.assert_called_once()

            # Verify it was called with the correct database option
            call_kwargs = mock_command_instance.execute.call_args[1]
            self.assertEqual(call_kwargs['database'], 'other')

    def test_standard_executor_migrates_all_databases(self):
        """
        StandardExecutor should migrate each tenant on all accessible tenant databases
        (excluding MIRROR databases) when database option is None.
        """
        options = {
            'database': None,
            'verbosity': 0,
            'interactive': False,
        }

        executor = StandardExecutor([], options)

        # Mock run_migrations to track calls
        with mock.patch('django_tenants.migration_executors.standard.run_migrations') as mock_run:
            executor.run_migrations(tenants=['test_migration'])

            # Should be called once for each accessible database (excluding mirrors)
            expected_databases = executor._get_databases_to_migrate()
            expected_calls = len(expected_databases)
            self.assertEqual(
                mock_run.call_count,
                expected_calls,
                f"Should migrate on all {expected_calls} accessible tenant databases"
            )

            # Verify each database was used
            called_databases = {call[0][1]['database'] for call in mock_run.call_args_list}
            self.assertEqual(
                called_databases,
                set(expected_databases),
                "Should migrate on all accessible tenant databases (excluding mirrors)"
            )

    def test_standard_executor_migrates_specific_database_when_specified(self):
        """
        StandardExecutor should migrate only the specified database when
        database option is set.
        """
        options = {
            'database': 'other',
            'verbosity': 0,
            'interactive': False,
        }

        executor = StandardExecutor([], options)

        # Mock run_migrations to track calls
        with mock.patch('django_tenants.migration_executors.standard.run_migrations') as mock_run:
            executor.run_migrations(tenants=['test_migration'])

            # Should be called once only
            mock_run.assert_called_once()

            # Verify correct database was used
            call_kwargs = mock_run.call_args[0][1]
            self.assertEqual(call_kwargs['database'], 'other')

    def test_migrate_schemas_command_defaults_to_all_databases(self):
        """
        The migrate_schemas command should migrate all accessible tenant databases
        (excluding MIRROR databases) when --database option is not specified.
        """
        stdout = StringIO()

        # Mock run_migrations to track which databases are migrated
        with mock.patch('django_tenants.migration_executors.base.get_tenant_base_migrate_command_class') as mock_migrate:
            mock_command_instance = mock.Mock()
            mock_migrate.return_value.return_value = mock_command_instance

            # Run command without --database option
            call_command(
                'migrate_schemas',
                schema_name='test_migration',
                verbosity=0,
                interactive=False,
                stdout=stdout,
            )

            # Should have migrated on all accessible tenant databases (excluding mirrors)
            # Each tenant on each database = 1 tenant * N accessible databases
            executor = StandardExecutor([], {'database': None})
            expected_calls = len(executor._get_databases_to_migrate())
            self.assertEqual(
                mock_command_instance.execute.call_count,
                expected_calls,
                f"Should migrate on all {expected_calls} accessible tenant databases"
            )

    def test_migrate_schemas_command_respects_database_option(self):
        """
        The migrate_schemas command should migrate only the specified database
        when --database option is provided.
        """
        stdout = StringIO()

        # Mock run_migrations to track which database is migrated
        with mock.patch('django_tenants.migration_executors.base.get_tenant_base_migrate_command_class') as mock_migrate:
            mock_command_instance = mock.Mock()
            mock_migrate.return_value.return_value = mock_command_instance

            # Run command with specific --database option
            call_command(
                'migrate_schemas',
                schema_name='test_migration',
                database='other',
                verbosity=0,
                interactive=False,
                stdout=stdout,
            )

            # Should have migrated only once
            mock_command_instance.execute.assert_called_once()

            # Verify correct database was used
            call_kwargs = mock_command_instance.execute.call_args[1]
            self.assertEqual(call_kwargs['database'], 'other')

    def test_signals_sent_for_each_database(self):
        """
        Migration signals should be sent for each accessible database that is migrated
        (excluding MIRROR databases).
        Tests executor-level behavior where migrations run on all accessible databases.
        """
        options = {
            'database': None,
            'verbosity': 0,
            'interactive': False,
        }

        executor = StandardExecutor([], options)

        pre_migration_calls = []
        post_migration_calls = []

        def pre_handler(sender, schema_name, **kwargs):
            pre_migration_calls.append(schema_name)

        def post_handler(sender, schema_name, **kwargs):
            post_migration_calls.append(schema_name)

        schema_pre_migration.connect(pre_handler)
        schema_migrated.connect(post_handler)

        try:
            # Mock the actual migration execution
            with mock.patch('django_tenants.migration_executors.base.get_tenant_base_migrate_command_class'):
                executor.run_migrations(tenants=['test_migration'])

            # Signals should be sent once per accessible database (excluding mirrors)
            expected_calls = len(executor._get_databases_to_migrate())
            self.assertEqual(
                len(pre_migration_calls),
                expected_calls,
                f"Pre-migration signal should be sent {expected_calls} times (once per accessible database)"
            )
            self.assertEqual(
                len(post_migration_calls),
                expected_calls,
                f"Post-migration signal should be sent {expected_calls} times (once per accessible database)"
            )

            # All calls should be for the same schema
            self.assertTrue(
                all(name == 'test_migration' for name in pre_migration_calls),
                "All pre-migration signals should be for test_migration schema"
            )
            self.assertTrue(
                all(name == 'test_migration' for name in post_migration_calls),
                "All post-migration signals should be for test_migration schema"
            )
        finally:
            schema_pre_migration.disconnect(pre_handler)
            schema_migrated.disconnect(post_handler)

    def test_public_schema_migrated_once_on_default_database(self):
        """
        The public schema should be migrated only once on the default database,
        not on all tenant databases.
        """
        options = {
            'database': None,
            'verbosity': 0,
            'interactive': False,
        }

        executor = StandardExecutor([], options)

        # Mock run_migrations to track calls
        with mock.patch('django_tenants.migration_executors.standard.run_migrations') as mock_run:
            executor.run_migrations(tenants=[get_public_schema_name()])

            # Public schema should be migrated exactly once
            mock_run.assert_called_once()

            # Should use default database (or the one from get_tenant_database_alias)
            call_kwargs = mock_run.call_args[0][1]
            expected_db = call_kwargs.get('database', get_tenant_database_alias())
            self.assertEqual(
                expected_db,
                get_tenant_database_alias(),
                "Public schema should be migrated on default database"
            )

    def test_get_databases_to_migrate_excludes_mirror_databases(self):
        """
        _get_databases_to_migrate() should exclude databases with TEST.MIRROR set.

        Django automatically adds default TEST configuration with MIRROR=None to all
        database configs. The method should correctly distinguish between:
        - Databases with MIRROR=None (should be included)
        - Databases with MIRROR='some_db' (should be excluded)
        """
        from django.conf import settings

        # Clear the cache to ensure we get fresh database detection
        # This is necessary because other tests may have called get_tenant_database_aliases()
        # before all test databases were created
        get_tenant_database_aliases.cache_clear()

        options = {'database': None}
        executor = StandardExecutor([], options)

        databases = executor._get_databases_to_migrate()

        # Should include 'default' and 'other' but not 'replica'
        self.assertIn('default', databases,
                      "default database should be included (MIRROR=None)")
        self.assertIn('other', databases,
                      "other database should be included (MIRROR=None)")
        self.assertNotIn('replica', databases,
                         "replica database should be excluded (MIRROR='default')")

        # Verify our understanding of the TEST configuration
        self.assertIsNone(settings.DATABASES['default'].get('TEST', {}).get('MIRROR'),
                          "default should have MIRROR=None")
        self.assertEqual(settings.DATABASES['replica'].get('TEST', {}).get('MIRROR'),
                         'default',
                         "replica should have MIRROR='default'")
        self.assertIsNone(settings.DATABASES['other'].get('TEST', {}).get('MIRROR'),
                          "other should have MIRROR=None")
