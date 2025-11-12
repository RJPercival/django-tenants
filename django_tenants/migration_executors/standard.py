import itertools

from .base import MigrationExecutor, run_migrations


class StandardExecutor(MigrationExecutor):
    codename = 'standard'

    def run_migrations(self, tenants=None):
        tenants = tenants or []

        # Public schema migrates once on default database only
        if self.PUBLIC_SCHEMA_NAME in tenants:
            # Ensure public schema uses the default tenant database
            public_options = self.options.copy()
            public_options['database'] = public_options.get('database') or self.TENANT_DB_ALIAS
            run_migrations(self.args, public_options, self.codename, self.PUBLIC_SCHEMA_NAME)
            tenants.pop(tenants.index(self.PUBLIC_SCHEMA_NAME))

        # Tenant schemas migrate on all tenant databases
        databases = self._get_databases_to_migrate()
        for idx, (schema_name, database) in enumerate(itertools.product(tenants, databases)):
            # Create a copy of options with the specific database
            options = self.options.copy()
            options['database'] = database
            run_migrations(
                self.args,
                options,
                self.codename,
                schema_name,
                idx=idx,
                count=len(tenants) * len(databases)
            )

    def run_multi_type_migrations(self, tenants):
        tenants = tenants or []

        # Tenant schemas migrate on all tenant databases
        databases = self._get_databases_to_migrate()
        for idx, (tenant, database) in enumerate(itertools.product(tenants, databases)):
            # Create a copy of options with the specific database
            options = self.options.copy()
            options['database'] = database
            run_migrations(
                self.args,
                options,
                self.codename,
                schema_name=tenant[0],
                tenant_type=tenant[1],
                idx=idx,
                count=len(tenants) * len(databases)
            )
