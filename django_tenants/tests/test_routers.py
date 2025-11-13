from django.db import connections
from django.test import TestCase

from django_tenants.routers import TenantSyncRouter
from django_tenants.tests.testcases import BaseTestCase
from django_tenants.utils import get_tenant_model, get_tenant_domain_model, get_public_schema_name


class MultiDatabaseRouterTestCase(BaseTestCase):
    """
    Tests for TenantSyncRouter with multi-database support.

    Verifies that the router allows migrations on all databases configured
    with the django-tenants engine, not just the single database returned
    by get_primary_tenant_database().

    Uses databases = '__all__' to access all configured tenant databases.
    """

    # Allow tests to access all configured databases
    databases = '__all__'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sync_shared()

        cls.public_tenant = get_tenant_model()(schema_name=get_public_schema_name())
        cls.public_tenant.save()
        cls.public_domain = get_tenant_domain_model()(tenant=cls.public_tenant, domain='test.com')
        cls.public_domain.save()

        # Create a test tenant
        cls.tenant = get_tenant_model()(schema_name='router_test')
        cls.tenant.save()
        cls.domain = get_tenant_domain_model()(tenant=cls.tenant, domain='router.test.com')
        cls.domain.save()

    @classmethod
    def tearDownClass(cls):
        # Switch to public schema before cleanup on ALL databases
        get_tenant_model().deactivate()

        cls.domain.delete()
        cls.tenant.delete(force_drop=True)
        cls.public_domain.delete()
        cls.public_tenant.delete()
        super().tearDownClass()

    def test_allow_migrate_on_all_tenant_databases(self):
        """
        Should allow migrations on all databases with django-tenants engine.

        When allow_migrate() is called with a database that uses the
        django-tenants engine, it should return None (allowing the migration)
        for appropriate apps, regardless of which specific tenant database it is.
        """
        from django_tenants.utils import get_all_tenant_databases

        router = TenantSyncRouter()
        tenant_dbs = get_all_tenant_databases()

        # Activate tenant on all databases
        self.tenant.activate()

        # For each tenant database, migrations should be allowed for TENANT_APPS
        for db_alias in tenant_dbs:
            conn = connections[db_alias]
            # Ensure we're on the tenant schema
            self.assertEqual(conn.schema_name, 'router_test')

            # Test with a TENANT_APP (from TENANT_APPS)
            result = router.allow_migrate(db_alias, 'dts_test_app', model_name='DummyModel')
            self.assertIsNone(result,
                            f"Router should allow migration on tenant database '{db_alias}' for TENANT_APP")

    def test_disallow_migrate_on_shared_apps_in_tenant_schema(self):
        """
        Should not allow SHARED_APPS migrations on tenant schemas.

        When in a tenant schema, migrations for SHARED_APPS should not be
        allowed regardless of which database we're on.
        """
        from django_tenants.utils import get_all_tenant_databases

        router = TenantSyncRouter()
        tenant_dbs = get_all_tenant_databases()

        # Activate tenant on all databases
        self.tenant.activate()

        # For each tenant database, SHARED_APPS migrations should not be allowed
        for db_alias in tenant_dbs:
            # Test with a SHARED_APP
            result = router.allow_migrate(db_alias, 'django_tenants', model_name='TenantModel')
            self.assertFalse(result,
                           f"Router should not allow SHARED_APP migration on tenant database '{db_alias}' in tenant schema")

    def test_allow_migrate_on_public_schema(self):
        """
        Should allow SHARED_APPS migrations on public schema.

        When in the public schema, migrations for SHARED_APPS should be
        allowed on tenant databases.
        """
        from django_tenants.utils import get_all_tenant_databases

        router = TenantSyncRouter()
        tenant_dbs = get_all_tenant_databases()

        # Set to public schema on all databases
        get_tenant_model().deactivate()

        # For each tenant database in public schema, SHARED_APPS should be allowed
        for db_alias in tenant_dbs:
            conn = connections[db_alias]
            self.assertEqual(conn.schema_name, get_public_schema_name())

            # Test with a SHARED_APP
            result = router.allow_migrate(db_alias, 'django_tenants', model_name='TenantModel')
            self.assertIsNone(result,
                            f"Router should allow SHARED_APP migration on '{db_alias}' in public schema")

    def test_disallow_migrate_on_non_tenant_database(self):
        """
        Should not allow migrations on databases that don't use django-tenants engine.

        If a database doesn't use the django-tenants engine, the router should
        return False to indicate migrations are not allowed.
        """
        router = TenantSyncRouter()

        # Test with a fake non-tenant database alias
        # This database doesn't exist in settings, so it won't be in get_all_tenant_databases()
        result = router.allow_migrate('fake_analytics_db', 'dts_test_app', model_name='DummyModel')

        # Should return False for non-tenant database
        self.assertFalse(result,
                        "Router should not allow migrations on non-tenant database")

    def test_allow_migrate_with_no_active_tenant(self):
        """
        Should handle queries appropriately when no tenant is active.

        When allow_migrate is called with no active tenant (connection.tenant is None),
        the router should still make appropriate routing decisions based on the
        schema (public vs tenant) and app type.
        """
        router = TenantSyncRouter()

        # Ensure no tenant is active on any database
        get_tenant_model().deactivate()

        # In public schema with no active tenant, SHARED_APPS should be allowed
        result = router.allow_migrate('default', 'django_tenants')
        self.assertIsNone(result,
                        "Router should allow SHARED_APP migrations in public schema even with no active tenant")

        # TENANT_APPS should not be allowed in public schema, even with no tenant
        result = router.allow_migrate('default', 'dts_test_app')
        self.assertFalse(result,
                       "Router should not allow TENANT_APP migrations in public schema")

    def test_allow_migrate_with_unknown_app_label(self):
        """
        Should raise LookupError for apps not in Django's app registry.

        When allow_migrate is called with an app that doesn't exist in Django's
        app registry, the router currently raises a LookupError. This behavior
        prevents migrations for misconfigured or typo'd app names.

        Note: This documents current behavior. Future enhancement could be to
        return None instead, allowing Django's default routing to handle it.
        """
        router = TenantSyncRouter()

        # Activate a tenant
        self.tenant.activate()

        # Test with an app that doesn't exist in Django's app registry
        with self.assertRaises(LookupError) as cm:
            router.allow_migrate('default', 'nonexistent_app', model_name='FakeModel')

        # Error should mention the app doesn't exist
        self.assertIn('nonexistent_app', str(cm.exception))

    def test_allow_migrate_with_none_model_name(self):
        """
        Should handle None model_name gracefully.

        When allow_migrate is called without a model_name (model_name=None),
        which can happen in some Django operations, the router should still
        make appropriate routing decisions based on app_label alone.
        """
        router = TenantSyncRouter()

        # Activate a tenant
        self.tenant.activate()

        # Test with TENANT_APP but no model_name
        result = router.allow_migrate('default', 'dts_test_app', model_name=None)
        # Should still allow migrations for TENANT_APP in tenant schema
        self.assertIsNone(result,
                        "Router should handle None model_name gracefully for TENANT_APPS")

        # Deactivate and test in public schema
        get_tenant_model().deactivate()

        # Test with SHARED_APP but no model_name
        result = router.allow_migrate('default', 'django_tenants', model_name=None)
        # Should allow migrations for SHARED_APP in public schema
        self.assertIsNone(result,
                        "Router should handle None model_name gracefully for SHARED_APPS")
