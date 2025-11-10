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
    by get_tenant_database_alias().
    """

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
        # Switch to public schema before cleanup
        from django.db import connection
        connection.set_schema_to_public()

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
        from django_tenants.utils import get_tenant_database_aliases

        router = TenantSyncRouter()
        tenant_dbs = get_tenant_database_aliases()

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
        from django_tenants.utils import get_tenant_database_aliases

        router = TenantSyncRouter()
        tenant_dbs = get_tenant_database_aliases()

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
        from django_tenants.utils import get_tenant_database_aliases

        router = TenantSyncRouter()
        tenant_dbs = get_tenant_database_aliases()

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
        # This database doesn't exist in settings, so it won't be in get_tenant_database_aliases()
        result = router.allow_migrate('fake_analytics_db', 'dts_test_app', model_name='DummyModel')

        # Should return False for non-tenant database
        self.assertFalse(result,
                        "Router should not allow migrations on non-tenant database")
