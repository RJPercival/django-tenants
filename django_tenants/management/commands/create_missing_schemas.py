from django.core.management.base import BaseCommand

from django_tenants.utils import get_tenant_model, schema_exists, get_all_tenant_databases


class Command(BaseCommand):
    help = 'Create missing tenants'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        tenants = get_tenant_model().objects.all()
        for tenant in tenants:
            # Check if schema is missing from any database (multi-database support)
            schema_missing = any(
                not schema_exists(schema_name=tenant.schema_name, database=db_alias)
                for db_alias in get_all_tenant_databases()
            )
            if schema_missing:
                self.stdout.write(self.style.NOTICE("Missing '%s' schema lets create it" % tenant.schema_name))
                tenant.create_schema()

        self.stdout.write("Done")
