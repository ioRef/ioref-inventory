from django.db import migrations

# Matches the five values already in use as `category:` in ioref-web's part
# front matter, so a group assigned Power here lines up with existing content
# rather than introducing a second spelling for the same category.
CATEGORIES = [
    ("Input", "input"),
    ("Output", "output"),
    ("Power", "power"),
    ("Connector", "connector"),
    ("Controller", "controller"),
]


def seed(apps, schema_editor):
    Category = apps.get_model("inventory", "Category")
    for name, slug in CATEGORIES:
        Category.objects.get_or_create(slug=slug, defaults={"name": name})


def unseed(apps, schema_editor):
    Category = apps.get_model("inventory", "Category")
    Category.objects.filter(slug__in=[slug for _, slug in CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0005_category_group_category"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
