"""
WebTest integration for django-tenants.

This module provides a wrapper for django-webtest's DjangoTestApp that
automatically sets the tenant context for all requests.
"""


class TenantDjangoTestApp:
    """
    Wrapper around django-webtest's DjangoTestApp that adds tenant context.

    This class wraps an existing DjangoTestApp instance and ensures that all
    requests made through it include the proper HTTP_HOST header and tenant
    context for the specified tenant.

    Usage:
        from django_webtest import DjangoTestApp
        from django_tenants.test.webtest import TenantDjangoTestApp

        app = DjangoTestApp()
        tenant_app = TenantDjangoTestApp(app, tenant)
        response = tenant_app.get('/path/')

    Args:
        app: An instance of DjangoTestApp (or any custom subclass)
        tenant: The tenant instance to use for requests
    """

    def __init__(self, app, tenant):
        """
        Initialize the wrapper with an app instance and tenant.

        Args:
            app: DjangoTestApp instance to wrap
            tenant: Tenant instance for request context
        """
        self._app = app
        self.tenant = tenant
        # Domain is retrieved dynamically rather than cached to handle
        # dynamic domain changes during tests

    def _add_tenant_context(self, kwargs):
        """
        Add tenant context to request kwargs.

        Sets the HTTP_HOST header to the tenant's primary domain if not
        already specified in the kwargs. The domain is retrieved dynamically
        from the tenant to handle any domain changes during tests.

        Args:
            kwargs: Dictionary of request parameters

        Returns:
            Modified kwargs dictionary with tenant context
        """
        # Add HTTP_HOST if not already present
        if "headers" not in kwargs:
            kwargs["headers"] = {}

        if "Host" not in kwargs["headers"] and "HTTP_HOST" not in kwargs["headers"]:
            kwargs["headers"]["Host"] = self.tenant.get_primary_domain().domain

        return kwargs

    def get(self, *args, **kwargs):
        """Make a GET request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        """Make a POST request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.post(*args, **kwargs)

    def put(self, *args, **kwargs):
        """Make a PUT request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.put(*args, **kwargs)

    def patch(self, *args, **kwargs):
        """Make a PATCH request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.patch(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Make a DELETE request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.delete(*args, **kwargs)

    def head(self, *args, **kwargs):
        """Make a HEAD request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.head(*args, **kwargs)

    def options(self, *args, **kwargs):
        """Make an OPTIONS request with tenant context."""
        kwargs = self._add_tenant_context(kwargs)
        return self._app.options(*args, **kwargs)

    def __getattr__(self, name):
        """
        Delegate all other attribute access to the wrapped app.

        This allows the wrapper to be used as a drop-in replacement for
        DjangoTestApp, providing access to all other methods and attributes.
        """
        return getattr(self._app, name)
