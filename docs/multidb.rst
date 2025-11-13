==========================================
Multi-Database Support
==========================================

Overview
========

As of version X.X, django-tenants supports distributing tenant schemas across multiple PostgreSQL databases. This enables advanced deployment patterns including:

* **Read replicas** for improved read performance
* **Database sharding** to distribute tenants across multiple servers
* **Application-level routing** with Django's database routers
* **Geographic distribution** for data locality requirements

Architecture
============

How Multi-Database Works
-------------------------

When multi-database support is enabled, django-tenants automatically:

1. **Detects all tenant databases** by scanning ``settings.DATABASES`` for databases using the ``django_tenants.postgresql_backend`` engine
2. **Creates tenant schemas on all databases** when a new tenant is created
3. **Activates tenant schemas on all databases** when a tenant becomes active (via middleware or manual activation)
4. **Runs migrations on all databases** when you execute ``migrate_schemas``

The public schema containing tenant metadata (the ``Tenant`` and ``Domain`` models) remains on the default database.

Key Concepts
------------

**Primary Database (``default``)**
    The database containing the public schema with tenant metadata. This is where your ``Tenant`` and ``Domain`` models live.

**Tenant Databases**
    All databases using ``django_tenants.postgresql_backend`` that will host tenant schemas. This includes the primary database, read replicas, and shard databases.

**Database Routers**
    Django's database routing system determines which database serves each query. Use routers to implement sharding, read/write splitting, and other patterns.

Configuration
=============

Basic Multi-Database Setup
---------------------------

To enable multi-database support, configure multiple databases with the django-tenants engine:

.. code-block:: python

    # settings.py
    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'main_db',
            'USER': 'postgres',
            'PASSWORD': 'password',
            'HOST': 'localhost',
            'PORT': '5432',
        },
        'replica': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'main_db',  # Same database, different connection
            'USER': 'postgres',
            'PASSWORD': 'password',
            'HOST': 'replica.example.com',  # Read replica host
            'PORT': '5432',
            # Mark as read replica for testing
            'TEST': {
                'MIRROR': 'default',  # In tests, mirror the default database
            },
        },
    }

That's it! django-tenants will automatically:

* Create tenant schemas on both databases when tenants are created
* Activate tenant schemas on both databases when tenants are activated
* Run migrations on both databases

Read Replica Configuration
---------------------------

For read replicas, configure a separate database entry pointing to your replica server:

.. code-block:: python

    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'primary_db',
            'HOST': 'primary.example.com',
        },
        'read_replica': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'primary_db',  # Same database name
            'HOST': 'replica.example.com',  # Different host
            'TEST': {
                'MIRROR': 'default',
            },
        },
    }

Use Django's database routers to direct read queries to the replica:

.. code-block:: python

    class ReadReplicaRouter:
        """Route read queries to replica."""

        def db_for_read(self, model, **hints):
            """Send reads to replica."""
            return 'read_replica'

        def db_for_write(self, model, **hints):
            """Send writes to primary."""
            return 'default'

    # Add to settings.py
    DATABASE_ROUTERS = ['myapp.routers.ReadReplicaRouter']

Database Sharding Configuration
--------------------------------

For sharding tenants across multiple databases:

.. code-block:: python

    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'tenants_db',
            'HOST': 'localhost',
        },
        'shard_1': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'shard_1_db',
            'HOST': 'shard1.example.com',
        },
        'shard_2': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'shard_2_db',
            'HOST': 'shard2.example.com',
        },
    }

Implement a router to distribute tenants:

.. code-block:: python

    class ShardRouter:
        """Route tenants to shards by ID."""

        def db_for_read(self, model, **hints):
            # Public schema models stay on default
            if model._meta.app_label in settings.SHARED_APPS:
                return 'default'

            # Route based on current tenant
            from django.db import connection
            if hasattr(connection, 'tenant'):
                tenant = connection.tenant
                if tenant:
                    # Hash tenant ID to determine shard
                    shard_num = (tenant.id % 2) + 1
                    return f'shard_{shard_num}'

            return 'default'

        def db_for_write(self, model, **hints):
            return self.db_for_read(model, **hints)

Usage
=====

Working with Tenants
--------------------

Tenant activation automatically switches all tenant databases:

.. code-block:: python

    from django_tenants.utils import get_tenant_model

    # Get a tenant
    Tenant = get_tenant_model()
    tenant = Tenant.objects.get(schema_name='acme')

    # Activate on ALL databases
    tenant.activate()

    # All queries now use acme's schema on all databases
    MyModel.objects.all()  # Queries acme schema

    # Deactivate (return to public schema on all databases)
    Tenant.deactivate()

Context Managers
----------------

Both ``tenant_context()`` and ``schema_context()`` operate on all tenant databases:

.. code-block:: python

    from django_tenants.utils import tenant_context, schema_context

    # Using tenant object
    with tenant_context(tenant):
        # All databases switched to tenant's schema
        MyModel.objects.all()
    # All databases restored to previous schema

    # Using schema name directly
    with schema_context('acme'):
        # All databases switched to 'acme' schema
        MyModel.objects.all()

.. note::

    The ``database`` parameter for ``tenant_context()`` and ``schema_context()`` is **deprecated**.
    These context managers now operate on all tenant databases for consistency and safety.

Database Routers
----------------

Use Django's database routing to control query distribution:

.. code-block:: python

    # myapp/routers.py
    class TenantRouter:
        """Route tenant app queries."""

        def db_for_read(self, model, **hints):
            """Route reads based on app."""
            if model._meta.app_label == 'heavy_analytics':
                return 'analytics_db'
            return None  # Use default routing

        def db_for_write(self, model, **hints):
            """Route writes."""
            if model._meta.app_label == 'heavy_analytics':
                return 'analytics_db'
            return None

    # settings.py
    DATABASE_ROUTERS = ['myapp.routers.TenantRouter']

Migrations
==========

Running Migrations
------------------

The ``migrate_schemas`` command automatically runs migrations on all tenant databases:

.. code-block:: bash

    # Migrate public schema on default database
    python manage.py migrate_schemas --schema=public

    # Migrate all tenant schemas on ALL databases
    python manage.py migrate_schemas

    # Migrate specific tenant on ALL databases
    python manage.py migrate_schemas --schema=acme

Migration Executors
-------------------

django-tenants supports two migration executors:

**Standard Executor** (default)
    Runs migrations serially in the current process. Suitable for most deployments.

**Multiprocessing Executor**
    Runs migrations in parallel using multiple processes. Faster for many tenants but requires more resources.

.. code-block:: python

    # settings.py
    TENANT_MIGRATION_EXECUTOR = 'django_tenants.migration_executors.StandardExecutor'
    # or
    TENANT_MIGRATION_EXECUTOR = 'django_tenants.migration_executors.MultiprocessingExecutor'

Testing
=======

Test Database Configuration
----------------------------

Django's test framework automatically creates test databases for all configured databases. For read replicas, use the ``MIRROR`` setting to avoid duplicating the same database:

.. code-block:: python

    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'main_db',
        },
        'replica': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'main_db',
            'TEST': {
                'MIRROR': 'default',  # Don't create separate test database
            },
        },
    }

Writing Tests
-------------

Specify which databases your test needs access to:

.. code-block:: python

    from django.test import TestCase

    class MyTest(TestCase):
        # Allow test to access all databases
        databases = '__all__'

        def test_multi_database_tenant(self):
            tenant = Tenant.objects.create(schema_name='test')

            # Tenant schema created on all databases
            from django_tenants.utils import schema_exists
            self.assertTrue(schema_exists('test', database='default'))
            self.assertTrue(schema_exists('test', database='replica'))

For single-database tests (better isolation):

.. code-block:: python

    class SingleDatabaseTest(TestCase):
        # Only access default database
        databases = {'default'}

        def test_something(self):
            # This test won't create schemas on other databases
            pass

Performance Considerations
==========================

Connection Pooling
------------------

Multiple databases increase connection usage. Configure connection pooling appropriately:

.. code-block:: python

    # settings.py
    DATABASES = {
        'default': {
            # ... connection settings ...
            'CONN_MAX_AGE': 600,  # Persistent connections
            'OPTIONS': {
                'connect_timeout': 10,
                'options': '-c statement_timeout=30000',
            },
        },
    }

Query Optimization
------------------

**Use database routers** to minimize cross-database queries:

.. code-block:: python

    # Good: Query stays on single database
    with tenant_context(tenant):
        items = MyModel.objects.filter(status='active')

    # Avoid: Don't manually switch between databases mid-operation
    # This can cause unexpected behavior

**Avoid N+1 queries** across databases:

.. code-block:: python

    # Good: Fetch related data in one query
    items = MyModel.objects.select_related('related').all()

    # Bad: N+1 queries
    items = MyModel.objects.all()
    for item in items:
        item.related  # Separate query for each

Caching
-------

Consider caching tenant metadata to avoid repeatedly querying the public schema:

.. code-block:: python

    from django.core.cache import cache

    def get_tenant_by_domain(domain):
        cache_key = f'tenant_domain_{domain}'
        tenant = cache.get(cache_key)
        if tenant is None:
            tenant = Tenant.objects.get(domains__domain=domain)
            cache.set(cache_key, tenant, timeout=3600)
        return tenant

Troubleshooting
===============

Common Issues
-------------

**Tenant schema not found on some databases**
    If a tenant schema exists on ``default`` but not on other databases:

    1. Check that the database uses ``django_tenants.postgresql_backend``
    2. Run ``migrate_schemas`` to create missing schemas
    3. Verify database connectivity and permissions

**Migrations fail on replica database**
    Read replicas should not be writable. Ensure your database router prevents writes to replicas:

    .. code-block:: python

        def db_for_write(self, model, **hints):
            # Never write to replica
            if 'replica' in hints.get('database', ''):
                return 'default'
            return None

**Tests fail with "Connection '_' doesn't exist"**
    Ensure test classes call ``super().setUpClass()``:

    .. code-block:: python

        @classmethod
        def setUpClass(cls):
            super().setUpClass()  # Essential!
            # ... your setup code ...

**Schema search_path not set correctly**
    Verify all tenant databases are activated:

    .. code-block:: python

        from django_tenants.utils import get_all_tenant_databases
        from django.db import connections

        tenant.activate()
        for db_alias in get_all_tenant_databases():
            conn = connections[db_alias]
            print(f"{db_alias}: {conn.schema_name}")

Migration from Single Database
===============================

If you're adding multi-database support to an existing single-database deployment:

Step 1: Configure Additional Databases
---------------------------------------

Add new database entries to ``settings.DATABASES`` using the django-tenants engine.

Step 2: Create Schemas on New Databases
----------------------------------------

For each new database, create the public schema:

.. code-block:: bash

    # Connect to new database and create public schema
    psql -h new_db_host -U postgres -d new_db_name
    CREATE SCHEMA IF NOT EXISTS public;

Step 3: Run Migrations
----------------------

Migrate the public schema and all tenant schemas to the new databases:

.. code-block:: bash

    # Migrate public schema
    python manage.py migrate_schemas --schema=public

    # Migrate all tenants
    python manage.py migrate_schemas

Step 4: Verify Schemas
----------------------

Check that all tenant schemas exist on all databases:

.. code-block:: python

    from django_tenants.utils import get_tenant_model, get_all_tenant_databases, schema_exists

    Tenant = get_tenant_model()
    for tenant in Tenant.objects.all():
        for db_alias in get_all_tenant_databases():
            exists = schema_exists(tenant.schema_name, database=db_alias)
            print(f"{tenant.schema_name} on {db_alias}: {exists}")

Best Practices
==============

1. **Use database routers** to explicitly control query routing rather than relying on defaults
2. **Monitor connection counts** to ensure you don't exhaust connection pools
3. **Test with multiple databases** even if you start with one - it's easier to add later
4. **Use TEST.MIRROR** for read replicas in tests to avoid duplicating test databases
5. **Document your sharding strategy** if using multiple distinct databases
6. **Cache tenant lookups** to avoid repeatedly hitting the public schema
7. **Use connection pooling** (PgBouncer, Django CONN_MAX_AGE) to manage connections efficiently
8. **Plan for schema naming** - ensure schema names are valid PostgreSQL identifiers

API Reference
=============

Utility Functions
-----------------

``get_all_tenant_databases()``
    Returns a list of all database aliases that host tenant schemas (i.e., all databases using the django-tenants engine).

    .. code-block:: python

        from django_tenants.utils import get_all_tenant_databases

        tenant_dbs = get_all_tenant_databases()
        # ['default', 'replica', 'shard_1', 'shard_2']

``get_primary_tenant_database()``
    Returns the primary database alias for tenant operations (defaults to 'default').

    .. code-block:: python

        from django_tenants.utils import get_primary_tenant_database

        primary_db = get_primary_tenant_database()
        # 'default'

``schema_exists(schema_name, database=...)``
    Check if a schema exists on a specific database.

    .. code-block:: python

        from django_tenants.utils import schema_exists

        if schema_exists('acme', database='default'):
            print("Schema exists on default database")

Tenant Methods
--------------

``tenant.activate()``
    Activate the tenant on **all** tenant databases.

    .. code-block:: python

        tenant.activate()
        # All databases now use tenant's schema

``Tenant.deactivate()``
    Deactivate tenants on **all** databases (return to public schema).

    .. code-block:: python

        from django_tenants.utils import get_tenant_model

        Tenant = get_tenant_model()
        Tenant.deactivate()
        # All databases now use public schema

Context Managers
----------------

``with tenant_context(tenant)``
    Context manager that switches **all** tenant databases to the tenant's schema.

    .. code-block:: python

        with tenant_context(tenant):
            # All databases on tenant schema
            MyModel.objects.all()
        # All databases restored

``with schema_context(schema_name)``
    Context manager that switches **all** tenant databases to the specified schema.

    .. code-block:: python

        with schema_context('acme'):
            # All databases on 'acme' schema
            MyModel.objects.all()
        # All databases restored

.. note::

    Both context managers now operate on **all** tenant databases. The ``database`` parameter is deprecated and ignored.

See Also
========

* :doc:`install` - Installation guide
* :doc:`use` - Basic usage guide
* :doc:`test` - Testing guide
* Django's `Multi-Database Support <https://docs.djangoproject.com/en/stable/topics/db/multi-db/>`_
* PostgreSQL `Schemas Documentation <https://www.postgresql.org/docs/current/ddl-schemas.html>`_
