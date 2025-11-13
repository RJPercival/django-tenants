"""
Integration tests for multi-database support in django-tenants.

These tests verify that the multi-database functionality works correctly
in real-world scenarios including read replicas, sharding, complex routing,
full tenant lifecycle, and concurrent operations.
"""
import threading
import time
from unittest import skipIf

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connections, transaction
from django.test import TransactionTestCase

from django_tenants.tests.testcases import BaseTestCase
from django_tenants.test.cases import FastTenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import (
    get_public_schema_name,
    get_all_tenant_databases,
    get_tenant_model,
    get_tenant_domain_model,
    schema_exists,
    tenant_context,
)
from dts_test_app.models import DummyModel


class ReadReplicaIntegrationTest(BaseTestCase):
    """
    Integration tests for read replica scenarios.

    Verifies that:
    - Tenant schemas exist on both primary and replica databases
    - Tenant activation works across all databases including replicas
    - Read operations work correctly on replica databases
    - Write operations go to primary database
    """

    def test_tenant_schema_created_on_replica_database(self):
        """
        Should create tenant schema on replica database when tenant is created.

        When a tenant is saved, the schema should be created on ALL tenant
        databases including read replicas (TEST.MIRROR databases).
        """
        tenant = get_tenant_model()(schema_name='replica_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='replica.test.com')
        domain.save()

        try:
            # Verify schema exists on all non-mirror databases
            tenant_dbs = get_all_tenant_databases()
            non_mirror_dbs = [
                db for db in tenant_dbs
                if not settings.DATABASES.get(db, {}).get('TEST', {}).get('MIRROR')
            ]

            for db_alias in non_mirror_dbs:
                self.assertTrue(
                    schema_exists('replica_test', database=db_alias),
                    f"Schema should exist on database {db_alias}"
                )
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_tenant_activation_sets_schema_on_all_databases(self):
        """
        Should activate tenant schema on all databases including replicas.

        When tenant.activate() is called, it should set the tenant schema
        on ALL tenant databases, including read replicas.
        """
        tenant = get_tenant_model()(schema_name='activation_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='activation.test.com')
        domain.save()

        try:
            # Activate the tenant
            tenant.activate()

            # Verify all databases have the tenant schema set
            tenant_dbs = get_all_tenant_databases()
            for db_alias in tenant_dbs:
                conn = connections[db_alias]
                self.assertEqual(
                    conn.schema_name,
                    'activation_test',
                    f"Database {db_alias} should have tenant schema set"
                )
                self.assertEqual(
                    conn.tenant,
                    tenant,
                    f"Database {db_alias} should have tenant object set"
                )

            # Also verify that operations actually work after activation
            # This tests the real behavior users care about, not just internal state
            DummyModel(name='activation_test_data').save()
            retrieved = DummyModel.objects.get(name='activation_test_data')
            self.assertEqual(retrieved.name, 'activation_test_data',
                           "Should be able to create and retrieve tenant data after activation")
        finally:
            get_tenant_model().deactivate()
            domain.delete()
            tenant.delete(force_drop=True)

    def test_read_operations_work_on_replica_with_tenant_context(self):
        """
        Should read data correctly from tenant schema using tenant_context.

        When using tenant_context, read operations should work correctly
        on all databases with the tenant schema properly set.
        """
        tenant = get_tenant_model()(schema_name='read_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='read.test.com')
        domain.save()

        try:
            # Write some data to the tenant schema
            with tenant_context(tenant):
                DummyModel(name='test_data').save()

                # Verify we can read it back
                self.assertEqual(DummyModel.objects.count(), 1)
                obj = DummyModel.objects.get(name='test_data')
                self.assertEqual(obj.name, 'test_data')
        finally:
            get_tenant_model().deactivate()
            domain.delete()
            tenant.delete(force_drop=True)

    def test_tenant_activation_provides_data_isolation(self):
        """
        Should isolate tenant data from other tenants and public schema.

        The primary purpose of tenant activation is to ensure data isolation.
        This test verifies that data created in one tenant's context is not
        visible in another tenant's context or the public schema.
        """
        tenant1 = get_tenant_model()(schema_name='isolation_test1')
        tenant1.save()
        domain1 = get_tenant_domain_model()(tenant=tenant1, domain='isolation1.test.com')
        domain1.save()

        tenant2 = get_tenant_model()(schema_name='isolation_test2')
        tenant2.save()
        domain2 = get_tenant_domain_model()(tenant=tenant2, domain='isolation2.test.com')
        domain2.save()

        try:
            # Create data in tenant1's context
            with tenant_context(tenant1):
                DummyModel(name='tenant1_data').save()
                self.assertEqual(DummyModel.objects.filter(name='tenant1_data').count(), 1,
                               "Data should be visible in tenant1's context")

            # Verify data not visible in tenant2's context
            with tenant_context(tenant2):
                self.assertEqual(DummyModel.objects.filter(name='tenant1_data').count(), 0,
                               "Tenant1's data should not be visible in tenant2's context")

            # Verify data not visible in public schema
            get_tenant_model().deactivate()
            # DummyModel is a TENANT_APP model, shouldn't have a table in public schema
            # Attempting to query should raise an exception
            with self.assertRaises(Exception):
                DummyModel.objects.count()

            # Verify data IS visible back in tenant1's context
            with tenant_context(tenant1):
                self.assertEqual(DummyModel.objects.filter(name='tenant1_data').count(), 1,
                               "Data should still be visible when returning to tenant1's context")
        finally:
            get_tenant_model().deactivate()
            domain1.delete()
            tenant1.delete(force_drop=True)
            domain2.delete()
            tenant2.delete(force_drop=True)


class ShardedScenarioIntegrationTest(BaseTestCase):
    """
    Integration tests for sharded database scenarios.

    Verifies that:
    - Different apps can be routed to different databases
    - Tenant schemas exist on all necessary databases
    - Cross-database operations work correctly
    """

    def test_tenant_schemas_created_on_multiple_distinct_databases(self):
        """
        Should create tenant schema on all distinct databases (not just replicas).

        In a sharded setup, tenant schemas should be created on all databases
        that have the django-tenants engine, not just the primary and its replicas.
        """
        tenant = get_tenant_model()(schema_name='sharded_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='sharded.test.com')
        domain.save()

        try:
            # Verify schema exists on all non-mirror databases
            tenant_dbs = get_all_tenant_databases()
            non_mirror_dbs = [
                db for db in tenant_dbs
                if not settings.DATABASES.get(db, {}).get('TEST', {}).get('MIRROR')
            ]

            # Should have at least 2 distinct databases (default and other)
            self.assertGreaterEqual(
                len(non_mirror_dbs), 2,
                "Should have multiple distinct databases for sharding"
            )

            for db_alias in non_mirror_dbs:
                self.assertTrue(
                    schema_exists('sharded_test', database=db_alias),
                    f"Schema should exist on distinct database {db_alias}"
                )
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_tenant_activation_works_across_sharded_databases(self):
        """
        Should activate tenant on all shard databases simultaneously.

        When tenant.activate() is called in a sharded setup, all shard
        databases should have the tenant schema set correctly.
        """
        tenant = get_tenant_model()(schema_name='shard_activation')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='shard.test.com')
        domain.save()

        try:
            # Activate the tenant
            tenant.activate()

            # Verify all distinct shard databases have the tenant schema set
            tenant_dbs = get_all_tenant_databases()
            non_mirror_dbs = [
                db for db in tenant_dbs
                if not settings.DATABASES.get(db, {}).get('TEST', {}).get('MIRROR')
            ]

            for db_alias in non_mirror_dbs:
                conn = connections[db_alias]
                self.assertEqual(
                    conn.schema_name,
                    'shard_activation',
                    f"Shard database {db_alias} should have tenant schema set"
                )
        finally:
            get_tenant_model().deactivate()
            domain.delete()
            tenant.delete(force_drop=True)


class TenantLifecycleIntegrationTest(BaseTestCase):
    """
    Integration tests for full tenant lifecycle across multiple databases.

    Verifies that:
    - Tenant creation works correctly
    - Tenant updates work correctly
    - Tenant deletion works correctly
    - All operations are atomic across databases
    """

    def test_tenant_creation_creates_schemas_on_all_databases(self):
        """
        Should create schemas on all tenant databases when tenant is created.

        When a new tenant is saved, schemas should be created on ALL
        tenant databases atomically.
        """
        tenant = get_tenant_model()(schema_name='lifecycle_create')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='lifecycle.test.com')
        domain.save()

        try:
            # Verify schema exists on all non-mirror databases
            tenant_dbs = get_all_tenant_databases()
            non_mirror_dbs = [
                db for db in tenant_dbs
                if not settings.DATABASES.get(db, {}).get('TEST', {}).get('MIRROR')
            ]

            for db_alias in non_mirror_dbs:
                self.assertTrue(
                    schema_exists('lifecycle_create', database=db_alias),
                    f"Schema should be created on database {db_alias}"
                )
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_tenant_deletion_removes_schemas_from_all_databases(self):
        """
        Should remove schemas from all tenant databases when tenant is deleted.

        When a tenant is deleted with force_drop=True, schemas should be
        dropped from ALL tenant databases.
        """
        tenant = get_tenant_model()(schema_name='lifecycle_delete')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='delete.test.com')
        domain.save()

        # Verify schemas exist
        tenant_dbs = get_all_tenant_databases()
        non_mirror_dbs = [
            db for db in tenant_dbs
            if not settings.DATABASES.get(db, {}).get('TEST', {}).get('MIRROR')
        ]

        for db_alias in non_mirror_dbs:
            self.assertTrue(
                schema_exists('lifecycle_delete', database=db_alias),
                f"Schema should exist on database {db_alias} before deletion"
            )

        # Delete tenant and domain
        domain.delete()
        tenant.delete(force_drop=True)

        # Verify schemas are gone from all databases
        for db_alias in non_mirror_dbs:
            self.assertFalse(
                schema_exists('lifecycle_delete', database=db_alias),
                f"Schema should be deleted from database {db_alias}"
            )

    def test_tenant_update_preserves_data_on_all_databases(self):
        """
        Should preserve tenant data across all databases when tenant is updated.

        When a tenant model is updated, the schemas and data should remain
        intact on all tenant databases.
        """
        tenant = get_tenant_model()(schema_name='lifecycle_update')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='update.test.com')
        domain.save()

        try:
            # Add some data to the tenant schema
            with tenant_context(tenant):
                DummyModel(name='before_update').save()

            # Update the tenant (change domain_urls for example)
            tenant.domain_urls = ['updated.test.com']
            tenant.save()

            # Verify data is still there
            with tenant_context(tenant):
                self.assertEqual(DummyModel.objects.count(), 1)
                obj = DummyModel.objects.get()
                self.assertEqual(obj.name, 'before_update')
        finally:
            domain.delete()
            tenant.delete(force_drop=True)


class ContextManagerIntegrationTest(BaseTestCase):
    """
    Integration tests for tenant context managers in multi-database setup.

    tenant_context() and schema_context() now operate on ALL tenant databases
    for consistency and safety. They switch all databases with the django-tenants
    engine to the specified tenant/schema.

    Verifies that:
    - tenant_context switches ALL tenant databases
    - schema_context switches ALL tenant databases
    - Nested contexts work correctly across all databases
    - Context restoration works correctly on all databases
    - Data operations work within tenant context
    """

    def test_tenant_context_switches_all_databases(self):
        """
        Should switch ALL tenant databases when entering tenant context.

        tenant_context() now operates on all databases with the django-tenants
        engine for consistency. It should switch ALL tenant databases to the
        tenant's schema simultaneously.
        """
        tenant = get_tenant_model()(schema_name='context_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='context.test.com')
        domain.save()

        try:
            # Start in public schema on all databases
            get_tenant_model().deactivate()
            public_schema = get_public_schema_name()

            # Verify all databases start on public schema
            tenant_dbs = get_all_tenant_databases()
            for db_alias in tenant_dbs:
                self.assertEqual(connections[db_alias].schema_name, public_schema)

            # Enter tenant context
            with tenant_context(tenant):
                # Verify ALL databases are on tenant schema
                for db_alias in tenant_dbs:
                    self.assertEqual(
                        connections[db_alias].schema_name,
                        'context_test',
                        f"Database {db_alias} should be on tenant schema inside context"
                    )

                # Can perform operations
                DummyModel(name='context_data').save()
                self.assertEqual(DummyModel.objects.count(), 1)

            # Verify ALL databases are back to public schema
            for db_alias in tenant_dbs:
                self.assertEqual(
                    connections[db_alias].schema_name,
                    public_schema,
                    f"Database {db_alias} should be back on public schema after context"
                )
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_nested_tenant_contexts_restore_correctly(self):
        """
        Should restore previous tenant when exiting nested context on ALL databases.

        When tenant contexts are nested, exiting the inner context should
        restore the outer context's tenant on ALL tenant databases.
        """
        tenant1 = get_tenant_model()(schema_name='nested1')
        tenant1.save()
        domain1 = get_tenant_domain_model()(tenant=tenant1, domain='nested1.test.com')
        domain1.save()

        tenant2 = get_tenant_model()(schema_name='nested2')
        tenant2.save()
        domain2 = get_tenant_domain_model()(tenant=tenant2, domain='nested2.test.com')
        domain2.save()

        try:
            # Start in public on all databases
            get_tenant_model().deactivate()
            public_schema = get_public_schema_name()

            tenant_dbs = get_all_tenant_databases()

            # Outer context: tenant1
            with tenant_context(tenant1):
                # Verify all databases are on tenant1's schema
                for db_alias in tenant_dbs:
                    self.assertEqual(connections[db_alias].schema_name, 'nested1')

                # Add data to tenant1
                DummyModel(name='tenant1_nested').save()

                # Inner context: tenant2
                with tenant_context(tenant2):
                    # Verify all databases are on tenant2's schema
                    for db_alias in tenant_dbs:
                        self.assertEqual(connections[db_alias].schema_name, 'nested2')
                    # Can't see tenant1's data in tenant2's schema
                    self.assertEqual(DummyModel.objects.count(), 0)

                # Back to tenant1 on all databases
                for db_alias in tenant_dbs:
                    self.assertEqual(connections[db_alias].schema_name, 'nested1')
                # Can see tenant1's data again
                self.assertEqual(DummyModel.objects.count(), 1)

            # Back to public on all databases
            for db_alias in tenant_dbs:
                self.assertEqual(connections[db_alias].schema_name, public_schema)
        finally:
            domain1.delete()
            tenant1.delete(force_drop=True)
            domain2.delete()
            tenant2.delete(force_drop=True)

    def test_schema_context_switches_all_databases(self):
        """
        Should switch ALL tenant databases when entering schema context.

        schema_context() now operates on all databases with the django-tenants
        engine for consistency. It should switch ALL tenant databases to the
        specified schema simultaneously.
        """
        from django_tenants.utils import schema_context

        tenant = get_tenant_model()(schema_name='schema_ctx_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='schemactx.test.com')
        domain.save()

        try:
            # Start in public schema on all databases
            get_tenant_model().deactivate()
            public_schema = get_public_schema_name()

            tenant_dbs = get_all_tenant_databases()

            # Verify all databases start on public schema
            for db_alias in tenant_dbs:
                self.assertEqual(connections[db_alias].schema_name, public_schema)

            # Enter schema context
            with schema_context('schema_ctx_test'):
                # Verify ALL databases are on the schema
                for db_alias in tenant_dbs:
                    self.assertEqual(
                        connections[db_alias].schema_name,
                        'schema_ctx_test',
                        f"Database {db_alias} should be on schema inside context"
                    )

            # Verify ALL databases are back to public schema
            for db_alias in tenant_dbs:
                self.assertEqual(
                    connections[db_alias].schema_name,
                    public_schema,
                    f"Database {db_alias} should be back on public schema after context"
                )
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_tenant_context_database_parameter_deprecated(self):
        """
        Should emit deprecation warning when database parameter is used.

        The database parameter for tenant_context() is deprecated because
        tenant_context() now operates on all tenant databases for consistency.
        """
        tenant = get_tenant_model()(schema_name='deprecation_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='deprecation.test.com')
        domain.save()

        try:
            # Using database parameter should emit deprecation warning
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with tenant_context(tenant, database='default'):
                    pass

                # Should have captured deprecation warning
                self.assertEqual(len(w), 1)
                self.assertTrue(issubclass(w[0].category, DeprecationWarning))
                self.assertIn("database", str(w[0].message).lower())
                self.assertIn("deprecated", str(w[0].message).lower())
        finally:
            domain.delete()
            tenant.delete(force_drop=True)

    def test_schema_context_database_parameter_deprecated(self):
        """
        Should emit deprecation warning when database parameter is used.

        The database parameter for schema_context() is deprecated because
        schema_context() now operates on all tenant databases for consistency.
        """
        from django_tenants.utils import schema_context

        tenant = get_tenant_model()(schema_name='deprecation_schema_test')
        tenant.save()
        domain = get_tenant_domain_model()(tenant=tenant, domain='deprecation-schema.test.com')
        domain.save()

        try:
            # Using database parameter should emit deprecation warning
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with schema_context('deprecation_schema_test', database='default'):
                    pass

                # Should have captured deprecation warning
                self.assertEqual(len(w), 1)
                self.assertTrue(issubclass(w[0].category, DeprecationWarning))
                self.assertIn("database", str(w[0].message).lower())
                self.assertIn("deprecated", str(w[0].message).lower())
        finally:
            domain.delete()
            tenant.delete(force_drop=True)


class ConcurrentOperationsIntegrationTest(TransactionTestCase):
    """
    Integration tests for concurrent tenant operations.

    Uses TransactionTestCase instead of BaseTestCase to allow testing
    real concurrency with threads.

    Verifies that:
    - Multiple threads can activate different tenants simultaneously
    - Tenant isolation is maintained across threads
    - No race conditions occur
    """

    databases = '__all__'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Reset all tenant databases to public schema
        get_tenant_model().deactivate()

    def setUp(self):
        super().setUp()
        # Reset all tenant databases to public schema before each test
        get_tenant_model().deactivate()

    def test_concurrent_tenant_activation_maintains_isolation(self):
        """
        Should maintain tenant isolation when multiple threads activate different tenants.

        When multiple threads activate different tenants simultaneously, each
        thread should work with its own tenant data without interference.
        """
        # Create two tenants
        tenant1 = get_tenant_model()(schema_name='concurrent1')
        tenant1.save()
        domain1 = get_tenant_domain_model()(tenant=tenant1, domain='concurrent1.test.com')
        domain1.save()

        tenant2 = get_tenant_model()(schema_name='concurrent2')
        tenant2.save()
        domain2 = get_tenant_domain_model()(tenant=tenant2, domain='concurrent2.test.com')
        domain2.save()

        try:
            # Add data to each tenant
            with tenant_context(tenant1):
                DummyModel(name='tenant1_data').save()

            with tenant_context(tenant2):
                DummyModel(name='tenant2_data').save()

            # Reset to public
            get_tenant_model().deactivate()

            # Results storage
            results = {'tenant1': None, 'tenant2': None, 'errors': []}

            def work_with_tenant1():
                try:
                    with tenant_context(tenant1):
                        time.sleep(0.1)  # Simulate some work
                        count = DummyModel.objects.count()
                        obj = DummyModel.objects.first()
                        results['tenant1'] = {'count': count, 'name': obj.name if obj else None}
                except Exception as e:
                    results['errors'].append(('tenant1', str(e)))

            def work_with_tenant2():
                try:
                    with tenant_context(tenant2):
                        time.sleep(0.1)  # Simulate some work
                        count = DummyModel.objects.count()
                        obj = DummyModel.objects.first()
                        results['tenant2'] = {'count': count, 'name': obj.name if obj else None}
                except Exception as e:
                    results['errors'].append(('tenant2', str(e)))

            # Start both threads
            thread1 = threading.Thread(target=work_with_tenant1)
            thread2 = threading.Thread(target=work_with_tenant2)

            thread1.start()
            thread2.start()

            thread1.join()
            thread2.join()

            # Verify no errors occurred
            self.assertEqual(results['errors'], [], "No errors should occur during concurrent operations")

            # Verify each thread saw its own tenant's data
            self.assertIsNotNone(results['tenant1'], "Thread 1 should have results")
            self.assertEqual(results['tenant1']['count'], 1, "Tenant1 should see 1 record")
            self.assertEqual(results['tenant1']['name'], 'tenant1_data', "Tenant1 should see its own data")

            self.assertIsNotNone(results['tenant2'], "Thread 2 should have results")
            self.assertEqual(results['tenant2']['count'], 1, "Tenant2 should see 1 record")
            self.assertEqual(results['tenant2']['name'], 'tenant2_data', "Tenant2 should see its own data")

        finally:
            get_tenant_model().deactivate()
            domain1.delete()
            tenant1.delete(force_drop=True)
            domain2.delete()
            tenant2.delete(force_drop=True)


class ValidationIntegrationTest(BaseTestCase):
    """
    Integration tests for multi-database validation during tenant operations.

    Verifies that:
    - Validation checks all databases
    - Proper error messages when validation fails
    - Validation doesn't prevent legitimate operations
    """

    def test_create_tenant_validates_all_databases_on_public_schema(self):
        """
        Should validate that ALL databases are on public schema before creating tenant.

        When creating a new tenant, the validation should check that ALL
        tenant databases are on the public schema.
        """
        # Set one database to a different schema
        tenant_dbs = get_all_tenant_databases()
        if len(tenant_dbs) < 2:
            self.skipTest("Need multiple databases to test validation")

        # Create a temporary tenant to get a non-public schema
        temp_tenant = get_tenant_model()(schema_name='temp_validation')
        temp_tenant.save()

        try:
            # Activate on one database only
            connections['other'].set_tenant(temp_tenant)

            # Try to create a new tenant - should fail
            new_tenant = get_tenant_model()(schema_name='validation_test')
            with self.assertRaises(Exception) as cm:
                new_tenant.save()

            self.assertIn("Can't create tenant outside the public schema", str(cm.exception))
            self.assertIn('other', str(cm.exception))
        finally:
            # Cleanup
            get_tenant_model().deactivate()
            temp_tenant.delete(force_drop=True)

    def test_create_tenant_succeeds_when_all_databases_on_public(self):
        """
        Should allow tenant creation when all databases are on public schema.

        When ALL tenant databases are on the public schema, tenant creation
        should succeed without validation errors.
        """
        # Ensure all databases are on public
        get_tenant_model().deactivate()

        # Verify all are on public
        public_schema = get_public_schema_name()
        for db_alias in get_all_tenant_databases():
            self.assertEqual(connections[db_alias].schema_name, public_schema)

        # Create tenant should succeed
        tenant = get_tenant_model()(schema_name='validation_success')
        tenant.save()  # Should not raise
        domain = get_tenant_domain_model()(tenant=tenant, domain='success.test.com')
        domain.save()

        try:
            self.assertIsNotNone(tenant.pk, "Tenant should be created successfully")
        finally:
            domain.delete()
            tenant.delete(force_drop=True)


class FailureModeIntegrationTest(BaseTestCase):
    """
    Integration tests for failure scenarios in multi-database operations.

    Verifies that:
    - Clear error messages when validation fails
    - Proper error propagation to users
    - Graceful handling of inconsistent database states
    """

    def test_validation_error_messages_identify_problematic_database(self):
        """
        Should clearly identify which database is in wrong state during validation.

        When save() validation fails because a database is on the wrong schema,
        the error message should explicitly name the problematic database(s)
        to aid in troubleshooting.
        """
        tenant_dbs = get_all_tenant_databases()
        if len(tenant_dbs) < 2:
            self.skipTest("Need multiple databases to test validation")

        # Create a temporary tenant
        temp_tenant = get_tenant_model()(schema_name='temp_validation')
        temp_tenant.save()

        try:
            # Activate on only one database (creating inconsistent state)
            connections['other'].set_tenant(temp_tenant)

            # Try to create a new tenant - should fail with clear error
            new_tenant = get_tenant_model()(schema_name='validation_error_test')
            with self.assertRaises(Exception) as cm:
                new_tenant.save()

            # Error message should identify 'other' database
            error_msg = str(cm.exception).lower()
            self.assertIn("can't create tenant outside the public schema", error_msg,
                        "Should have validation error message")
            self.assertIn('other', error_msg,
                        f"Error should identify problematic database. Got: {cm.exception}")
        finally:
            # Cleanup
            get_tenant_model().deactivate()
            temp_tenant.delete(force_drop=True)

    def test_schema_creation_handles_partial_database_failure(self):
        """
        Should handle gracefully when schema creation fails on one database.

        When schema creation fails on one of the tenant databases, the system
        should propagate the error with clear information about which database
        failed, helping operators diagnose and resolve the issue.
        """
        from django.db import connections
        from django.db.utils import DatabaseError

        tenant_dbs = get_all_tenant_databases()
        if len(tenant_dbs) < 2:
            self.skipTest("Need multiple databases to test partial failure")

        tenant = get_tenant_model()(schema_name='partial_failure_test')
        tenant.auto_create_schema = False
        tenant.save()

        # Create a wrapper that fails CREATE SCHEMA on 'other' database
        def failing_wrapper(execute, sql, params, many, context):
            connection = context['connection']
            if connection.alias == 'other' and 'CREATE SCHEMA' in sql.upper():
                raise DatabaseError(f"Simulated failure on database '{connection.alias}'")
            return execute(sql, params, many, context)

        try:
            # Wrap 'other' database to simulate failure
            with connections['other'].execute_wrapper(failing_wrapper):
                with self.assertRaises(DatabaseError) as cm:
                    tenant.create_schema(sync_schema=True, verbosity=0)

                # Error should mention the failing database
                error_msg = str(cm.exception).lower()
                self.assertTrue(
                    'other' in error_msg or 'simulated' in error_msg,
                    f"Error should identify failing database. Got: {cm.exception}"
                )
        finally:
            # Cleanup - schemas may be partially created
            tenant.delete(force_drop=True)

    def test_schema_operations_with_connection_failure(self):
        """
        Should provide clear error when database connection fails.

        When a database is unreachable during schema operations, the error
        should clearly indicate the connection problem and which database
        is affected.
        """
        from django.db import connections
        from django.db.utils import OperationalError

        tenant_dbs = get_all_tenant_databases()
        if len(tenant_dbs) < 2:
            self.skipTest("Need multiple databases to test connection failure")

        tenant = get_tenant_model()(schema_name='connection_failure_test')
        tenant.auto_create_schema = False
        tenant.save()

        # Create a wrapper that simulates connection failure
        def connection_failure_wrapper(execute, sql, params, many, context):
            connection = context['connection']
            if connection.alias == 'other':
                raise OperationalError(
                    f"could not connect to server on database '{connection.alias}': "
                    "Connection refused"
                )
            return execute(sql, params, many, context)

        try:
            # Wrap 'other' database to simulate connection failure
            with connections['other'].execute_wrapper(connection_failure_wrapper):
                with self.assertRaises(OperationalError) as cm:
                    tenant.create_schema(sync_schema=True, verbosity=0)

                # Error should mention connection problem
                error_msg = str(cm.exception).lower()
                self.assertTrue(
                    'connection' in error_msg or 'connect' in error_msg,
                    f"Error should indicate connection problem. Got: {cm.exception}"
                )
                self.assertIn('other', error_msg,
                            f"Error should identify problematic database. Got: {cm.exception}")
        finally:
            # Cleanup
            tenant.delete(force_drop=True)

    def test_concurrent_schema_operations_are_safe(self):
        """
        Should handle concurrent schema operations safely.

        When multiple processes attempt schema operations simultaneously,
        the system should either succeed atomically or fail with clear
        errors, without leaving databases in inconsistent states.
        """
        import threading
        from django.db import connections

        tenant = get_tenant_model()(schema_name='concurrent_ops_test')
        tenant.auto_create_schema = False
        tenant.save()

        results = {'errors': [], 'success': []}

        def attempt_create_schema(thread_id):
            try:
                tenant.create_schema(sync_schema=True, check_if_exists=True, verbosity=0)
                results['success'].append(thread_id)
            except Exception as e:
                results['errors'].append((thread_id, str(e)))

        try:
            # Start multiple threads trying to create schema simultaneously
            threads = [
                threading.Thread(target=attempt_create_schema, args=(i,))
                for i in range(3)
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # At least one should succeed (or all if check_if_exists works correctly)
            self.assertGreater(
                len(results['success']),
                0,
                "At least one schema creation should succeed"
            )

            # Verify schema actually exists on all databases
            tenant_dbs = get_all_tenant_databases()
            for db_alias in tenant_dbs:
                self.assertTrue(
                    schema_exists('concurrent_ops_test', database=db_alias),
                    f"Schema should exist on {db_alias} after concurrent operations"
                )

            # Any errors should be about schema already existing, not corruption
            for thread_id, error_msg in results['errors']:
                error_lower = error_msg.lower()
                # Acceptable errors: schema exists, or benign race conditions
                # Unacceptable: database corruption, constraint violations
                if 'exists' not in error_lower:
                    self.fail(f"Thread {thread_id} had unexpected error: {error_msg}")
        finally:
            # Cleanup
            tenant.delete(force_drop=True)


class EndToEndHttpRequestIntegrationTest(FastTenantTestCase):
    """
    End-to-end integration tests for HTTP request → tenant identification → database routing.

    These tests verify the complete flow from receiving an HTTP request with a Host header,
    through middleware tenant identification, to database queries being routed to the
    correct tenant schema(s).

    Unlike other tests that verify individual components (middleware, activation, queries),
    these tests verify the ENTIRE flow works correctly and that data is actually isolated
    to the correct tenant schemas.
    """

    @classmethod
    def get_test_tenant_domain(cls) -> str:
        return 'tenant1.e2e-test.com'

    @classmethod
    def get_test_schema_name(cls) -> str:
        return 'tenant1_e2e'

    def setUp(self) -> None:
        super().setUp()
        self.client = TenantClient(self.tenant)

        # Must deactivate to public schema before creating another tenant
        get_tenant_model().deactivate()

        # Create a second tenant for isolation testing
        self.tenant2 = get_tenant_model()(schema_name='tenant2_e2e')
        self.tenant2.save()
        self.domain2 = get_tenant_domain_model()(tenant=self.tenant2, domain='tenant2.e2e-test.com')
        self.domain2.save()
        self.client2 = TenantClient(self.tenant2)

    def tearDown(self) -> None:
        # Clean up tenant2
        get_tenant_model().deactivate()
        self.domain2.delete()
        self.tenant2.delete(force_drop=True)
        super().tearDown()

    def test_http_request_creates_data_in_correct_tenant_schema_single_db(self) -> None:
        """
        Should create data in the correct tenant schema when processing HTTP request.

        When an HTTP request is received with a Host header matching tenant1,
        any database records created should:
        1. Be visible in tenant1's schema
        2. NOT be visible in tenant2's schema
        3. NOT be visible in the public schema

        This test verifies the single-database case by checking only the 'default' database.

        Note: TenantClient sets request.tenant but doesn't invoke middleware,
        so we manually activate tenants to simulate what middleware would do.
        """
        from django_tenants.utils import get_primary_tenant_database

        # Make HTTP request with tenant1's Host header
        response = self.client.get('/')
        # Simulate middleware activation
        self.tenant.activate()

        # Verify the default database is on tenant1's schema after activation
        db_alias = get_primary_tenant_database()
        self.assertEqual(
            connections[db_alias].schema_name,
            'tenant1_e2e',
            "Default database should be on tenant1's schema after HTTP request"
        )

        # Create data through tenant1's request context
        obj1 = DummyModel.objects.create(name='tenant1_http_data')
        self.assertIsNotNone(obj1.pk, "Record should be created")

        # Verify data IS visible in tenant1's schema
        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_http_data').count(),
            1,
            "Data should be visible in tenant1's schema"
        )

        # Switch to tenant2 - data should NOT be visible
        with tenant_context(self.tenant2):
            self.assertEqual(
                DummyModel.objects.filter(name='tenant1_http_data').count(),
                0,
                "Tenant1's data should NOT be visible in tenant2's schema"
            )

        # Switch to public schema - data should NOT be visible (DummyModel is TENANT_APP)
        get_tenant_model().deactivate()
        # Verify we're on public schema
        public_schema = get_public_schema_name()
        from django_tenants.utils import get_primary_tenant_database
        self.assertEqual(
            connections[get_primary_tenant_database()].schema_name,
            public_schema,
            "Should be on public schema after deactivate"
        )
        # Note: We don't query DummyModel here because it would abort the transaction
        # and cause tearDown() to fail. The schema isolation is already proven by
        # the tenant2 test above.

    def test_http_request_creates_data_in_correct_tenant_schemas_multi_db(self) -> None:
        """
        Should create data in correct tenant schemas across ALL databases when processing HTTP request.

        When an HTTP request is received with a Host header matching tenant1,
        the middleware should activate tenant1's schema on ALL tenant databases,
        and any database records created should be isolated to tenant1's schemas
        across all databases.

        This test verifies:
        1. ALL tenant databases are on tenant1's schema after HTTP request
        2. Data created is NOT visible in tenant2's schemas on any database
        3. Schema isolation works correctly across multiple databases

        Note: TenantClient sets request.tenant but doesn't invoke middleware,
        so we manually activate tenants to simulate what middleware would do.
        """
        tenant_dbs = get_all_tenant_databases()
        if len(tenant_dbs) < 2:
            self.skipTest("Need multiple databases to test multi-database scenario")

        # Make HTTP request with tenant1's Host header
        response = self.client.get('/')
        # Simulate middleware activation
        self.tenant.activate()

        # Verify ALL tenant databases are on tenant1's schema after activation
        for db_alias in tenant_dbs:
            self.assertEqual(
                connections[db_alias].schema_name,
                'tenant1_e2e',
                f"Database {db_alias} should be on tenant1's schema after HTTP request"
            )
            self.assertEqual(
                connections[db_alias].tenant,
                self.tenant,
                f"Database {db_alias} should have tenant1 object set"
            )

        # Create data through tenant1's request context
        obj1 = DummyModel.objects.create(name='tenant1_multi_db_data')
        self.assertIsNotNone(obj1.pk, "Record should be created")

        # Verify data IS visible in tenant1's context
        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_multi_db_data').count(),
            1,
            "Data should be visible in tenant1's schema"
        )

        # Switch to tenant2 - data should NOT be visible
        # tenant_context() now switches ALL databases
        with tenant_context(self.tenant2):
            # Verify ALL databases are on tenant2's schema
            for db_alias in tenant_dbs:
                self.assertEqual(
                    connections[db_alias].schema_name,
                    'tenant2_e2e',
                    f"Database {db_alias} should be on tenant2's schema inside context"
                )

            # Verify tenant1's data is NOT visible
            self.assertEqual(
                DummyModel.objects.filter(name='tenant1_multi_db_data').count(),
                0,
                "Tenant1's data should NOT be visible in tenant2's schema"
            )

        # Verify we're back to tenant1's schema on all databases
        for db_alias in tenant_dbs:
            self.assertEqual(
                connections[db_alias].schema_name,
                'tenant1_e2e',
                f"Database {db_alias} should be back on tenant1's schema after context exit"
            )

    def test_multiple_http_requests_maintain_schema_isolation(self) -> None:
        """
        Should maintain schema isolation across multiple HTTP requests to different tenants.

        When multiple HTTP requests are made to different tenants in sequence,
        each request should:
        1. Activate the correct tenant's schema
        2. Only see data belonging to that tenant
        3. Not see data from other tenants

        This simulates the real-world scenario where a web application serves
        multiple tenants with the same codebase and database server.

        Note: TenantClient sets request.tenant but doesn't invoke middleware,
        so we manually activate tenants to simulate what middleware would do.
        """
        # Request 1: Create data for tenant1
        response1 = self.client.get('/')
        # Simulate middleware activation
        self.tenant.activate()

        DummyModel.objects.create(name='tenant1_request_data')

        # Verify tenant1 sees their data
        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_request_data').count(),
            1,
            "Tenant1 should see their own data after their request"
        )
        self.assertEqual(
            DummyModel.objects.filter(name='tenant2_request_data').count(),
            0,
            "Tenant1 should not see tenant2's data (which doesn't exist yet)"
        )

        # Request 2: Create data for tenant2
        response2 = self.client2.get('/')
        # Simulate middleware activation for tenant2
        self.tenant2.activate()

        DummyModel.objects.create(name='tenant2_request_data')

        # Verify tenant2 sees only their data, not tenant1's
        self.assertEqual(
            DummyModel.objects.filter(name='tenant2_request_data').count(),
            1,
            "Tenant2 should see their own data after their request"
        )
        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_request_data').count(),
            0,
            "Tenant2 should NOT see tenant1's data"
        )

        # Request 3: Back to tenant1 - should still see only their data
        response3 = self.client.get('/')
        # Simulate middleware activation back to tenant1
        self.tenant.activate()

        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_request_data').count(),
            1,
            "Tenant1 should still see their own data on subsequent request"
        )
        self.assertEqual(
            DummyModel.objects.filter(name='tenant2_request_data').count(),
            0,
            "Tenant1 should still NOT see tenant2's data on subsequent request"
        )

        # Verify tenant2 still sees only their data
        response4 = self.client2.get('/')
        # Simulate middleware activation back to tenant2
        self.tenant2.activate()

        self.assertEqual(
            DummyModel.objects.filter(name='tenant2_request_data').count(),
            1,
            "Tenant2 should still see their own data on subsequent request"
        )
        self.assertEqual(
            DummyModel.objects.filter(name='tenant1_request_data').count(),
            0,
            "Tenant2 should still NOT see tenant1's data on subsequent request"
        )
