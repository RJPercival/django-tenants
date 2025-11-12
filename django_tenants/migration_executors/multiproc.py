import functools
import itertools
import multiprocessing

from django.conf import settings

from .base import MigrationExecutor, run_migrations


def run_migrations_percent(args, options, codename, count, idx_schema_database):
    """Helper function for multiprocessing: run migrations for one schema on one database."""
    idx, (schema_name, database) = idx_schema_database
    # Update options with the specific database
    options = options.copy()
    options['database'] = database
    return run_migrations(
        args,
        options,
        codename,
        schema_name,
        allow_atomic=False,
        idx=idx,
        count=count
    )


def run_multi_type_migrations_percent(args, options, codename, count, idx_tenant_database):
    """Helper function for multiprocessing: run multi-type migrations for one tenant on one database."""
    idx, (tenant, database) = idx_tenant_database
    # Update options with the specific database
    options = options.copy()
    options['database'] = database
    return run_migrations(
        args,
        options,
        codename,
        schema_name=tenant[0],
        tenant_type=tenant[1],
        allow_atomic=False,
        idx=idx,
        count=count
    )


class MultiprocessingExecutor(MigrationExecutor):
    codename = 'multiprocessing'

    def run_migrations(self, tenants=None):
        tenants = tenants or []

        # Public schema migrates once on default database only
        if self.PUBLIC_SCHEMA_NAME in tenants:
            # Ensure public schema uses the default tenant database
            public_options = self.options.copy()
            public_options['database'] = public_options.get('database') or self.TENANT_DB_ALIAS
            run_migrations(self.args, public_options, self.codename, self.PUBLIC_SCHEMA_NAME)
            tenants.pop(tenants.index(self.PUBLIC_SCHEMA_NAME))

        if tenants:
            processes = getattr(
                settings,
                'TENANT_MULTIPROCESSING_MAX_PROCESSES',
                2
            )
            chunks = getattr(
                settings,
                'TENANT_MULTIPROCESSING_CHUNKS',
                2
            )

            from django.db import connections

            # Close connections for all tenant databases before forking
            databases = self._get_databases_to_migrate()
            for db_alias in databases:
                connection = connections[db_alias]
                connection.close()
                connection.connection = None

            # Create list of (schema_name, database) tuples
            tenant_database_pairs = list(itertools.product(tenants, databases))

            run_migrations_p = functools.partial(
                run_migrations_percent,
                self.args,
                self.options,
                self.codename,
                len(tenant_database_pairs)
            )
            p = multiprocessing.Pool(processes=processes)
            p.map(
                run_migrations_p,
                enumerate(tenant_database_pairs),
                chunks
            )

    def run_multi_type_migrations(self, tenants):
        tenants = tenants or []
        processes = getattr(
            settings,
            'TENANT_MULTIPROCESSING_MAX_PROCESSES',
            2
        )
        chunks = getattr(
            settings,
            'TENANT_MULTIPROCESSING_CHUNKS',
            2
        )

        from django.db import connections

        # Close connections for all tenant databases before forking
        databases = self._get_databases_to_migrate()
        for db_alias in databases:
            connection = connections[db_alias]
            connection.close()
            connection.connection = None

        # Create list of (tenant, database) tuples
        tenant_database_pairs = list(itertools.product(tenants, databases))

        run_migrations_p = functools.partial(
            run_multi_type_migrations_percent,
            self.args,
            self.options,
            self.codename,
            len(tenant_database_pairs)
        )
        p = multiprocessing.Pool(processes=processes)
        p.map(
            run_migrations_p,
            enumerate(tenant_database_pairs),
            chunks
        )
