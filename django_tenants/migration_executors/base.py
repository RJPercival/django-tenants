import sys

from django.db import transaction

from django.db.migrations.recorder import MigrationRecorder

from django_tenants.signals import schema_migrated, schema_migrate_message, schema_pre_migration
from django_tenants.utils import (
    get_public_schema_name,
    get_tenant_base_migrate_command_class,
    get_tenant_database_alias,
)


def run_migrations(args, options, executor_codename, schema_name, tenant_type='',
                   allow_atomic=True, idx=None, count=None):
    from django.core.management import color
    from django.core.management.base import OutputWrapper
    from django.db import connections
    style = color.color_style()

    def style_func(msg):
        percent_str = ''
        if idx is not None and count is not None and count > 0:
            percent_str = '%d/%d (%s%%) ' % (idx + 1, count, int(100 * (idx + 1) / count))

        message = '[%s%s:%s] %s' % (
            percent_str,
            style.NOTICE(executor_codename),
            style.NOTICE(schema_name),
            msg
        )
        signal_message = '[%s%s:%s] %s' % (
            percent_str,
            executor_codename,
            schema_name,
            msg
        )
        schema_migrate_message.send(run_migrations, message=signal_message)
        return message

    schema_pre_migration.send(run_migrations, schema_name=schema_name)

    # Use default database if not specified in options.
    # Note: run_migrations() operates on a single database. Multi-database
    # iteration happens at the executor level via _get_databases_to_migrate().
    database = options.get('database') or get_tenant_database_alias()
    connection = connections[database]
    connection.set_schema(schema_name, tenant_type=tenant_type, include_public=False)

    # ensure that django_migrations table is created in the schema before migrations run, otherwise the migration
    # table in the public schema gets picked and no migrations are applied.   For psycopg3, need to explicitly
    # set include_public to false during schema check
    migration_recorder = MigrationRecorder(connection)
    migration_recorder.ensure_schema()
    connection.set_schema(schema_name, tenant_type=tenant_type)
                       
    stdout = OutputWrapper(sys.stdout)
    stdout.style_func = style_func
    stderr = OutputWrapper(sys.stderr)
    stderr.style_func = style_func
    if int(options.get('verbosity', 1)) >= 1:
        stdout.write(style.NOTICE("=== Starting migration"))
    migrate_command_class = get_tenant_base_migrate_command_class()
    migrate_command_class(stdout=stdout, stderr=stderr).execute(*args, **options)

    try:
        transaction.commit()
        connection.close()
        connection.connection = None
    except transaction.TransactionManagementError:
        if not allow_atomic:
            raise

        # We are in atomic transaction, don't close connections
        pass

    connection.set_schema_to_public()
    schema_migrated.send(run_migrations, schema_name=schema_name)


class MigrationExecutor:
    codename = None

    def __init__(self, args, options):
        self.args = args
        self.options = options

        self.PUBLIC_SCHEMA_NAME = get_public_schema_name()
        self.TENANT_DB_ALIAS = get_tenant_database_alias()

    def run_migrations(self, tenants=None):
        raise NotImplementedError

    def run_multi_type_migrations(self, tenants):
        raise NotImplementedError

    def _get_databases_to_migrate(self) -> list[str]:
        """
        Get the list of databases to migrate.

        Returns:
            list[str]: List of database aliases to migrate. If 'database' option
                       is None, returns all accessible tenant databases (excluding
                       MIRROR databases which should be managed by their source).
                       Otherwise, returns the single specified database.
        """
        from django.conf import settings
        from django_tenants.utils import get_tenant_database_aliases

        database = self.options.get('database')
        if database is None:
            # No database specified, migrate all tenant databases
            all_databases = get_tenant_database_aliases()

            # Filter out MIRROR databases - they should be managed by their source database
            # Note: Django adds default TEST config with MIRROR=None, so we check the value
            accessible_databases = []
            for db_alias in all_databases:
                db_config = settings.DATABASES.get(db_alias, {})
                test_config = db_config.get('TEST', {})
                # Skip databases that have MIRROR set to a non-empty value
                if not test_config.get('MIRROR'):
                    accessible_databases.append(db_alias)

            return accessible_databases
        else:
            # Specific database specified, migrate only that one
            return [database]
