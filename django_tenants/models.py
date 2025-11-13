from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site
from django.core.management import call_command
from django.db import models, connections, transaction
from django.urls import reverse

from django_tenants.clone import CloneSchema
from .postgresql_backend.base import _check_schema_name
from .signals import post_schema_sync, schema_needs_to_be_sync
from .utils import get_creation_fakes_migrations, get_tenant_base_schema
from .utils import schema_exists, get_tenant_domain_model, get_public_schema_name, get_primary_tenant_database, \
    get_all_tenant_databases


def _get_database_key(connection):
    """
    Generate a unique identifier for a physical database based on connection settings.

    Used to deduplicate operations when multiple database aliases point to the same
    physical database (e.g., for read replicas).

    Args:
        connection: Django database connection

    Returns:
        tuple: (HOST, PORT, NAME) identifying the physical database
    """
    return (
        connection.settings_dict.get('HOST', 'localhost'),
        connection.settings_dict.get('PORT', 5432),
        connection.settings_dict.get('NAME', '')
    )


class TenantMixin(models.Model):
    """
    All tenant models must inherit this class.
    """

    auto_drop_schema = False
    """
    USE THIS WITH CAUTION!
    Set this flag to true on a parent class if you want the schema to be
    automatically deleted if the tenant row gets deleted.
    """

    auto_create_schema = True
    """
    Set this flag to false on a parent class if you don't want the schema
    to be automatically created upon save.
    """

    clone_mode = "DATA"
    """
    One of "DATA", "NODATA".
    When using TENANT_BASE_SCHEMA, controls whether only the database
    structure will be copied, or if data will be copied along with it.
    """

    schema_name = models.CharField(max_length=63, unique=True, db_index=True,
                                   validators=[_check_schema_name])

    domain_url = None
    """
    Leave this as None. Stores the current domain url so it can be used in the logs
    """
    domain_subfolder = None
    """
    Leave this as None. Stores the subfolder in subfolder routing was used
    """

    _previous_tenant = {}

    class Meta:
        abstract = True

    def __str__(self):
        return self.schema_name

    def __enter__(self):
        """
        Syntax sugar which helps in celery tasks, cron jobs, and other scripts

        Usage:
            with Tenant.objects.get(schema_name='test') as tenant:
                # run some code in tenant test
            # run some code in previous tenant (public probably)
        """
        # Save previous tenant for each database
        for db_alias in get_all_tenant_databases():
            if db_alias not in self._previous_tenant:
                self._previous_tenant[db_alias] = []
            conn = connections[db_alias]
            self._previous_tenant[db_alias].append(conn.tenant)

        self.activate()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore previous tenant for each database using shared helper
        from django_tenants.utils import _restore_tenant_state_on_all_databases
        _restore_tenant_state_on_all_databases(self._previous_tenant)

    def activate(self):
        """
        Activate this tenant on all tenant databases.

        Switches ALL tenant databases to this tenant's schema. This is syntax
        sugar for quickly changing tenants at the Django shell or in code.

        In multi-database setups, this ensures all databases with the
        django-tenants engine are switched to this tenant's schema atomically.

        Usage:
            Tenant.objects.get(schema_name='test').activate()
        """
        for db_alias in get_all_tenant_databases():
            connection = connections[db_alias]
            connection.set_tenant(self)

    @classmethod
    def deactivate(cls):
        """
        Deactivate tenants on all tenant databases, returning to public schema.

        Switches ALL tenant databases back to the public schema. This is syntax
        sugar for returning to the public schema at the Django shell or in code.

        In multi-database setups, this ensures all databases with the
        django-tenants engine are switched to the public schema atomically.

        Usage:
            test_tenant.deactivate()
            # or simpler
            Tenant.deactivate()
        """
        for db_alias in get_all_tenant_databases():
            connection = connections[db_alias]
            connection.set_schema_to_public()

    def save(self, verbosity=1, *args, **kwargs):
        is_new = self._state.adding

        # Validate all tenant databases have correct schema
        for db_alias in get_all_tenant_databases():
            connection = connections[db_alias]
            has_schema = hasattr(connection, 'schema_name')

            if has_schema and is_new and connection.schema_name != get_public_schema_name():
                raise Exception(f"Can't create tenant outside the public schema. "
                              f"Current schema is {connection.schema_name} on database '{db_alias}'.")
            elif has_schema and not is_new and connection.schema_name not in (self.schema_name, get_public_schema_name()):
                raise Exception(f"Can't update tenant outside it's own schema or "
                              f"the public schema. Current schema is {connection.schema_name} "
                              f"on database '{db_alias}'.")

        super().save(*args, **kwargs)

        # All tenant databases have schema support (checked above)
        # Note: has_schema is True for all databases returned by get_all_tenant_databases()
        has_schema = len(get_all_tenant_databases()) > 0
        if has_schema and is_new and self.auto_create_schema:
            try:
                self.create_schema(check_if_exists=True, verbosity=verbosity)
                post_schema_sync.send(sender=TenantMixin, tenant=self.serializable_fields())
            except Exception:
                # We failed creating the tenant, delete what we created and
                # re-raise the exception
                self.delete(force_drop=True)
                raise
        elif is_new:
            # although we are not using the schema functions directly, the signal might be registered by a listener
            schema_needs_to_be_sync.send(sender=TenantMixin, tenant=self.serializable_fields())
        elif not is_new and self.auto_create_schema:
            # Check if schema is missing from any database (multi-database support)
            schema_missing = any(
                not schema_exists(self.schema_name, database=db_alias)
                for db_alias in get_all_tenant_databases()
            )
            if schema_missing:
                # Create schemas for existing models on databases where missing
                try:
                    self.create_schema(check_if_exists=True, verbosity=verbosity)
                    post_schema_sync.send(sender=TenantMixin, tenant=self.serializable_fields())
                except Exception:
                    # We failed creating the schema, delete what we created and
                    # re-raise the exception
                    self._drop_schema()
                    raise

    def serializable_fields(self):
        """ in certain cases the user model isn't serializable so you may want to only send the id """
        return self

    def _drop_schema(self, force_drop=False):
        """ Drops the schema from all tenant databases"""
        # Track unique physical databases to avoid duplicate operations
        # (prevents self-deadlock when multiple aliases point to same database)
        processed_databases = set()

        # Check if we should drop (based on first accessible database)
        should_drop = False

        for db_alias in get_all_tenant_databases():
            connection = connections[db_alias]
            db_key = _get_database_key(connection)

            # Skip if we've already processed this physical database
            if db_key in processed_databases:
                continue
            processed_databases.add(db_key)

            has_schema = hasattr(connection, 'schema_name')
            if has_schema and schema_exists(self.schema_name, database=db_alias) and (self.auto_drop_schema or force_drop):
                should_drop = True
                break

        if should_drop:
            # Call pre_drop hook once before dropping from any database
            self.pre_drop()

            # Drop schema from all tenant databases
            processed_databases.clear()  # Reset for drop loop

            for db_alias in get_all_tenant_databases():
                connection = connections[db_alias]
                db_key = _get_database_key(connection)

                # Skip if we've already processed this physical database
                if db_key in processed_databases:
                    continue
                processed_databases.add(db_key)

                if schema_exists(self.schema_name, database=db_alias):
                    # Drop the schema. PostgreSQL allows dropping from any schema context.
                    # Use on_commit to defer DROP execution until after the model delete() completes.
                    # Even in autocommit mode (TransactionTestCase), this defers execution enough
                    # to avoid "pending trigger events" errors.
                    # Use default parameter to capture current db_alias value (avoid closure bug)
                    def drop_schema(captured_db_alias=db_alias):
                        # Re-check schema exists since transaction might have been rolled back
                        if schema_exists(self.schema_name, database=captured_db_alias):
                            cursor = connections[captured_db_alias].cursor()
                            cursor.execute('DROP SCHEMA "%s" CASCADE' % self.schema_name)

                    from django.db import transaction
                    transaction.on_commit(drop_schema, using=db_alias)

    def pre_drop(self):
        """
        This is a routine which you could override to backup the tenant schema before dropping.
        :return:
        """

    def delete(self, force_drop=False, *args, **kwargs):
        """
        Deletes this row. Drops the tenant's schema if the attribute
        auto_drop_schema set to True.
        """
        self._drop_schema(force_drop)
        return super().delete(*args, **kwargs)

    def create_schema(self, check_if_exists=False, sync_schema=True,
                      verbosity=1):
        """
        Creates the schema 'schema_name' for this tenant. Optionally checks if
        the schema already exists before creating it. Returns true if the
        schema was created, false otherwise.

        In multi-database setups, this will create the schema on all databases
        where it doesn't exist. The check_if_exists parameter is handled
        per-database in the creation loop.
        """

        _check_schema_name(self.schema_name)

        # Note: We don't do an early return check here anymore. In multi-database
        # setups, we need to check each database individually (done in the loop below)
        # to create the schema only on databases where it doesn't exist.

        fake_migrations = get_creation_fakes_migrations()

        if sync_schema:
            if fake_migrations:
                # copy tables and data from provided model schema
                # Note: CloneSchema currently only works with the default database
                # For multi-database support, this path needs further work
                connection = connections[get_primary_tenant_database()]
                base_schema = get_tenant_base_schema()
                clone_schema = CloneSchema()
                clone_schema.clone_schema(
                    base_schema, self.schema_name, self.clone_mode
                )

                call_command('migrate_schemas',
                             tenant=True,
                             fake=True,
                             schema_name=self.schema_name,
                             interactive=False,
                             verbosity=verbosity)

                connection.set_schema_to_public()
            else:
                # Create the schema on all tenant databases
                # Track unique physical databases to avoid duplicate operations
                # (prevents self-deadlock when multiple aliases point to same database)
                processed_databases = set()

                for db_alias in get_all_tenant_databases():
                    connection = connections[db_alias]
                    db_key = _get_database_key(connection)

                    # Skip if we've already processed this physical database
                    if db_key in processed_databases:
                        continue
                    processed_databases.add(db_key)

                    # Check if schema exists on this specific database
                    if not schema_exists(self.schema_name, database=db_alias):
                        cursor = connection.cursor()
                        cursor.execute('CREATE SCHEMA "%s"' % self.schema_name)

                # Run migrations (router will direct to appropriate databases)
                call_command('migrate_schemas',
                             tenant=True,
                             schema_name=self.schema_name,
                             interactive=False,
                             verbosity=verbosity)

                # Set all databases back to public schema
                for db_alias in get_all_tenant_databases():
                    connections[db_alias].set_schema_to_public()

    def get_primary_domain(self):
        """
        Returns the primary domain of the tenant
        """
        try:
            domain = self.domains.get(is_primary=True)
            return domain
        except get_tenant_domain_model().DoesNotExist:
            return None

    def reverse(self, request, view_name):
        """
        Returns the URL of this tenant.
        """
        http_type = 'https://' if request.is_secure() else 'http://'

        domain = get_current_site(request).domain

        url = ''.join((http_type, self.schema_name, '.', domain, reverse(view_name)))

        return url

    def get_tenant_type(self):
        """
        Get the type of tenant. Will only work for multi type tenants
        :return: str
        """
        return getattr(self, settings.MULTI_TYPE_DATABASE_FIELD)


class DomainMixin(models.Model):
    """
    All models that store the domains must inherit this class
    """
    domain = models.CharField(max_length=253, unique=True, db_index=True)
    tenant = models.ForeignKey(settings.TENANT_MODEL, db_index=True, related_name='domains',
                               on_delete=models.CASCADE)

    # Set this to true if this is the primary domain
    is_primary = models.BooleanField(default=True, db_index=True)

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Get all other primary domains with the same tenant
        domain_list = self.__class__.objects.filter(tenant=self.tenant, is_primary=True).exclude(pk=self.pk)
        # If we have no primary domain yet, set as primary domain by default
        self.is_primary = self.is_primary or (not domain_list.exists())
        if self.is_primary:
            # Remove primary status of existing domains for tenant
            domain_list.update(is_primary=False)
        super().save(*args, **kwargs)

    class Meta:
        abstract = True

    def __str__(self):
        return self.domain
