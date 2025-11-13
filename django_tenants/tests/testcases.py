from django.db import connection
from django.conf import settings
from django.core.management import call_command
from django.test import TransactionTestCase

from django_tenants.utils import get_public_schema_name


class BaseTestCase(TransactionTestCase):
    """
    Base test case that comes packed with overloaded INSTALLED_APPS,
    custom public tenant, and schemas cleanup on tearDown.

    Uses databases = '__all__' because tenant creation/deletion is inherently
    a multi-database operation in the django-tenants architecture.
    """

    # Tenant operations affect all configured tenant databases
    databases = '__all__'

    TENANT_APPS = ('dts_test_app',
                   'django.contrib.contenttypes',
                   'django.contrib.auth', )
    SHARED_APPS = ('django_tenants',
                   'customers')

    @classmethod
    def setUpClass(cls):
        # Reset all tenant databases to public schema BEFORE any class setup
        # This ensures clean state even if previous test class left schemas dirty
        from django.db import connections
        from django_tenants.utils import get_tenant_database_aliases

        for db_alias in get_tenant_database_aliases():
            try:
                connections[db_alias].set_schema_to_public()
            except Exception:
                # Database might not be accessible yet, ignore
                pass

        # Save original settings to restore in tearDownClass
        # NOTE: We do NOT save INSTALLED_APPS - restoring it causes Django's
        # app registry stack corruption. Django's test framework handles app
        # registry cleanup automatically between test classes.
        cls._original_settings = {
            'TENANT_MODEL': getattr(settings, 'TENANT_MODEL', None),
            'TENANT_DOMAIN_MODEL': getattr(settings, 'TENANT_DOMAIN_MODEL', None),
            'SHARED_APPS': getattr(settings, 'SHARED_APPS', None),
            'TENANT_APPS': getattr(settings, 'TENANT_APPS', None),
        }

        settings.TENANT_MODEL = 'customers.Client'
        settings.TENANT_DOMAIN_MODEL = 'customers.Domain'
        settings.SHARED_APPS = cls.SHARED_APPS
        settings.TENANT_APPS = cls.TENANT_APPS
        settings.INSTALLED_APPS = settings.SHARED_APPS + settings.TENANT_APPS
        if '.test.com' not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS += ['.test.com']

        super().setUpClass()

    def setUp(self):
        # Reset tenant databases to public schema
        # Only reset databases that this test has access to
        from django.db import connections
        from django_tenants.utils import get_tenant_model, get_tenant_database_aliases

        # Get databases this test class is allowed to access
        # Include mirrors because validation checks all tenant databases including mirrors
        test_databases = self._databases_names(include_mirrors=True)
        tenant_databases = get_tenant_database_aliases()

        # Reset only accessible tenant databases
        for db_alias in tenant_databases:
            if db_alias in test_databases:
                connections[db_alias].set_schema_to_public()

        super().setUp()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if '.test.com' in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.remove('.test.com')

        # Restore original settings to avoid test pollution
        for key, value in cls._original_settings.items():
            setattr(settings, key, value)

        # Clear cached database aliases since settings changed
        from django_tenants.utils import get_tenant_database_aliases
        get_tenant_database_aliases.cache_clear()

    @classmethod
    def get_tables_list_in_schema(cls, schema_name):
        cursor = connection.cursor()
        sql = """SELECT table_name FROM information_schema.tables
              WHERE table_schema = %s"""
        cursor.execute(sql, (schema_name, ))
        return [row[0] for row in cursor.fetchall()]

    @classmethod
    def sync_shared(cls):
        call_command('migrate_schemas',
                     schema_name=get_public_schema_name(),
                     interactive=False,
                     verbosity=0)
