import os
from contextlib import ContextDecorator
from functools import lru_cache, wraps
from types import ModuleType

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.utils.module_loading import import_string

try:
    from django.apps import apps
    get_model = apps.get_model
except ImportError:
    from django.db.models.loading import get_model

from django.core import mail


def get_tenant_model():
    return get_model(settings.TENANT_MODEL)


def get_tenant_domain_model():
    return get_model(settings.TENANT_DOMAIN_MODEL)


def get_primary_tenant_database():
    """
    Returns the primary database alias for tenant operations.

    This is the database containing the public schema with tenant metadata
    (Tenant and Domain models). Typically this is 'default' unless overridden
    by the TENANT_DB_ALIAS setting.

    Returns:
        str: Database alias string for the primary tenant database

    Example:
        >>> from django_tenants.utils import get_primary_tenant_database
        >>> primary_db = get_primary_tenant_database()
        >>> print(primary_db)
        'default'
    """
    return getattr(settings, 'TENANT_DB_ALIAS', DEFAULT_DB_ALIAS)


@lru_cache(maxsize=1)
def get_all_tenant_databases():
    """
    Returns all database aliases that host tenant schemas.

    Scans settings.DATABASES for all databases using the django-tenants engine
    (django_tenants.postgresql_backend). This enables multi-database support
    where tenant schemas can exist across multiple databases.

    The returned list includes:
    - The primary database (containing public schema with tenant metadata)
    - Read replicas (databases mirroring the primary)
    - Shard databases (separate databases for distributing tenants)

    Returns:
        list: A list of database alias strings for databases using django-tenants engine

    Example:
        >>> from django_tenants.utils import get_all_tenant_databases
        >>> tenant_dbs = get_all_tenant_databases()
        >>> print(tenant_dbs)
        ['default', 'replica', 'shard_1', 'shard_2']

    Note:
        This function is cached using @lru_cache for performance. If you
        modify settings.DATABASES at runtime, clear the cache using:
        get_all_tenant_databases.cache_clear()
    """
    from django.conf import settings

    tenant_databases = []

    for alias, db_config in settings.DATABASES.items():
        engine = db_config.get('ENGINE', '')
        if engine == 'django_tenants.postgresql_backend':
            tenant_databases.append(alias)

    return tenant_databases


# Deprecated aliases - kept for backwards compatibility
def get_tenant_database_alias():
    """
    .. deprecated:: X.X
        Use :func:`get_primary_tenant_database` instead.
        This function will be removed in a future version.
    """
    import warnings
    warnings.warn(
        "get_tenant_database_alias() is deprecated, use get_primary_tenant_database() instead",
        DeprecationWarning,
        stacklevel=2
    )
    return get_primary_tenant_database()


def get_tenant_database_aliases():
    """
    .. deprecated:: X.X
        Use :func:`get_all_tenant_databases` instead.
        This function will be removed in a future version.
    """
    import warnings
    warnings.warn(
        "get_tenant_database_aliases() is deprecated, use get_all_tenant_databases() instead",
        DeprecationWarning,
        stacklevel=2
    )
    return get_all_tenant_databases()


def get_public_schema_name():
    return getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')


def get_tenant_types():
    return getattr(settings, 'TENANT_TYPES', {})


def get_tenant_base_migrate_command_class():
    class_path = getattr(
        settings,
        'TENANT_BASE_MIGRATE_COMMAND',
        'django.core.management.commands.migrate.Command',
    )
    return import_string(class_path)


def has_multi_type_tenants():
    return getattr(settings, 'HAS_MULTI_TYPE_TENANTS', False)


def get_multi_type_database_field_name():
    return getattr(settings, 'MULTI_TYPE_DATABASE_FIELD', '')


def get_public_schema_urlconf():
    if has_multi_type_tenants():
        return get_tenant_types()[get_public_schema_name()]['URLCONF']
    else:
        return getattr(settings, 'PUBLIC_SCHEMA_URLCONF', 'urls_public')


def get_tenant_type_choices():
    """This is to allow a choice field for the type of tenant"""
    if not has_multi_type_tenants():
        assert False, 'get_tenant_type_choices should only be used for multi type tenants'

    tenant_types = get_tenant_types()

    return [(k, k) for k in tenant_types.keys()]


def get_limit_set_calls():
    return getattr(settings, 'TENANT_LIMIT_SET_CALLS', False)


def get_subfolder_prefix():
    subfolder_prefix = getattr(settings, 'TENANT_SUBFOLDER_PREFIX', '') or ''
    return subfolder_prefix.strip('/ ')


def get_creation_fakes_migrations():
    """
    If TENANT_CREATION_FAKES_MIGRATIONS, tenants will be created by cloning an
    existing schema specified by TENANT_CLONE_BASE.
    """
    faked = getattr(settings, 'TENANT_CREATION_FAKES_MIGRATIONS', False)
    if faked:
        if not getattr(settings, 'TENANT_BASE_SCHEMA', False):
            raise ImproperlyConfigured(
                'You must specify a schema name in TENANT_BASE_SCHEMA if '
                'TENANT_CREATION_FAKES_MIGRATIONS is enabled.'
            )
    return faked


def get_tenant_base_schema():
    """
    If TENANT_CREATION_FAKES_MIGRATIONS, tenants will be created by cloning an
    existing schema specified by TENANT_CLONE_BASE.
    """
    schema = getattr(settings, 'TENANT_BASE_SCHEMA', False)
    if schema:
        if not getattr(settings, 'TENANT_CREATION_FAKES_MIGRATIONS', False):
            raise ImproperlyConfigured(
                'TENANT_CREATION_FAKES_MIGRATIONS setting must be True to use '
                'TENANT_BASE_SCHEMA for cloning.'
            )
    return schema


def get_tenant_migration_order():
    return getattr(settings, 'TENANT_MIGRATION_ORDER', None)


def _restore_tenant_state_on_all_databases(previous_tenant_dict):
    """
    Restore previous tenant state for all databases.

    This helper is used by both TenantMixin and schema_context to restore
    the schema state when exiting a context manager. It implements the
    common restoration logic to avoid duplication.

    Args:
        previous_tenant_dict: Dict of {db_alias: [previous_tenant_stack]}
                              where each database alias maps to a list
                              (stack) of previous tenant objects
    """
    for db_alias in get_all_tenant_databases():
        if db_alias in previous_tenant_dict and previous_tenant_dict[db_alias]:
            previous = previous_tenant_dict[db_alias].pop()
            conn = connections[db_alias]
            if previous is None:
                conn.set_schema_to_public()
            else:
                conn.set_tenant(previous)


class schema_context(ContextDecorator):
    """
    Context manager for switching to a schema across all tenant databases.

    Switches ALL databases with the django-tenants engine to the specified schema.
    When the context exits, restores the previous schema on all databases.
    Supports nesting - each exit restores the schema that was active when that
    context was entered.

    Usage:
        with schema_context('tenant_schema'):
            # All tenant databases are now on tenant_schema
            Model.objects.all()  # Queries tenant_schema on appropriate database
        # All databases restored to previous schema

    Args:
        schema_name: Name of the schema to switch to
        database: DEPRECATED. This parameter is ignored. All tenant databases
                  are always switched for consistency and safety.

    Note: Please do not try and merge this with tenant_context as they are
    not the same, as pointed out in #501.
    """
    def __init__(self, *args, **kwargs):
        self.schema_name = args[0]
        self._previous_tenant = {}  # Dict of {db_alias: [previous_tenant, ...]}

        # Deprecation warning for database parameter
        if 'database' in kwargs:
            import warnings
            warnings.warn(
                "The 'database' parameter for schema_context() is deprecated. "
                "schema_context() now operates on all tenant databases for consistency. "
                "This parameter is ignored.",
                DeprecationWarning,
                stacklevel=2
            )
        super().__init__()

    def __enter__(self):
        # Save previous tenant for each database and switch to new schema
        for db_alias in get_all_tenant_databases():
            if db_alias not in self._previous_tenant:
                self._previous_tenant[db_alias] = []
            conn = connections[db_alias]
            self._previous_tenant[db_alias].append(conn.tenant)
            conn.set_schema(self.schema_name)

    def __exit__(self, *exc):
        # Restore previous tenant for each database using shared helper
        _restore_tenant_state_on_all_databases(self._previous_tenant)


class tenant_context(ContextDecorator):
    """
    Context manager for switching to a tenant across all tenant databases.

    This is a convenience wrapper around the tenant's built-in context manager.
    Switches ALL databases with the django-tenants engine to the specified tenant.
    When the context exits, restores the previous tenant on all databases.
    Supports nesting - each exit restores the tenant that was active when that
    context was entered.

    Usage:
        with tenant_context(tenant):
            # All tenant databases are now on tenant's schema
            Model.objects.all()  # Queries tenant's schema on appropriate database
        # All databases restored to previous tenant/schema

    Args:
        tenant: The tenant object to switch to

    Note: Please do not try and merge this with schema_context as they are not
    the same, as pointed out in #501. While they both switch schemas, tenant_context
    works with tenant objects and schema_context works with schema names directly.
    """
    def __init__(self, *args, **kwargs):
        self.tenant = args[0]

        # Deprecation warning for database parameter
        if 'database' in kwargs:
            import warnings
            warnings.warn(
                "The 'database' parameter for tenant_context() is deprecated. "
                "tenant_context() now operates on all tenant databases for consistency. "
                "This parameter is ignored.",
                DeprecationWarning,
                stacklevel=2
            )
        super().__init__()

    def __enter__(self):
        # Delegate to the tenant's context manager
        return self.tenant.__enter__()

    def __exit__(self, *exc):
        # Delegate to the tenant's context manager
        return self.tenant.__exit__(*exc)


def clean_tenant_url(url_string):
    """
    Removes the TENANT_TOKEN from a particular string
    """
    if hasattr(settings, 'PUBLIC_SCHEMA_URLCONF'):
        if (settings.PUBLIC_SCHEMA_URLCONF and
                url_string.startswith(settings.PUBLIC_SCHEMA_URLCONF)):
            url_string = url_string[len(settings.PUBLIC_SCHEMA_URLCONF):]
    return url_string


def remove_www_and_dev(hostname):
    """
    Legacy function - just in case someone is still using the old name
    """
    return remove_www(hostname)


def remove_www(hostname):
    """
    Removes www. from the beginning of the address. Only for
    routing purposes. www.test.com/login/ and test.com/login/ should
    find the same tenant.
    """
    if hostname.startswith("www."):
        return hostname[4:]

    return hostname


def django_is_in_test_mode():
    """
    I know this is very ugly! I'm looking for more elegant solutions.
    See: http://stackoverflow.com/questions/6957016/detect-django-testing-mode
    """
    return hasattr(mail, 'outbox')


def schema_exists(schema_name: str, database: str = get_primary_tenant_database()) -> bool:
    """
    Check if a schema exists on a specific database.

    This function queries the PostgreSQL system catalog to determine if a schema
    with the given name exists on the specified database. The check is case-insensitive,
    matching PostgreSQL's schema name handling.

    Args:
        schema_name: Name of the schema to check for existence
        database: Database alias to check (defaults to the tenant database from settings)

    Returns:
        True if the schema exists on the specified database, False otherwise

    Example:
        >>> from django_tenants.utils import schema_exists
        >>> # Check on default tenant database
        >>> schema_exists('my_tenant')
        True
        >>> # Check on specific database
        >>> schema_exists('my_tenant', database='replica')
        True
        >>> # Non-existent schema
        >>> schema_exists('nonexistent')
        False
    """
    _connection = connections[database]
    cursor = _connection.cursor()

    # check if this schema already exists in the db
    sql = 'SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE LOWER(nspname) = LOWER(%s))'
    cursor.execute(sql, (schema_name, ))

    row = cursor.fetchone()
    if row:
        exists = row[0]
    else:
        exists = False

    cursor.close()

    return exists


def schema_rename(tenant, new_schema_name, database=get_primary_tenant_database(), save=True):
    """
    This renames a schema to a new name. It checks to see if it exists first.

    Note: This function only renames the schema on the specified database.
    In multi-database setups, you may need to call this for each database.
    """
    from django_tenants.postgresql_backend.base import is_valid_schema_name
    _connection = connections[database]
    cursor = _connection.cursor()

    if schema_exists(new_schema_name, database=database):
        raise ValidationError("New schema name already exists")
    if not is_valid_schema_name(new_schema_name):
        raise ValidationError("Invalid string used for the schema name.")
    sql = 'ALTER SCHEMA {0} RENAME TO {1}'.format(_connection.ops.quote_name(tenant.schema_name),
                                                  _connection.ops.quote_name(new_schema_name))
    cursor.execute(sql)
    cursor.close()
    tenant.schema_name = new_schema_name
    if save:
        tenant.save()


@lru_cache(maxsize=128)
def get_app_label(app):
    from django.apps import apps  # Ensure app registry is imported

    candidate = app.split(".")[-1]

    try:
        imported_app = import_string(app)
    except ImportError:
        return candidate

    app_name = app if isinstance(imported_app, ModuleType) else imported_app.name

    app_label = [
        app_config.label
        for app_config in apps.get_app_configs()
        if app_config.name == app_name
    ]

    if len(app_label) != 1:
        return candidate

    return app_label[0]


def app_labels(apps_list):
    """
    Returns a list of app labels of the given apps_list
    """
    return [get_app_label(app) for app in apps_list]


def parse_tenant_config_path(config_path):
    """
    Convenience function for parsing django-tenants' path configuration strings.

    If the string contains '%s', then the current tenant's schema name will be inserted at that location. Otherwise
    the schema name will be appended to the end of the string.

    :param config_path: A configuration path string that optionally contains '%s' to indicate where the tenant
    schema name should be inserted.

    :return: The formatted string containing the schema name
    """
    try:
        # Insert schema name
        return config_path % connection.schema_name
    except (TypeError, ValueError):
        # No %s in string; append schema name at the end
        return os.path.join(config_path, connection.schema_name)


def validate_extra_extensions():
    skip_validation = getattr(settings, 'SKIP_PG_EXTRA_VALIDATION', False)
    extra_extensions = getattr(settings, 'PG_EXTRA_SEARCH_PATHS', [])

    if not skip_validation and extra_extensions:
        if get_public_schema_name() in extra_extensions:
            raise ImproperlyConfigured(
                "%s can not be included on PG_EXTRA_SEARCH_PATHS."
                % get_public_schema_name())

        # make sure no tenant schema is in settings.PG_EXTRA_SEARCH_PATHS

        # first check that the model table is created
        model = get_tenant_model()
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.tables WHERE table_name = %s;',
                [model._meta.db_table]
            )
            if cursor.fetchone():
                invalid_schemas = set(extra_extensions).intersection(
                    model.objects.all().values_list('schema_name', flat=True))
                if invalid_schemas:
                    raise ImproperlyConfigured(
                        "Do not include tenant schemas (%s) on PG_EXTRA_SEARCH_PATHS."
                        % list(invalid_schemas))

        # Make sure the connection used for the check is not reused and doesn't stay idle.
        connection.close()


def tenant_migration(*args, tenant_schema=True, public_schema=False):
    """
    Decorator to control which schemas a data migration will execute on.
    
    :param tenant_schema: If True (default), the data migration will execute on the tenant schema(s).
    :param public_schema: If True, the data migration will execute on the public schema.

    :return: None
    """

    def _tenant_migration(func):
        @wraps(func)
        def wrapper(*_args, **kwargs):
            try:
                _, schema_editor = _args  # noqa
            except Exception as excp:
                raise Exception(f'Decorator requires apps & schema_editor as positional arguments: {excp}')

            if ((tenant_schema and schema_editor.connection.schema_name != get_public_schema_name()) or
                    (public_schema and schema_editor.connection.schema_name == get_public_schema_name())):
                func(*_args, **kwargs)

        return wrapper

    if len(args) == 1 and callable(args[0]):
        return _tenant_migration(args[0])

    return _tenant_migration


def get_tenant(request):
    """This gets the tenant object from the request"""
    if hasattr(request, 'tenant'):
        return request.tenant
    return None
